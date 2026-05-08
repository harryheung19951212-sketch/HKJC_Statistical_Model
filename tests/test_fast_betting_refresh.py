from pathlib import Path

import racing_model.app_server as app_server
from racing_model.app_server import AppState, api_betting, api_race_dashboard
from racing_model.model import RankingModel
from racing_model.odds import refresh_odds
from racing_model.storage import connect, init_db, insert_rows, upsert_race_status


class FakeOddsProvider:
    source_name = "hkjc_graphql"
    active_source = "hkjc_graphql"
    last_error = None

    def fetch_odds(self, conn, race_id: str):
        return [
            {
                "race_id": race_id,
                "horse_id": "H001",
                "timestamp": "2099-01-01T10:00:30+00:00",
                "win_odds": 8.0,
                "place_odds": 3.0,
                "source": self.source_name,
            }
        ]


def add_minimal_race(conn) -> None:
    insert_rows(
        conn,
        "races",
        [
            {
                "race_id": "HK20990101-ST-01",
                "date": "2099-01-01",
                "track": "Sha Tin",
                "course": "Turf",
                "distance_m": 1200,
                "going": "Good",
                "class_rating": "Class 4",
                "prize": 1000000,
                "race_name": "Test",
            }
        ],
    )
    insert_rows(
        conn,
        "runners",
        [
            {
                "race_id": "HK20990101-ST-01",
                "horse_id": "H001",
                "horse_no": 1,
                "horse_name": "Test Horse",
                "horse_name_zh": "測試馬",
                "jockey": "Jockey",
                "trainer": "Trainer",
                "draw": 1,
                "weight_lbs": 120,
                "official_rating": 50,
                "age": 4,
                "sex": "G",
                "running_style": "pace",
            }
        ],
    )
    insert_rows(
        conn,
        "odds_ticks",
        [
            {
                "race_id": "HK20990101-ST-01",
                "horse_id": "H001",
                "timestamp": "2099-01-01T10:00:00+00:00",
                "win_odds": 4.0,
                "place_odds": 2.0,
                "source": "hkjc_graphql",
            }
        ],
    )
    conn.commit()


def prediction() -> dict[str, object]:
    return {
        "horse_id": "H001",
        "horse_no": 1,
        "display_name": "測試馬",
        "display_jockey": "騎師",
        "display_trainer": "練馬師",
        "win_probability": 0.4,
        "latest_win_odds": 4.0,
        "latest_win_odds_source": "hkjc_graphql",
        "top3_probability": 0.7,
        "place_odds": 2.0,
        "place_odds_source": "hkjc_graphql",
    }


