import gc
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from racing_model.features import build_race_features
from racing_model.storage import connect, init_db, insert_rows
from racing_model.track_bias import same_day_track_bias
from racing_model.walk_forward import default_variants


def build_bias_database(db_path: Path) -> None:
    init_db(db_path)
    conn = connect(db_path)
    try:
        insert_rows(
            conn,
            "races",
            [
                race("R20260506-S1"),
                race("R20260506-S2"),
                race("R20260506-S3"),
            ],
        )
        insert_rows(
            conn,
            "runners",
            [
                runner("R20260506-S1", "H101", 1, "leader"),
                runner("R20260506-S1", "H102", 2, "stalker"),
                runner("R20260506-S1", "H103", 3, "closer"),
                runner("R20260506-S2", "H201", 1, "leader"),
                runner("R20260506-S2", "H202", 2, "stalker"),
                runner("R20260506-S2", "H203", 3, "closer"),
                runner("R20260506-S3", "H301", 1, "leader"),
                runner("R20260506-S3", "H302", 2, "stalker"),
                runner("R20260506-S3", "H303", 3, "closer"),
            ],
        )
        insert_rows(
            conn,
            "results",
            [
                result("R20260506-S1", "H101", 1),
                result("R20260506-S1", "H102", 3),
                result("R20260506-S1", "H103", 2),
                result("R20260506-S3", "H301", 3),
                result("R20260506-S3", "H302", 2),
                result("R20260506-S3", "H303", 1),
            ],
        )
        conn.commit()
    finally:
        conn.close()
        gc.collect()


def race(race_id: str) -> dict[str, object]:
    return {
        "race_id": race_id,
        "date": "2026-05-06",
        "track": "Sha Tin",
        "course": "Turf",
        "distance_m": 1200,
        "going": "Good",
        "class_rating": "Class 3",
        "prize": 1000000,
    }


def runner(race_id: str, horse_id: str, draw: int, running_style: str) -> dict[str, object]:
    return {
        "race_id": race_id,
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


def result(race_id: str, horse_id: str, finish_position: int) -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "finish_position": finish_position,
        "finish_time_sec": 70 + finish_position,
        "margin_lengths": max(finish_position - 1, 0),
    }


def test_same_day_track_bias_uses_only_prior_resulted_races() -> None:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "racing.db"
        build_bias_database(db_path)
        conn = connect(db_path)
        try:
            bias = same_day_track_bias(conn, "R20260506-S2")
            features = {runner.horse_id: runner.features for runner in build_race_features(conn, "R20260506-S2")}
        finally:
            conn.close()
            gc.collect()

    assert bias.source_race_ids == ["R20260506-S1"]
    assert math.isclose(bias.inside_bias, 1 / 3, rel_tol=0.0001)
    assert math.isclose(bias.front_bias, 1 / 3, rel_tol=0.0001)
    assert math.isclose(features["H201"]["same_day_inside_bias"], 1 / 3, rel_tol=0.0001)
    assert math.isclose(features["H201"]["same_day_pace_bias"], 1 / 3, rel_tol=0.0001)
    assert features["H203"]["same_day_inside_bias"] == 0.0


def test_first_race_has_no_same_day_track_bias() -> None:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "racing.db"
        build_bias_database(db_path)
        conn = connect(db_path)
        try:
            bias = same_day_track_bias(conn, "R20260506-S1")
            features = build_race_features(conn, "R20260506-S1")
        finally:
            conn.close()
            gc.collect()

    assert bias.source_race_ids == []
    assert all(runner.features["same_day_inside_bias"] == 0.0 for runner in features)
    assert all(runner.features["same_day_outside_bias"] == 0.0 for runner in features)
    assert all(runner.features["same_day_pace_bias"] == 0.0 for runner in features)


def test_walk_forward_variants_include_track_bias_ablation() -> None:
    variants = {variant.variant_id: variant for variant in default_variants()}
    assert "no_track_bias" in variants
    assert "same_day_inside_bias" in variants["baseline"].feature_names
    assert "same_day_inside_bias" not in variants["no_track_bias"].feature_names


if __name__ == "__main__":
    test_same_day_track_bias_uses_only_prior_resulted_races()
    test_first_race_has_no_same_day_track_bias()
    test_walk_forward_variants_include_track_bias_ablation()
