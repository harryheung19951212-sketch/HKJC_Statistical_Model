from __future__ import annotations

import sqlite3
from typing import Any

from .features import build_race_features
from .model import RankingModel
from .model_compare import ABILITY_FEATURES, dual_model_backtest, masked_model


MARKET_BLEND_WEIGHT = 0.35


def adaptive_prediction_policy(conn: sqlite3.Connection, model: RankingModel) -> dict[str, Any]:
    report = dual_model_backtest(conn, model)
    summary = report.get("summary", {})
    recommendation = report.get("recommendation", {})
    verdict = str(recommendation.get("verdict") or "mixed")
    races = int(summary.get("races") or 0)
    if races < 5:
        mode = "baseline"
        weight = 0.0
        reason = "已完賽樣本不足，暫時使用基礎模型。"
    elif verdict == "ability_leads":
        mode = "ability"
        weight = 0.0
        reason = "雙軌回測顯示純能力軌較穩，預測暫時降低賠率訊號影響。"
    elif verdict == "market_leads":
        mode = "market_blend"
        weight = MARKET_BLEND_WEIGHT
        reason = "雙軌回測顯示市場融合軌較準，預測會按比例吸收最新賠率 implied probability。"
    else:
        mode = "baseline"
        weight = 0.0
        reason = "雙軌回測未分高下，保持基礎市場融合模型。"
    return {
        "mode": mode,
        "market_blend_weight": weight,
        "reason": reason,
        "races": races,
        "ability_win_rate": summary.get("ability_win_rate"),
        "market_win_rate": summary.get("market_win_rate"),
        "verdict": verdict,
    }


def adaptive_predict_race(
    conn: sqlite3.Connection,
    model: RankingModel,
    race_id: str,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = policy or adaptive_prediction_policy(conn, model)
    runners = build_race_features(conn, race_id)
    active_model = masked_model(model, ABILITY_FEATURES) if policy.get("mode") == "ability" else model
    rows = active_model.predict_race(runners)
    if policy.get("mode") == "market_blend":
        rows = blend_market_probabilities(rows, float(policy.get("market_blend_weight") or MARKET_BLEND_WEIGHT))
    for row in rows:
        row["prediction_mode"] = policy.get("mode")
        row["prediction_policy"] = policy.get("reason")
    return {"predictions": rows, "policy": policy}


def blend_market_probabilities(rows: list[dict[str, Any]], market_weight: float) -> list[dict[str, Any]]:
    if not rows:
        return rows
    market_weight = min(max(market_weight, 0.0), 0.8)
    model_weight = 1.0 - market_weight
    win_market = normalized_probability(rows, "market_probability", target_sum=1.0)
    place_market = normalized_probability(rows, "place_market_probability", target_sum=min(3.0, float(len(rows))))
    blended = []
    for row in rows:
        item = dict(row)
        horse_id = str(item.get("horse_id"))
        original_win = float(item.get("win_probability") or 0.0)
        original_top3 = float(item.get("top3_probability") or 0.0)
        item["raw_model_win_probability"] = original_win
        item["raw_model_top3_probability"] = original_top3
        item["win_probability"] = model_weight * original_win + market_weight * win_market.get(horse_id, original_win)
        item["top3_probability"] = min(
            1.0,
            model_weight * original_top3 + market_weight * place_market.get(horse_id, original_top3),
        )
        recalculate_value_fields(item)
        blended.append(item)
    return sorted(blended, key=lambda row: float(row.get("win_probability") or 0.0), reverse=True)


def normalized_probability(rows: list[dict[str, Any]], key: str, target_sum: float) -> dict[str, float]:
    raw = {
        str(row.get("horse_id")): max(float(row.get(key) or 0.0), 0.0)
        for row in rows
    }
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {horse_id: min(value / total * target_sum, 1.0) for horse_id, value in raw.items()}


def recalculate_value_fields(row: dict[str, Any]) -> None:
    win_probability = float(row.get("win_probability") or 0.0)
    top3_probability = float(row.get("top3_probability") or 0.0)
    win_odds = safe_float(row.get("latest_win_odds"))
    place_odds = safe_float(row.get("place_odds") or row.get("latest_place_odds"))
    market_probability = float(row.get("market_probability") or 0.0)
    place_market_probability = float(row.get("place_market_probability") or 0.0)
    row["value_gap"] = win_probability - market_probability
    row["top3_value_gap"] = top3_probability - place_market_probability
    row["expected_value"] = win_probability * win_odds - 1.0 if win_odds and win_odds > 1 else None
    row["top3_expected_value"] = top3_probability * place_odds - 1.0 if place_odds and place_odds > 1 else None


def safe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