def test_race_dashboard_returns_fast_betting_preview(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    with connect(db_path) as conn:
        add_minimal_race(conn)

        dashboard = api_race_dashboard(conn, state, "HK20990101-ST-01", 10000, "standard")

    assert dashboard["betting"]["fast_preview"] is True
    assert dashboard["betting"]["exotics_deferred"] is True
    assert dashboard["betting"]["decisions"]


def test_full_betting_queues_missing_exotic_refresh_without_blocking(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    state.start_exotic_refresh_job = lambda race_id: True  # type: ignore[method-assign]
    state.start_betting_record_job = lambda race, payload, model_path: True  # type: ignore[method-assign]
    state.calibration_gate = lambda conn, model: None  # type: ignore[method-assign]
    with connect(db_path) as conn:
        add_minimal_race(conn)

    payload = api_betting(
            conn,
            RankingModel.new(),
            "HK20990101-ST-01",
            10000,
            "standard",
            state=state,
            model_path=tmp_path / "model.json",
            predictions=[prediction()],
            include_exotics=True,
            record_mode="async",
        )

    assert payload["exotic_refresh"]["status"] == "queued"
    assert payload["exotic_refresh"]["cached_dividends"] == 0
    assert payload["decisions"]
    assert payload["ledger"]["record_mode"] == "async"
    assert all("recommendation_id" in ticket for ticket in payload["tickets"])
    assert all(ticket.get("execution_status") == "confirmed" for ticket in payload["tickets"])


def test_full_betting_reuses_recent_dashboard_predictions(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    state.calibration_gate = lambda conn, model: None  # type: ignore[method-assign]
    with connect(db_path) as conn:
        add_minimal_race(conn)
        dashboard = api_race_dashboard(conn, state, "HK20990101-ST-01", 10000, "standard")
        assert dashboard["predictions"]["predictions"]

        def fail_adaptive_prediction(*args, **kwargs):
            raise AssertionError("recent dashboard predictions should be reused")

        monkeypatch.setattr(app_server, "adaptive_predict_race", fail_adaptive_prediction)

        payload = api_betting(
            conn,
            RankingModel.new(),
            "HK20990101-ST-01",
            10000,
            "standard",
            state=state,
            include_exotics=False,
            record_mode="none",
        )

    assert payload["decisions"]
    assert payload["prediction_policy"]


def test_full_betting_summary_uses_confirmed_ledger_stake(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    state.calibration_gate = lambda conn, model: None  # type: ignore[method-assign]
    with connect(db_path) as conn:
        add_minimal_race(conn)

        payload = api_betting(
            conn,
            RankingModel.new(),
            "HK20990101-ST-01",
            10000,
            "standard",
            state=state,
            model_path=tmp_path / "model.json",
            predictions=[prediction()],
            include_exotics=False,
            record_mode="sync",
        )

    assert payload["placed_summary"]["confirmed"] > 0
    assert payload["placed_summary"]["executed_staked"] > 0
    assert payload["placed_summary"]["display_stake"] == payload["settlement"]["summary"]["staked"]
    assert payload["display_total_recommended_stake"] == payload["settlement"]["summary"]["staked"]
    assert payload["display_max_race_stake"] == max(payload["max_race_stake"], payload["display_total_recommended_stake"])


def test_live_race_odds_refresh_keeps_updating_ticks(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        add_minimal_race(conn)
        upsert_race_status(conn, "HK20990101-ST-01", "live")

        inserted = refresh_odds(conn, "HK20990101-ST-01", FakeOddsProvider())

    assert inserted == 1


def test_full_betting_refreshes_win_place_odds_and_bypasses_cache(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    state.calibration_gate = lambda conn, model: None  # type: ignore[method-assign]
    state.start_betting_record_job = lambda race, payload, model_path: True  # type: ignore[method-assign]
    calls = []

    def fake_refresh_odds(conn, race_id, provider):
        calls.append(race_id)
        return refresh_odds(conn, race_id, FakeOddsProvider())

    monkeypatch.setattr(app_server, "refresh_odds", fake_refresh_odds)
    with connect(db_path) as conn:
        add_minimal_race(conn)
        api_race_dashboard(conn, state, "HK20990101-ST-01", 10000, "standard")

        payload = api_betting(
            conn,
            RankingModel.new(),
            "HK20990101-ST-01",
            10000,
            "standard",
            state=state,
            include_exotics=False,
            refresh_odds_live=True,
            record_mode="none",
        )

    win = next(row for row in payload["decisions"] if row["market"] == "WIN")
    place = next(row for row in payload["decisions"] if row["market"] == "PLACE")
    assert calls == ["HK20990101-ST-01"]
    assert payload["odds_refresh"]["status"] == "refreshed"
    assert win["odds"] == 8.0
    assert place["odds"] == 3.0


def test_full_betting_refresh_updates_existing_ticket_pool_price(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    state.calibration_gate = lambda conn, model: None  # type: ignore[method-assign]

    def fake_refresh_odds(conn, race_id, provider):
        return refresh_odds(conn, race_id, FakeOddsProvider())

    monkeypatch.setattr(app_server, "refresh_odds", fake_refresh_odds)
    with connect(db_path) as conn:
        add_minimal_race(conn)
        first = api_betting(
            conn,
            RankingModel.new(),
            "HK20990101-ST-01",
            10000,
            "standard",
            state=state,
            model_path=tmp_path / "model.json",
            predictions=[prediction()],
            include_exotics=False,
            record_mode="sync",
        )
        first_win = next(row for row in first["settlement"]["items"] if row["market"] == "WIN")
        assert first_win["execution_odds"] == 4.0

        refreshed = api_betting(
            conn,
            RankingModel.new(),
            "HK20990101-ST-01",
            10000,
            "standard",
            state=state,
            model_path=tmp_path / "model.json",
            include_exotics=False,
            refresh_odds_live=True,
            record_mode="sync",
        )

    assert refreshed["odds_refresh"]["status"] == "refreshed"
    assert refreshed["ledger_price_refresh"]["updated"] == 2
    refreshed_win = next(row for row in refreshed["settlement"]["items"] if row["market"] == "WIN")
    assert refreshed_win["execution_odds"] == 8.0
    assert refreshed_win["execution_source"] == "hkjc_graphql"
