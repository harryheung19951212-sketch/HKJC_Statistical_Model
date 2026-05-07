import gc
from pathlib import Path
from tempfile import TemporaryDirectory

from racing_model.error_taxonomy import error_taxonomy_report, persisted_error_reviews
from racing_model.model import RankingModel
from racing_model.storage import connect, fetch_all, init_db, insert_rows


def build_taxonomy_database(db_path: Path) -> None:
    init_db(db_path)
    conn = connect(db_path)
    try:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "R-TAX",
                    "date": "2026-05-06",
                    "track": "Sha Tin",
                    "course": "Turf",
                    "distance_m": 1200,
                    "going": "Good",
                    "class_rating": "Class 3",
                    "prize": 1000000,
                }
            ],
        )
        insert_rows(
            conn,
            "runners",
            [
                runner("H001", 1, "leader"),
                runner("H002", 2, "closer"),
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                result("H001", 2),
                result("H002", 1),
            ],
        )
        insert_rows(
            conn,
            "odds_ticks",
            [
                odds("H001", 2.0),
                odds("H002", 1.8),
            ],
        )
        conn.commit()
    finally:
        conn.close()
        gc.collect()


def runner(horse_id: str, draw: int, running_style: str) -> dict[str, object]:
    return {
        "race_id": "R-TAX",
        "horse_id": horse_id,
        "horse_no": draw,
        "horse_name": horse_id,
        "jockey": "Jockey",
        "trainer": "Trainer",
        "draw": draw,
        "weight_lbs": 120,
        "official_rating": 50,
        "age": 5,
        "sex": "G",
        "running_style": running_style,
        "gear": "",
    }


def result(horse_id: str, finish_position: int) -> dict[str, object]:
    return {
        "race_id": "R-TAX",
        "horse_id": horse_id,
        "finish_position": finish_position,
        "finish_time_sec": 70 + finish_position,
        "margin_lengths": max(finish_position - 1, 0),
    }


def odds(horse_id: str, win_odds: float) -> dict[str, object]:
    return {
        "race_id": "R-TAX",
        "horse_id": horse_id,
        "timestamp": "2026-05-06T12:00:00+08:00",
        "win_odds": win_odds,
        "place_odds": None,
        "source": "hkjc_results_final",
    }


def test_error_taxonomy_persists_reviews() -> None:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "racing.db"
        build_taxonomy_database(db_path)
        conn = connect(db_path)
        try:
            report = error_taxonomy_report(conn, RankingModel.new())
            persisted = persisted_error_reviews(conn)
            table_count = fetch_all(conn, "SELECT count(*) AS n FROM race_error_reviews")[0]["n"]
        finally:
            conn.close()
            gc.collect()

    categories = {row["category"] for row in report["reviews"]}
    assert report["summary"]["reviewed_races"] == 1
    assert report["summary"]["review_count"] >= 1
    assert "calibration_error" in categories
    assert persisted
    assert table_count == len(persisted)


if __name__ == "__main__":
    test_error_taxonomy_persists_reviews()
