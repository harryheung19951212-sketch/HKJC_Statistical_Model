from pathlib import Path

import racing_model.app_server as app_server
from racing_model.app_server import AppState, active_refreshable_race_id, api_lifecycle, current_refreshable_race_id, ensure_model_file, reconcile_pool_replay_with_final_dividends, run_lifecycle_step
from racing_model.exotic_dividends import upsert_exotic_dividends
from racing_model.betting_ledger import betting_ledger_report
from racing_model.storage import connect, init_db, insert_rows, upsert_race_status


def add_race(conn, race_id: str, race_date: str = "2026-05-06") -> None:
    insert_rows(
        conn,
        "races",
        [
            {
                "race_id": race_id,
                "date": race_date,
                "track": "Sha Tin",
                "course": "Turf",
                "distance_m": 1200,
                "going": "Good",
                "class_rating": "Class 4",
                "prize": 1000000,
                "race_name": race_id,
            }
        ],
    )


def test_lifecycle_sleeps_without_active_race(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    with connect(db_path) as conn:
        add_race(conn, "HK20260509-ST-01", "2099-01-01")
        conn.commit()

        result = run_lifecycle_step(conn, state)
        lifecycle = api_lifecycle(conn, state)

    assert result["status"] == "idle"
    assert result["message"] == "no_active_race"
    assert result["active_race_id"] is None
    assert lifecycle["mode"] == "active_race_only"
    assert lifecycle["refreshable_race_id"] is None


def test_focused_scheduled_and_live_races_are_refreshable(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    with connect(db_path) as conn:
        add_race(conn, "HK20990101-ST-01", "2099-01-01")
        add_race(conn, "HK20990101-ST-02", "2099-01-01")
        upsert_race_status(conn, "HK20990101-ST-02", "live")
        conn.commit()

        state.focus_race("HK20990101-ST-01", now=100.0)
        focused = active_refreshable_race_id(conn, state)
        state.focus_race("HK20990101-ST-02", now=101.0)
        live = active_refreshable_race_id(conn, state)
        state.sleep_race("HK20990101-ST-02")

    assert focused == "HK20990101-ST-01"
    assert live == "HK20990101-ST-02"
    assert state.active_race(now=102.0) is None


def test_active_race_does_not_expire_by_ttl(tmp_path: Path) -> None:
    state = AppState(tmp_path / "model.json", 30)

    state.focus_race("HK20990101-ST-01", now=100.0)
    active = state.active_race(now=120.0)
    still_active = state.active_race(now=250.0)

    assert active == "HK20990101-ST-01"
    assert still_active == "HK20990101-ST-01"
    assert state.active_race_expires_in(now=250.0) is None


def test_live_active_race_continues_result_detection(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    calls: list[tuple[str, str]] = []
    original_refresh_odds = app_server.refresh_odds
    original_refresh_exotic = app_server.refresh_exotic_dividends
    original_refresh_results = app_server.refresh_hkjc_results_if_available
    original_backfill = app_server.backfill_final_place_odds

    def fake_refresh_odds(conn, race_id, provider):
        calls.append(("odds", race_id))
        return 0

    def fake_refresh_exotic(conn, race_id, provider):
        calls.append(("exotic", race_id))
        return {"inserted": 0, "status": "ok"}

    def fake_refresh_results(conn, race_id, model, user_agent, request_delay_seconds):
        calls.append(("results", race_id))
        upsert_race_status(conn, race_id, "resulted")
        conn.commit()
        return {"race_id": race_id, "status": "resulted"}

    def fake_backfill(conn, race_id, provider):
        calls.append(("backfill", race_id))
        return {"race_id": race_id, "status": "done", "inserted": 0}

    app_server.refresh_odds = fake_refresh_odds
    app_server.refresh_exotic_dividends = fake_refresh_exotic
    app_server.refresh_hkjc_results_if_available = fake_refresh_results
    app_server.backfill_final_place_odds = fake_backfill
    try:
        with connect(db_path) as conn:
            add_race(conn, "HK20260506-ST-01")
            upsert_race_status(conn, "HK20260506-ST-01", "live")
            conn.commit()
            state.focus_race("HK20260506-ST-01", now=100.0)

            result = run_lifecycle_step(conn, state)
    finally:
        app_server.refresh_odds = original_refresh_odds
        app_server.refresh_exotic_dividends = original_refresh_exotic
        app_server.refresh_hkjc_results_if_available = original_refresh_results
        app_server.backfill_final_place_odds = original_backfill

    assert result["status"] == "done"
    assert result["result"]["status"] == "resulted"
    assert "betting_reconciliation" in result["result"]
    assert "pool_replay" in result["result"]
    assert calls == [
        ("odds", "HK20260506-ST-01"),
        ("exotic", "HK20260506-ST-01"),
        ("results", "HK20260506-ST-01"),
        ("backfill", "HK20260506-ST-01"),
    ]
    assert state.active_race(now=101.0) is None


def test_manual_lifecycle_step_can_run_globally_without_active_race(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    calls: list[tuple[str, str]] = []
    original_refresh_odds = app_server.refresh_odds
    original_refresh_exotic = app_server.refresh_exotic_dividends
    original_refresh_results = app_server.refresh_hkjc_results_if_available

    def fake_refresh_odds(conn, race_id, provider):
        calls.append(("odds", race_id))
        return 1

    def fake_refresh_exotic(conn, race_id, provider):
        calls.append(("exotic", race_id))
        return {"inserted": 2, "status": "ok"}

    def fake_refresh_results(conn, race_id, model, user_agent, request_delay_seconds):
        calls.append(("results", race_id))
        return {"race_id": race_id, "status": "scheduled"}

    app_server.refresh_odds = fake_refresh_odds
    app_server.refresh_exotic_dividends = fake_refresh_exotic
    app_server.refresh_hkjc_results_if_available = fake_refresh_results
    try:
        with connect(db_path) as conn:
            add_race(conn, "HK20990101-ST-01", "2099-01-01")
            add_race(conn, "HK20990101-ST-02", "2099-01-01")
            conn.commit()

            result = run_lifecycle_step(conn, state, scope="global")
    finally:
        app_server.refresh_odds = original_refresh_odds
        app_server.refresh_exotic_dividends = original_refresh_exotic
        app_server.refresh_hkjc_results_if_available = original_refresh_results

    assert result["status"] == "done"
    assert result["scope"] == "global"
    assert result["race_id"] == "HK20990101-ST-01"
    assert calls == [
        ("odds", "HK20990101-ST-01"),
        ("exotic", "HK20990101-ST-01"),
        ("results", "HK20990101-ST-01"),
    ]


def test_global_refresh_ignores_stale_past_scheduled_races(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    with connect(db_path) as conn:
        add_race(conn, "HK20251115-ST-10", "2025-11-15")
        add_race(conn, "HK20990101-ST-01", "2099-01-01")
        conn.commit()

        selected = current_refreshable_race_id(conn)
        state.focus_race("HK20251115-ST-10", now=100.0)
        focused = active_refreshable_race_id(conn, state)

    assert selected == "HK20990101-ST-01"
    assert focused is None


def test_missing_server_model_is_trained_from_database(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    model_path = tmp_path / "models" / "baseline.json"
    init_db(db_path)
    with connect(db_path) as conn:
        add_race(conn, "HK20260506-ST-02")
        insert_rows(conn, "runners", [runner("H001", 1), runner("H002", 2)])
        insert_rows(conn, "results", [result("H001", 1), result("H002", 2)])
        conn.commit()

        training = ensure_model_file(conn, model_path, epochs=1)
        second = ensure_model_file(conn, model_path, epochs=1)

    assert training["trained"] is True
    assert training["training_races"] == 1
    assert model_path.exists()
    assert second["reason"] == "model_exists"


def test_pool_replay_reconcile_refreshes_final_dividends_then_settles(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)
    calls: list[tuple[str, str]] = []
    original_refresh_results = app_server.refresh_hkjc_results_if_available
    original_backfill = app_server.backfill_final_place_odds

    def fake_refresh_results(conn, race_id, model, user_agent, request_delay_seconds):
        calls.append(("results", race_id))
        insert_rows(conn, "results", [result("H001", 1), result("H002", 2), result("H003", 3)])
        upsert_exotic_dividends(
            conn,
            race_id,
            [{"market": "QPL", "combination": "1+2", "dividend": 18.0}],
            source="hkjc_results_final",
            dividend_status="final",
        )
        upsert_race_status(conn, race_id, "resulted")
        conn.commit()
        return {"race_id": race_id, "status": "resulted", "results": 3, "exotic_dividends": 1}

    def fake_backfill(conn, race_id, provider):
        calls.append(("backfill", race_id))
        return {"race_id": race_id, "status": "done", "inserted": 0}

    app_server.refresh_hkjc_results_if_available = fake_refresh_results
    app_server.backfill_final_place_odds = fake_backfill
    try:
        with connect(db_path) as conn:
            add_race(conn, "HK20260506-ST-02")
            insert_rows(conn, "runners", [runner("H001", 1), runner("H002", 2), runner("H003", 3)])
            insert_rows(conn, "betting_recommendations", [pending_exotic_recommendation()])
            conn.commit()

            result_payload = reconcile_pool_replay_with_final_dividends(conn, state, race_id="HK20260506-ST-02")
            ledger = betting_ledger_report(conn, "HK20260506-ST-02")
    finally:
        app_server.refresh_hkjc_results_if_available = original_refresh_results
        app_server.backfill_final_place_odds = original_backfill

    assert result_payload["status"] == "done"
    assert result_payload["refresh_targets"] == ["HK20260506-ST-02"]
    assert result_payload["updated"] == 1
    assert calls == [("results", "HK20260506-ST-02"), ("backfill", "HK20260506-ST-02")]
    assert ledger["summary"]["reconciled"] == 1
    assert ledger["items"][0]["profit"] == 340


def runner(horse_id: str, horse_no: int) -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-02",
        "horse_id": horse_id,
        "horse_no": horse_no,
        "horse_name": f"Horse {horse_no}",
        "horse_name_zh": f"馬{horse_no}",
        "jockey": "Jockey",
        "jockey_zh": "騎師",
        "trainer": "Trainer",
        "trainer_zh": "練馬師",
        "draw": horse_no,
        "weight_lbs": 120,
        "official_rating": 50,
        "age": 4,
        "sex": "G",
        "running_style": "pace",
        "gear": "",
    }


def result(horse_id: str, position: int) -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-02",
        "horse_id": horse_id,
        "finish_position": position,
        "finish_time_sec": 70.0 + position,
        "margin_lengths": position - 1,
        "sectional_400_sec": None,
        "sectional_800_sec": None,
        "comment": "",
    }


def pending_exotic_recommendation() -> dict[str, object]:
    return {
        "recommendation_id": "pending-qpl-final-refresh",
        "created_at": "2026-05-06T12:00:00+00:00",
        "updated_at": "2026-05-06T12:00:00+00:00",
        "source": "test",
        "model_path": "models/test.json",
        "race_id": "HK20260506-ST-02",
        "race_date": "2026-05-06",
        "market": "QPL",
        "market_label": "位置Q",
        "horse_id": "1+2",
        "horse_no": None,
        "horse_name": "1 + 2",
        "model_rank": 1,
        "risk_profile": "standard",
        "bankroll": 10000,
        "probability": 0.12,
        "recommended_odds": 20.0,
        "odds_source": "hkjc_graphql",
        "fair_odds": 8.33,
        "market_probability": 0.05,
        "edge": 0.07,
        "expected_value": 1.4,
        "recommended_stake": 20,
        "race_status_at_recommendation": "scheduled",
        "action": "有值博",
        "reason": "test",
        "execution_status": "confirmed",
        "executed_at": "2026-05-06T12:01:00+00:00",
        "execution_odds": 20.0,
        "execution_stake": 20,
        "execution_source": "hkjc_graphql",
        "execution_slippage": 0.0,
        "execution_clv": None,
        "final_odds": None,
        "finish_position": None,
        "outcome_win": None,
        "returned": None,
        "profit": None,
        "clv": None,
        "slippage": None,
        "reconciled_at": None,
        "reconciliation_status": "pending",
    }
