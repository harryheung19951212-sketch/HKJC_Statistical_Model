from pathlib import Path

from racing_model.features import adjusted_speed_figure, build_race_features
from racing_model.storage import connect, init_db, insert_rows


def test_adjusted_speed_figure_ranks_faster_prior_runs_higher(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                race("R-OLD", "2026-05-01"),
                race("R-TARGET", "2026-05-08"),
            ],
        )
        insert_rows(
            conn,
            "runners",
            [
                runner("R-OLD", "H001", 1, 124),
                runner("R-OLD", "H002", 2, 116),
                runner("R-TARGET", "H001", 1, 124),
                runner("R-TARGET", "H002", 2, 116),
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                result("R-OLD", "H001", 1, 70.0, 0.0),
                result("R-OLD", "H002", 8, 72.0, 10.0),
            ],
        )
        conn.commit()

        features = {row.horse_id: row.features for row in build_race_features(conn, "R-TARGET")}

    assert features["H001"]["adjusted_speed_figure"] > features["H002"]["adjusted_speed_figure"]
    assert features["H001"]["adjusted_speed_figure"] > 95
    assert features["H002"]["adjusted_speed_figure"] < 95


def test_adjusted_speed_figure_uses_only_pre_race_history(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                race("R-OLD", "2026-05-01"),
                race("R-TARGET", "2026-05-08"),
            ],
        )
        insert_rows(
            conn,
            "runners",
            [
                runner("R-OLD", "H001", 1, 120),
                runner("R-TARGET", "H001", 1, 120),
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                result("R-OLD", "H001", 2, 72.0, 4.0),
            ],
        )
        conn.commit()

        before_target_result = adjusted_speed_figure(conn, "H001", "2026-05-08", 1200, "Good")
        insert_rows(conn, "results", [result("R-TARGET", "H001", 1, 68.0, 0.0)])
        conn.commit()
        after_target_result = adjusted_speed_figure(conn, "H001", "2026-05-08", 1200, "Good")

    assert after_target_result == before_target_result


def race(race_id: str, date: str) -> dict[str, object]:
    return {
        "race_id": race_id,
        "date": date,
        "track": "Sha Tin",
        "course": "Turf",
        "distance_m": 1200,
        "going": "Good",
        "class_rating": "Class 4",
        "prize": 1000000,
        "race_name": "Test",
    }


def runner(race_id: str, horse_id: str, horse_no: int, weight_lbs: float) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "horse_no": horse_no,
        "horse_name": f"Horse {horse_no}",
        "horse_name_zh": f"馬{horse_no}",
        "jockey": "Jockey",
        "jockey_zh": "騎師",
        "trainer": "Trainer",
        "trainer_zh": "練馬師",
        "draw": horse_no,
        "weight_lbs": weight_lbs,
        "official_rating": 50,
        "age": 4,
        "sex": "G",
        "running_style": "unknown",
        "gear": "",
    }


def result(
    race_id: str,
    horse_id: str,
    finish_position: int,
    finish_time_sec: float,
    margin_lengths: float,
) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "finish_position": finish_position,
        "finish_time_sec": finish_time_sec,
        "margin_lengths": margin_lengths,
        "sectional_400_sec": None,
        "sectional_800_sec": None,
        "comment": "",
    }
