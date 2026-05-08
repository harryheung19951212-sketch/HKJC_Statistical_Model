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
    assert len(items) == 36
    assert report["summary"]["target_groups"] == 36
    assert sum(1 for item in items if item["category"] == "core") == 21
    assert sum(1 for item in items if item["category"] == "advanced") == 15
    assert {item["status"] for item in items}.issubset(statuses)
    assert all(item["data_sources"] for item in items)
    assert all(item["gaps"] for item in items)
    assert all(item["next_steps"] for item in items)
    assert all(item["sub_items"] for item in items)
    pace_item = next(item for item in items if item["id"] == 27)
    assert pace_item["status"] == STATUS_PARTIAL
    assert "src/racing_model/pace.py" in pace_item["supported_files"]
    scorecard_item = next(item for item in items if item["id"] == 18)
    assert "src/racing_model/promotion_scorecard.py" in scorecard_item["supported_files"]
    for item_id in {2, 3, 6, 8, 21, 33}:
        item = next(row for row in items if row["id"] == item_id)
        assert "src/racing_model/trip_diagnostics.py" in item["supported_files"]
    assert report["summary"]["database"]["races"] > 0
    assert report["summary"]["database"]["runners"] > 0
    assert report["summary"]["coverage_score"] > 0
    assert report["priority_next_steps"]


def test_coverage_report_is_localized_for_users() -> None:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "racing.db"
        load_sample_database(db_path)
        conn = connect(db_path)
        try:
            report = build_coverage_report(conn)
        finally:
            conn.close()
            gc.collect()

    first = report["items"][0]
    names = {item["name"] for item in report["items"]}
    joined_sources = " ".join(first["data_sources"])
    assert "賽事基本條件" in names
    assert "馬匹本身能力" in names
    assert "臨場資金流模型" in names
    assert "不下注決策" in names
    assert "馬場" in first["sub_items"]
    assert first["current_support"].startswith("已儲存")
    assert "HKJC" in joined_sources
    assert "Horse baseline ability" not in names
    assert "Recent condition" not in names


if __name__ == "__main__":
    test_coverage_report_contains_all_roadmap_groups()
