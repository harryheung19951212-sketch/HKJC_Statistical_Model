import json
from pathlib import Path

from racing_model.features import RunnerFeatures
from racing_model.model import RankingModel


def test_ranking_model_saves_and_loads_temperature(tmp_path: Path) -> None:
    model_path = tmp_path / "model.json"
    model = RankingModel.new()
    model.temperature = 1.35

    model.save(model_path)
    loaded = RankingModel.load(model_path)

    assert loaded.temperature == 1.35


def test_ranking_model_loads_legacy_model_with_default_temperature(tmp_path: Path) -> None:
    model_path = tmp_path / "legacy.json"
    model = RankingModel.new()
    model.save(model_path)
    data = json.loads(model_path.read_text(encoding="utf-8"))
    data.pop("temperature")
    model_path.write_text(json.dumps(data), encoding="utf-8")

    loaded = RankingModel.load(model_path)

    assert loaded.temperature == 1.0


def test_temperature_reduces_win_probability_confidence() -> None:
    hot = one_feature_model(temperature=1.0)
    conservative = one_feature_model(temperature=2.0)
    runners = [
        runner("H001", 1, 2.0),
        runner("H002", 2, 0.0),
    ]

    hot_probability = hot.predict_race(runners)[0]["win_probability"]
    conservative_probability = conservative.predict_race(runners)[0]["win_probability"]

    assert float(conservative_probability) < float(hot_probability)
    assert float(conservative_probability) > 0.5


def one_feature_model(temperature: float) -> RankingModel:
    return RankingModel(
        feature_names=["official_rating"],
        weights={"official_rating": 1.0},
        means={"official_rating": 0.0},
        scales={"official_rating": 1.0},
        temperature=temperature,
    )


def runner(horse_id: str, horse_no: int, rating: float) -> RunnerFeatures:
    return RunnerFeatures(
        race_id="R1",
        horse_id=horse_id,
        horse_no=horse_no,
        horse_name=horse_id,
        last_six_runs="",
        horse_name_zh="",
        running_style="",
        gear="",
        body_weight_lbs=None,
        jockey="",
        jockey_zh="",
        trainer="",
        trainer_zh="",
        draw=horse_no,
        features={"official_rating": rating, "market_implied": 0.0},
        latest_win_odds=3.0,
        latest_win_odds_source="test",
        latest_place_odds=None,
        latest_place_odds_source=None,
        finish_position=None,
    )
