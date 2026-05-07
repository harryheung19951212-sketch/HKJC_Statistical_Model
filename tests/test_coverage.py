import gc
from pathlib import Path
from tempfile import TemporaryDirectory

from racing_model.coverage import (
    STATUS_DONE,
    STATUS_EXTERNAL,
    STATUS_MISSING,
    STATUS_PARTIAL,
    build_coverage_report,
)
from racing_model.storage import connect, import_csv, init_db


def load_sample_database(db_path: Path) -> None:
    init_db(db_path)
    gc.collect()
    sample_dir = Path("data/sample")
    conn = connect(db_path)
    try:
        for table, filename in {
            "races": "races.csv",
            "runners": "runners.csv",
            "results": "results.csv",
            "workouts": "workouts.csv",
            "odds_ticks": "odds.csv",
        }.items():
            import_csv(conn, table, sample_dir / filename)
        conn.commit()
    finally:
        conn.close()
        gc.collect()


def test_coverage_report_contains_all_roadmap_groups() -> None:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "racing.db"
        load_sample_database(db_path)
        conn = connect(db_path)
        try:
            report = build_coverage_report(conn)
        finally:
            conn.close()
            gc.collect()

    items = report["items"]
    statuses = {STATUS_DONE, STATUS_PARTIAL, STATUS_MISSING, STATUS_EXTERNAL}
    assert len(items) == 32
    assert sum(1 for item in items if item["category"] == "core") == 21
    assert sum(1 for item in items if item["category"] == "blind_spot") == 11
    assert {item["status"] for item in items}.issubset(statuses)
    assert all(item["data_sources"] for item in items)
    assert all(item["gaps"] for item in items)
    assert all(item["next_steps"] for item in items)
    assert report["summary"]["database"]["races"] > 0
    assert report["summary"]["database"]["runners"] > 0
    assert report["summary"]["coverage_score"] > 0
    assert report["priority_next_steps"]


if __name__ == "__main__":
    test_coverage_report_contains_all_roadmap_groups()
