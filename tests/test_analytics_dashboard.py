from pathlib import Path

import racing_model.app_server as app_server
from racing_model.app_server import AppState, api_analytics_dashboard
from racing_model.storage import connect, init_db


def test_fast_analytics_dashboard_defers_heavy_reports(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    state = AppState(tmp_path / "model.json", 30)

    def fail_heavy(*args, **kwargs):
        raise AssertionError("fast analytics should not run heavy reports")

    monkeypatch.setattr(app_server, "run_backtest", fail_heavy)
    monkeypatch.setattr(app_server, "run_walk_forward_versions", fail_heavy)
    monkeypatch.setattr(app_server, "dual_model_backtest", fail_heavy)

    with connect(db_path) as conn:
        payload = api_analytics_dashboard(conn, state, include_coverage=True, fast=True)

    assert payload["fast"] is True
    assert payload["backtest"]["deferred"] is True
    assert payload["pool_replay"]["deferred"] is True
    assert "coverage" in payload
