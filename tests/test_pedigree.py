from pathlib import Path

from racing_model.features import FEATURE_NAMES, build_race_features, pedigree_context
from racing_model.model import RankingModel
from racing_model.storage import connect, init_db, insert_rows


def test_pedigree_context_scores_related_horses_by_trip_and_surface(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        add_race(conn, "R-OLD-1", "2026/04/01", 1200, "Turf", "Good")
        add_race(conn, "R-OLD-2", "2026/04/08", 1200, "Turf", "Good")
        add_race(conn, "R-TARGET", "2026/05/09", 1200, "Turf", "Good")
        insert_rows(
            conn,
            "runners",
            [
                runner("R-OLD-1", "REL1", 1, "Sire A", "Dam X"),
                runner("R-OLD-2", "REL2", 2, "Sire A", "Dam Y"),
                runner("R-TARGET", "NEW1", 3, "Sire A", "Dam Z"),
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                result("R-OLD-1", "REL1", 1, 0.0),
                result("R-OLD-2", "REL2", 2, 1.0),
            ],
        )
        conn.commit()

        pedigree = pedigree_context(conn, "Sire A", "Dam Z", "NEW1", "2026/05/09", 1200, "Turf", "Good")
        prediction = RankingModel.new().predict_race(build_race_features(conn, "R-TARGET"))[0]

    assert pedigree["pedigree_distance_fit"] > 0.45
    assert pedigree["pedigree_surface_fit"] > 0.45
    assert pedigree["pedigree_novelty_risk"] < 0
    assert "pedigree_distance_fit" in FEATURE_NAMES
    assert prediction["pedigree_distance_fit"] > 0
    assert prediction["pedigree_surface_fit"] > 0


def add_race(conn, race_id: str, date: str, distance_m: int, course: str, going: str) -> None:
    insert_rows(
        conn,
        "races",
        [
            {
                "race_id": race_id,
                "date": date,
                "track": "Sha Tin",
                "course": course,
                "distance_m": distance_m,
                "going": going,
                "class_rating": "Class 4",
                "prize": 1000000,
            }
        ],
    )


def runner(race_id: str, horse_id: str, horse_no: int, sire: str, dam: str) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "horse_no": horse_no,
        "horse_name": horse_id,
        "jockey": "Jockey",
        "trainer": "Trainer",
        "draw": horse_no,
        "weight_lbs": 120,
        "body_weight_lbs": 1100,
        "official_rating": 60,
        "age": 3,
        "sex": "G",
        "sire": sire,
        "dam": dam,
        "running_style": "pace",
        "gear": "",
    }


def result(race_id: str, horse_id: str, finish_position: int, margin_lengths: float) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "finish_position": finish_position,
        "finish_time_sec": 70.0 + finish_position,
        "margin_lengths": margin_lengths,
        "sectional_400_sec": None,
        "sectional_800_sec": None,
        "running_positions": "",
        "comment": "",
    }
