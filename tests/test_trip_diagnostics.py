from pathlib import Path

from racing_model.features import FEATURE_NAMES, build_race_features
from racing_model.model import RankingModel
from racing_model.storage import connect, init_db, insert_rows
from racing_model.trip_diagnostics import horse_context_signals, parse_running_positions


def test_trip_diagnostics_separates_luck_from_ability_and_body_trend(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        add_race(conn, "R-OLD", "2026/04/01", 1400)
        add_race(conn, "R-LAST", "2026/04/20", 1600)
        add_race(conn, "R-TARGET", "2026/05/06", 1600)
        insert_rows(
            conn,
            "runners",
            [
                runner("R-OLD", "H001", body_weight=1100, gear="B"),
                runner("R-LAST", "H001", body_weight=1110, gear="B"),
                runner("R-TARGET", "H001", body_weight=1120, gear="B/TT"),
                runner("R-TARGET", "H002", body_weight=1080, gear=""),
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                result("R-OLD", "H001", 4, 1.5, "10/9/6/4", "blocked, no clear run, ran on strongly"),
                result("R-LAST", "H001", 6, 6.0, "1/1/2/6", "led, weakened, no extra"),
            ],
        )
        conn.commit()

        signals = horse_context_signals(conn, "H001", "2026/05/06", 1600, 1120, "B/TT")
        features = build_race_features(conn, "R-TARGET")
        prediction = next(row for row in RankingModel.new().predict_race(features) if row["horse_id"] == "H001")

    assert parse_running_positions("10/9/6/4") == [10, 9, 6, 4]
    assert signals["body_weight_change"] == 0.5
    assert signals["body_weight_trend"] > 0
    assert signals["gear_change_signal"] > 0
    assert signals["trip_luck_score"] > 0
    assert signals["ability_issue_score"] > 0
    assert signals["closing_gain_score"] > 0
    assert signals["pace_fade_score"] > 0
    assert "trip_luck_score" in FEATURE_NAMES
    assert prediction["body_weight_lbs"] == 1120.0
    assert "trip_luck_score" in prediction
    assert "ability_issue_score" in prediction


def add_race(conn, race_id: str, date: str, distance_m: int) -> None:
    insert_rows(
        conn,
        "races",
        [
            {
                "race_id": race_id,
                "date": date,
                "track": "Sha Tin",
                "course": "Turf",
                "distance_m": distance_m,
                "going": "Good",
                "class_rating": "Class 4",
                "prize": 1000000,
            }
        ],
    )


def runner(race_id: str, horse_id: str, body_weight: float, gear: str) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "horse_no": 1 if horse_id == "H001" else 2,
        "horse_name": horse_id,
        "jockey": "Jockey",
        "trainer": "Trainer",
        "draw": 2,
        "weight_lbs": 120,
        "body_weight_lbs": body_weight,
        "official_rating": 60 if horse_id == "H001" else 55,
        "age": 4,
        "sex": "G",
        "running_style": "pace",
        "gear": gear,
    }


def result(race_id: str, horse_id: str, position: int, margin: float, running_positions: str, comment: str) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "finish_position": position,
        "finish_time_sec": 82.0 + position,
        "margin_lengths": margin,
        "sectional_400_sec": None,
        "sectional_800_sec": None,
        "running_positions": running_positions,
        "comment": comment,
    }
