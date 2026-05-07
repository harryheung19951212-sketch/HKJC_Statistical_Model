from pathlib import Path

from racing_model.model import RankingModel
from racing_model.model_compare import (
    ABILITY_FEATURES,
    MARKET_FEATURES,
    dual_model_backtest,
    dual_model_comparison,
    masked_model,
)
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
    conn = connect(db_path)
    try:
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
    finally:
        conn.close()

    assert report["summary"]["runner_count"] == 2
    assert report["tracks"]["ability"]
    assert report["tracks"]["market"]
    assert len(report["runners"]) == 2
    assert report["summary"]["ability_features"] == len(ABILITY_FEATURES)
    assert "market_implied" in report["summary"]["market_features_removed"]


def test_dual_model_backtest_compares_ability_and_market_tracks(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    conn = connect(db_path)
    try:
        race_ids = ["HK20260506-ST-01", "HK20260506-ST-02"]
        insert_rows(conn, "races", [race(race_id=race_ids[0]), race(race_id=race_ids[1], distance_m=1400)])
        rows = []
        odds_rows = []
        result_rows = []
        for race_id in race_ids:
            rows.extend(
                [
                    runner("H001", 1, 70, race_id=race_id),
                    runner("H002", 2, 55, race_id=race_id),
                    runner("H003", 3, 45, race_id=race_id),
                ]
            )
            odds_rows.extend(
                [
                    odds("H001", 6.0, race_id=race_id),
                    odds("H002", 2.2, race_id=race_id),
                    odds("H003", 18.0, race_id=race_id),
                ]
            )
            result_rows.extend(
                [
                    result("H001", 1, race_id=race_id),
                    result("H002", 2, race_id=race_id),
                    result("H003", 3, race_id=race_id),
                ]
            )
        insert_rows(conn, "runners", rows)
        insert_rows(conn, "odds_ticks", odds_rows)
        insert_rows(conn, "results", result_rows)
        conn.commit()

        model = RankingModel.new()
        model.weights["official_rating"] = 1.0
        model.weights["market_implied"] = 4.0
        report = dual_model_backtest(conn, model)
    finally:
        conn.close()

    summary = report["summary"]
    assert summary["races"] == 2
    assert summary["runner_count"] == 6
    assert summary["ability_win_rate"] is not None
    assert summary["market_win_rate"] is not None
    assert summary["avg_ability_winner_rank"] is not None
    assert report["recent_races"]
    assert report["recommendation"]["verdict"] in {"insufficient", "ability_leads", "market_leads", "mixed"}


def race(race_id: str = "HK20260506-ST-01", distance_m: int = 1200) -> dict[str, object]:
    return {
        "race_id": race_id,
        "date": "2026-05-06",
        "track": "Sha Tin",
        "course": "Turf",
        "distance_m": distance_m,
        "going": "Good",
        "class_rating": "Class 4",
        "prize": 1000000,
        "race_name": "Test",
    }


def runner(horse_id: str, horse_no: int, rating: int, race_id: str = "HK20260506-ST-01") -> dict[str, object]:
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
        "weight_lbs": 120,
        "official_rating": rating,
        "age": 4,
        "sex": "G",
        "running_style": "unknown",
        "gear": "",
    }


def odds(horse_id: str, win_odds: float, race_id: str = "HK20260506-ST-01") -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "timestamp": "2026-05-06T12:00:00+08:00",
        "win_odds": win_odds,
        "place_odds": None,
        "source": "hkjc_graphql",
    }


def result(horse_id: str, finish_position: int, race_id: str = "HK20260506-ST-01") -> dict[str, object]:
    return {
        "race_id": race_id,
        "horse_id": horse_id,
        "finish_position": finish_position,
        "finish_time_sec": 70.0 + finish_position,
        "margin_lengths": float(finish_position - 1),
        "sectional_400_sec": None,
        "sectional_800_sec": None,
        "comment": "",
    }
