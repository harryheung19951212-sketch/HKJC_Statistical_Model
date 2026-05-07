from pathlib import Path

from racing_model.model_registry import model_registry_report, run_and_record_model_registry
from racing_model.storage import connect, import_csv, init_db


def test_model_registry_records_walk_forward_gate(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    sample_dir = Path("data/sample")
    with connect(db_path) as conn:
        for table, filename in {
            "races": "races.csv",
            "runners": "runners.csv",
            "results": "results.csv",
            "workouts": "workouts.csv",
            "odds_ticks": "odds.csv",
        }.items():
            import_csv(conn, table, sample_dir / filename)
        conn.commit()

        recorded = run_and_record_model_registry(conn, "models/baseline.json", epochs=2)
        registry = model_registry_report(conn)

    assert recorded["status"] == "recorded"
    assert recorded["run"]["promotion_gate"] == "sample_insufficient"
    assert recorded["run"]["folds"] == 1
    assert registry["summary"]["run_count"] == 1
    assert registry["runs"][0]["promotion_gate_label"] == "樣本不足"
    assert "report_json" not in registry["runs"][0]
    assert registry["latest_report"]["summary"]["folds"] == 1
    assert registry["clv_status"]
