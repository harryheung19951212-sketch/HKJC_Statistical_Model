import gc
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from racing_model.features import build_race_features, late_market_flow
from racing_model.storage import connect, init_db, insert_rows
from racing_model.walk_forward import default_variants


def build_flow_database(db_path: Path) -> None:
    init_db(db_path)
    conn = connect(db_path)
    try:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "R-FLOW",
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
                runner("H001", 1),
                runner("H002", 2),
            ],
        )
        insert_rows(
            conn,
            "odds_ticks",
            [
                odds("H001", "2026-05-06T12:00:00+08:00", 5.0, "hkjc_mqtt"),
                odds("H001", "2026-05-06T12:03:00+08:00", 4.0, "hkjc_mqtt"),
                odds("H001", "2026-05-06T12:04:20+08:00", 3.8, "hkjc_mqtt"),
                odds("H001", "2026-05-06T12:04:40+08:00", 3.5, "hkjc_mqtt"),
                odds("H001", "2026-05-06T12:05:00+08:00", 2.0, "hkjc_results_final"),
                odds("H002", "2026-05-06T12:00:00+08:00", 4.0, "hkjc_mqtt"),
                odds("H002", "2026-05-06T12:03:00+08:00", 4.4, "hkjc_mqtt"),
                odds("H002", "2026-05-06T12:04:20+08:00", 4.6, "hkjc_mqtt"),
                odds("H002", "2026-05-06T12:04:40+08:00", 4.8, "hkjc_mqtt"),
                odds("H002", "2026-05-06T12:05:00+08:00", 8.0, "hkjc_results_final"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
        gc.collect()


def runner(horse_id: str, draw: int) -> dict[str, object]:
    return {
        "race_id": "R-FLOW",
        "horse_id": horse_id,
        "horse_no": draw,
        "horse_name": f"Horse {draw}",
        "jockey": "Jockey",
        "trainer": "Trainer",
        "draw": draw,
        "weight_lbs": 120,
        "official_rating": 50,
        "age": 5,
        "sex": "G",
        "running_style": "pace",
        "gear": "",
    }


def odds(horse_id: str, timestamp: str, win_odds: float, source: str) -> dict[str, object]:
    return {
        "race_id": "R-FLOW",
        "horse_id": horse_id,
        "timestamp": timestamp,
        "win_odds": win_odds,
        "place_odds": None,
        "source": source,
    }


def test_late_market_flow_uses_live_ticks_without_result_odds() -> None:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "racing.db"
        build_flow_database(db_path)
        conn = connect(db_path)
        try:
            flow = late_market_flow(conn, "R-FLOW")
            runners = {runner.horse_id: runner for runner in build_race_features(conn, "R-FLOW")}
        finally:
            conn.close()
            gc.collect()

    assert math.isclose(flow["H001"]["odds_delta_5m"], math.log(5.0 / 3.5), rel_tol=0.0001)
    assert math.isclose(flow["H001"]["odds_delta_2m"], math.log(4.0 / 3.5), rel_tol=0.0001)
    assert math.isclose(flow["H001"]["odds_delta_30s"], math.log(3.8 / 3.5), rel_tol=0.0001)
    assert flow["H001"]["late_steam"] > 0
    assert flow["H001"]["late_drift"] == 0
    assert flow["H002"]["late_steam"] == 0
    assert flow["H002"]["late_drift"] < 0
    assert runners["H001"].features["odds_delta_5m"] == flow["H001"]["odds_delta_5m"]


def test_walk_forward_variants_include_late_flow_ablation() -> None:
    variants = {variant.variant_id: variant for variant in default_variants()}
    assert "no_late_flow" in variants
    assert "odds_delta_5m" in variants["baseline"].feature_names
    assert "odds_delta_5m" not in variants["no_late_flow"].feature_names
    assert "odds_delta_5m" not in variants["no_market"].feature_names
    assert "odds_delta_5m" in variants["market_only"].feature_names


if __name__ == "__main__":
    test_late_market_flow_uses_live_ticks_without_result_odds()
    test_walk_forward_variants_include_late_flow_ablation()
