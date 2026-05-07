from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Any

from .features import (
    FEATURE_NAMES,
    LATE_MARKET_FLOW_FEATURES,
    SAME_DAY_TRACK_BIAS_FEATURES,
    RunnerFeatures,
    build_race_features,
)
from .model import RankingModel, softmax
from .storage import fetch_all


@dataclass(frozen=True)
class ModelVariant:
    variant_id: str
    label: str
    description: str
    feature_names: list[str]
    temperature: float = 1.0


def default_variants() -> list[ModelVariant]:
    market_features = {"market_implied", *LATE_MARKET_FLOW_FEATURES}
    no_market = [name for name in FEATURE_NAMES if name not in market_features]
    no_late_flow = [name for name in FEATURE_NAMES if name not in set(LATE_MARKET_FLOW_FEATURES)]
    no_track_bias = [name for name in FEATURE_NAMES if name not in set(SAME_DAY_TRACK_BIAS_FEATURES)]
    return [
        ModelVariant(
            "baseline",
            "Baseline",
            "現有全特徵模型，包含市場賠率機率、臨場賠率流及同日跑道偏差。",
            list(FEATURE_NAMES),
        ),
        ModelVariant(
            "no_late_flow",
            "No Late Flow",
            "保留市場機率，但移除最後 5/2/0.5 分鐘賠率流，用來驗證 late-flow 是否真有提升。",
            no_late_flow,
        ),
        ModelVariant(
            "no_track_bias",
            "No Track Bias",
            "保留市場及 late-flow，但移除同日跑道偏差，用來驗證 bias replay 是否真有提升。",
            no_track_bias,
        ),
        ModelVariant(
            "no_market",
            "No Market",
            "移除市場賠率及 late-flow 特徵，只測試賽事本身因素有冇預測力。",
            no_market,
        ),
        ModelVariant(
            "market_only",
            "Market Only",
            "只用市場機率及臨場賠率流，作為必須打贏嘅市場基線。",
            ["market_implied", *LATE_MARKET_FLOW_FEATURES],
        ),
        ModelVariant(
            "conservative_calibrated",
            "Conservative",
            "沿用全特徵，但用溫度縮細勝率差距，測試是否可減少首選過熱。",
            list(FEATURE_NAMES),
            temperature=1.35,
        ),
    ]


def run_walk_forward_versions(
    conn: sqlite3.Connection,
    min_train_races: int = 1,
    epochs: int = 80,
    min_expected_value: float = 0.05,
    stake: float = 10.0,
) -> dict[str, Any]:
    race_ids = resulted_race_ids(conn)
    variants = default_variants()
    race_features = {race_id: build_race_features(conn, race_id) for race_id in race_ids}
    state = {variant.variant_id: new_variant_state(variant) for variant in variants}
    fold_rows = []

    for test_index in range(min_train_races, len(race_ids)):
        train_ids = race_ids[:test_index]
        test_id = race_ids[test_index]
        train_races = [race_features[race_id] for race_id in train_ids]
        test_runners = race_features[test_id]
        if not train_races or not test_runners:
            continue

        fold = {
            "race_id": test_id,
            "train_races": len(train_ids),
            "versions": [],
        }
        for variant in variants:
            model = new_model_for_variant(variant)
            model.fit(train_races, epochs=epochs)
            predictions = predict_for_variant(model, variant, test_runners)
            metrics = evaluate_predictions(test_runners, predictions, min_expected_value, stake)
            update_variant_state(state[variant.variant_id], metrics)
            fold["versions"].append(
                {
                    "variant_id": variant.variant_id,
                    "label": variant.label,
                    **metrics,
                }
            )
        fold_rows.append(fold)

    versions = [finalize_variant_state(state[variant.variant_id]) for variant in variants]
    versions.sort(key=lambda row: (row["metrics"]["log_loss"], row["metrics"]["brier_score"]))
    baseline = next((row for row in versions if row["variant_id"] == "baseline"), None)
    best = versions[0] if versions else None
    recommendation = build_recommendation(best, baseline)
    return {
        "summary": {
            "race_count": len(race_ids),
            "folds": len(fold_rows),
            "min_train_races": min_train_races,
            "best_variant_id": best["variant_id"] if best else None,
            "best_label": best["label"] if best else None,
            "recommendation": recommendation,
        },
        "versions": versions,
        "folds": fold_rows[-12:],
    }


