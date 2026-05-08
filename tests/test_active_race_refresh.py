from pathlib import Path

import racing_model.app_server as app_server
from racing_model.app_server import AppState, active_refreshable_race_id, api_lifecycle, run_lifecycle_step
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
