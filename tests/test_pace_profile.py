from pathlib import Path

from racing_model.features import FEATURE_NAMES, build_race_features
from racing_model.model import RankingModel
from racing_model.pace_profile import horse_pace_profile
from racing_model.storage import connect, init_db, insert_rows


def test_pace_profile_builds_projection_distance_and_hidden_ability_signals(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        add_race(conn, "R-FADE", "2026/04/01", 1400, "Class 4")
        add_race(conn, "R-CLOSE", "2026/04/20", 1400, "Class 4")
        add_race(conn, "R-TARGET", "2026/05/06", 1600, "Class 3")
        add_field(conn, "R-FADE", "H001", rating=60, style="leader")
        add_field(conn, "R-CLOSE", "H001", rating=60, style="closer")
        add_target_field(conn)
        insert_rows(
            conn,
            "results",
            [
                result("R-FADE", "H001", 6, 6.0, "1/1/2/6", "led, weakened, no extra"),
                result("R-CLOSE", "H001", 3, 1.0, "11/9/5/3", "blocked, no clear run, ran on strongly"),
            ],
        )
        conn.commit()

        profile = horse_pace_profile(conn, "H001", "2026/05/06", 1600, "Class 3", 65)
        features = build_race_features(conn, "R-TARGET")
        h001 = next(row for row in features if row.horse_id == "H001")
        prediction = next(row for row in RankingModel.new().predict_race(features) if row["horse_id"] == "H001")

    for name in {
        "early_speed_profile",
        "midrace_move_score",
        "projected_position_score",
        "traffic_risk_score",
        "pace_advantage_score",
        "distance_pace_fit",
        "class_change_signal",
        "rating_change_signal",
        "hidden_ability_signal",
    }:
        assert name in FEATURE_NAMES
        assert name in prediction

    assert profile["distance_pace_fit"] > 0
    assert profile["hidden_ability_signal"] > 0
    assert profile["class_change_signal"] > 0
    assert profile["rating_change_signal"] > 0
    assert h001.features["traffic_risk_score"] > 0
    assert h001.features["distance_pace_fit"] > 0
    assert h001.features["hidden_ability_signal"] > 0


def add_race(conn, race_id: str, date: str, distance_m: int, class_rating: str) -> None:
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
                "class_rating": class_rating,
                "prize": 1000000,
            }
        ],
    )


def add_field(conn, race_id: str, horse_id: str, rating: float, style: str) -> None:
    rows = [runner(race_id, horse_id, 1, 2, rating, style)]
    for index in range(2, 13):
        rows.append(runner(race_id, f"D{race_id[-1]}{index:02d}", index, index, rating - 8, "midfield"))
    insert_rows(conn, "runners", rows)


def add_target_field(conn) -> None:
    rows = [
        runner("R-TARGET", "H001", 1, 1, 65, "closer"),
        runner("R-TARGET", "H002", 2, 9, 58, "leader"),
        runner("R-TARGET", "H003", 3, 5, 56, "pace"),
        runner("R-TARGET", "H004", 4, 3, 55, "midfield"),
        runner("R-TARGET", "H005", 5, 7, 53, "stalker"),
        runner("R-TARGET", "H006", 6, 11, 52, "closer"),
    ]
    insert_rows(conn, "runners", rows)


def runner(race_id: str, horse_id: str, horse_no: int, draw: int, rating: float, style: str) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "horse_no": horse_no,
        "horse_name": horse_id,
        "jockey": "Jockey",
        "trainer": "Trainer",
        "draw": draw,
        "weight_lbs": 120,
        "body_weight_lbs": 1100,
        "official_rating": rating,
        "age": 4,
        "sex": "G",
        "running_style": style,
        "gear": "",
    }


def result(race_id: str, horse_id: str, position: int, margin: float, running_positions: str, comment: str) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "finish_position": position,
        "finish_time_sec": 84.0 + position,
        "margin_lengths": margin,
        "sectional_400_sec": None,
        "sectional_800_sec": None,
        "running_positions": running_positions,
        "comment": comment,
    }