def resulted_race_ids(conn: sqlite3.Connection) -> list[str]:
    return [
        row["race_id"]
        for row in fetch_all(
            conn,
            """
            SELECT r.race_id
            FROM races r
            WHERE EXISTS (SELECT 1 FROM results x WHERE x.race_id = r.race_id)
            ORDER BY r.date, r.race_id
            """,
        )
    ]


def new_model_for_variant(variant: ModelVariant) -> RankingModel:
    return RankingModel(
        feature_names=list(variant.feature_names),
        weights={name: 0.0 for name in variant.feature_names},
        means={name: 0.0 for name in variant.feature_names},
        scales={name: 1.0 for name in variant.feature_names},
    )


def predict_for_variant(
    model: RankingModel,
    variant: ModelVariant,
    runners: list[RunnerFeatures],
) -> list[dict[str, Any]]:
    if variant.temperature == 1.0:
        return model.predict_race(runners)

    base_rows = {str(row["horse_id"]): row for row in model.predict_race(runners)}
    scores = [model.score_runner(runner) / max(variant.temperature, 0.01) for runner in runners]
    probabilities = softmax(scores)
    rows = []
    for runner, score, probability in zip(runners, scores, probabilities):
        row = dict(base_rows[runner.horse_id])
        market_probability = runner.features.get("market_implied", 0.0)
        row.update(
            {
                "score": score,
                "win_probability": probability,
                "market_probability": market_probability,
                "value_gap": probability - market_probability,
                "expected_value": probability * runner.latest_win_odds - 1.0
                if runner.latest_win_odds
                else None,
            }
        )
        rows.append(row)
    return sorted(rows, key=lambda item: float(item["win_probability"]), reverse=True)


def evaluate_predictions(
    runners: list[RunnerFeatures],
    predictions: list[dict[str, Any]],
    min_expected_value: float,
    stake: float,
) -> dict[str, Any]:
    winner = next((runner for runner in runners if runner.finish_position == 1), None)
    if not winner or not predictions:
        return new_empty_metrics()

    result_by_horse = {runner.horse_id: int(runner.finish_position or 99) for runner in runners}
    winner_prediction = next(row for row in predictions if row["horse_id"] == winner.horse_id)
    winner_probability = max(float(winner_prediction["win_probability"]), 1e-9)
    top_pick = predictions[0]
    top_pick_finish = result_by_horse.get(str(top_pick["horse_id"]), 99)
    brier = sum(
        (float(row["win_probability"]) - (1.0 if row["horse_id"] == winner.horse_id else 0.0)) ** 2
        for row in predictions
    )
    value_bets = 0
    value_wins = 0
    value_returned = 0.0
    value_staked = 0.0
    for row in predictions:
        ev = row["expected_value"]
        odds = row["latest_win_odds"]
        if ev is None or odds is None or float(ev) < min_expected_value:
            continue
        value_bets += 1
        value_staked += stake
        if row["horse_id"] == winner.horse_id:
            value_wins += 1
            value_returned += stake * float(odds)

    return {
        "races": 1,
        "runners": len(predictions),
        "top_pick_wins": 1 if top_pick_finish == 1 else 0,
        "winner_rank": predictions.index(winner_prediction) + 1,
        "winner_probability": winner_probability,
        "log_loss_total": -math.log(winner_probability),
        "brier_total": brier,
        "value_bets": value_bets,
        "value_wins": value_wins,
        "value_staked": value_staked,
        "value_returned": value_returned,
        "winner_name": winner_prediction.get("display_name") or winner_prediction.get("horse_name"),
        "winner_horse_no": winner_prediction.get("horse_no"),
        "top_pick_name": top_pick.get("display_name") or top_pick.get("horse_name"),
        "top_pick_horse_no": top_pick.get("horse_no"),
        "top_pick_probability": top_pick["win_probability"],
    }


