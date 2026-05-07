from pathlib import Path

from racing_model.model import RankingModel
from racing_model.model_compare import ABILITY_FEATURES, MARKET_FEATURES, dual_model_comparison, masked_model
from racing_model.storage import connect, init_db, insert_rows


def test_masked_model_removes_market_features() -> None:
    model = RankingModel.new()
    model.weights["market_implied"] = 99.0
    model.weights["official_rating"] = 1.5

    ability = masked_model(model, ABILITY_FEATURES)

    assert "market_implied" not in ability.feature_names
    assert not MARKET_FEATURES.intersection(ability.feature_names)
    assert ability.weights["official_rating"] == 1.5


def test_dual_model_comparison_returns_two_tracks(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(conn, "races", [race()])
        insert_rows(conn, "runners", [runner("H001", 1, 60), runner("H002", 2, 40)])
        insert_rows(
            conn,
            "odds_ticks",
            [
                odds("H001", 10.0),
                odds("H002", 2.0),
            ],
        )
        conn.commit()
        model = RankingModel.new()
        model.weights["official_rating"] = 1.0
        model.weights["market_implied"] = 4.0
        report = dual_model_comparison(conn, model, "HK20260506-ST-01")

    assert report["summary"]["runner_count"] == 2
    assert report["tracks"]["ability"]
    assert report["tracks"]["market"]
    assert len(report["runners"]) == 2
    assert report["summary"]["ability_features"] == len(ABILITY_FEATURES)
    assert "market_implied" in report["summary"]["market_features_removed"]


def race() -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-01",
        "date": "2026-05-06",
        "track": "Sha Tin",
        "course": "Turf",
        "distance_m": 1200,
        "going": "Good",
        "class_rating": "Class 4",
        "prize": 1000000,
        "race_name": "Test",
    }


def runner(horse_id: str, horse_no: int, rating: int) -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-01",
        "horse_id": horse_id,
        "horse_no": horse_no,
        "horse_name": f"Horse {horse_no}",
        "horse_name_zh": f"馬{horse_no}",
        "jockey": "Jockey",
        "jockey_zh": "騎師",
        "trainer": "Trainer",
        "trainer_zh": "練馬師",
        "draw": horse_no,
        "weight_lbs": 120,
        "official_rating": rating,
        "age": 4,
        "sex": "G",
        "running_style": "unknown",
        "gear": "",
    }


def odds(horse_id: str, win_odds: float) -> dict[str, object]:
    return {
        "race_id": "HK20260506-ST-01",
        "horse_id": horse_id,
        "timestamp": "2026-05-06T12:00:00+08:00",
        "win_odds": win_odds,
        "place_odds": None,
        "source": "hkjc_graphql",
    }