def new_empty_metrics() -> dict[str, Any]:
    return {
        "races": 0,
        "runners": 0,
        "top_pick_wins": 0,
        "winner_rank": 0,
        "winner_probability": 0.0,
        "log_loss_total": 0.0,
        "brier_total": 0.0,
        "value_bets": 0,
        "value_wins": 0,
        "value_staked": 0.0,
        "value_returned": 0.0,
    }


def new_variant_state(variant: ModelVariant) -> dict[str, Any]:
    return {
        "variant_id": variant.variant_id,
        "label": variant.label,
        "description": variant.description,
        "feature_count": len(variant.feature_names),
        "temperature": variant.temperature,
        "races": 0,
        "runners": 0,
        "top_pick_wins": 0,
        "winner_rank_total": 0.0,
        "winner_probability_total": 0.0,
        "log_loss_total": 0.0,
        "brier_total": 0.0,
        "value_bets": 0,
        "value_wins": 0,
        "value_staked": 0.0,
        "value_returned": 0.0,
    }


def update_variant_state(state: dict[str, Any], metrics: dict[str, Any]) -> None:
    state["races"] += int(metrics["races"])
    state["runners"] += int(metrics["runners"])
    state["top_pick_wins"] += int(metrics["top_pick_wins"])
    state["winner_rank_total"] += float(metrics["winner_rank"])
    state["winner_probability_total"] += float(metrics["winner_probability"])
    state["log_loss_total"] += float(metrics["log_loss_total"])
    state["brier_total"] += float(metrics["brier_total"])
    state["value_bets"] += int(metrics["value_bets"])
    state["value_wins"] += int(metrics["value_wins"])
    state["value_staked"] += float(metrics["value_staked"])
    state["value_returned"] += float(metrics["value_returned"])


def finalize_variant_state(state: dict[str, Any]) -> dict[str, Any]:
    races = int(state["races"])
    staked = float(state["value_staked"])
    metrics = {
        "races": races,
        "runners": int(state["runners"]),
        "top_pick_wins": int(state["top_pick_wins"]),
        "top_pick_hit_rate": safe_div(float(state["top_pick_wins"]), races),
        "avg_winner_rank": safe_div(float(state["winner_rank_total"]), races),
        "mean_winner_probability": safe_div(float(state["winner_probability_total"]), races),
        "log_loss": safe_div(float(state["log_loss_total"]), races),
        "brier_score": safe_div(float(state["brier_total"]), races),
        "value_bets": int(state["value_bets"]),
        "value_wins": int(state["value_wins"]),
        "value_hit_rate": safe_div(float(state["value_wins"]), float(state["value_bets"])),
        "value_roi": safe_div(float(state["value_returned"]) - staked, staked),
    }
    verdict = variant_verdict(metrics)
    return {
        "variant_id": state["variant_id"],
        "label": state["label"],
        "description": state["description"],
        "feature_count": state["feature_count"],
        "temperature": state["temperature"],
        "metrics": metrics,
        "verdict": verdict,
    }


def variant_verdict(metrics: dict[str, Any]) -> str:
    if int(metrics["races"]) < 30:
        return "樣本不足，暫作研究參考"
    if float(metrics["log_loss"]) <= 1.8 and float(metrics["brier_score"]) <= 0.8:
        return "可列入候選升級"
    return "暫不建議升級"


def build_recommendation(best: dict[str, Any] | None, baseline: dict[str, Any] | None) -> str:
    if not best:
        return "未有足夠賽果做 walk-forward。"
    if int(best["metrics"]["races"]) < 30:
        return f"{best['label']} 暫時排第一，但樣本少於 30 場，只可當研究訊號。"
    if baseline and best["variant_id"] != "baseline":
        improvement = safe_div(
            float(baseline["metrics"]["log_loss"]) - float(best["metrics"]["log_loss"]),
            float(baseline["metrics"]["log_loss"]),
        )
        if improvement >= 0.02:
            return f"{best['label']} out-of-sample Log Loss 較 Baseline 改善 {improvement:.1%}，可列入升級候選。"
    return "暫時保持 Baseline，繼續累積賽果。"


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0
