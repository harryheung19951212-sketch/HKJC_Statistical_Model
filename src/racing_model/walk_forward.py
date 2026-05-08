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
from .model import RankingModel
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
    race_rows = race_metadata(conn)
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
            metrics["slice_keys"] = slice_keys_for_race(race_rows.get(test_id, {}), test_runners, predictions)
            update_variant_state(state[variant.variant_id], metrics)
            fold["versions"].append(
                {
                    "variant_id": variant.variant_id,
                    "label": variant.label,
                    **public_fold_metrics(metrics),
                }
            )
        fold_rows.append(fold)

    versions = [finalize_variant_state(state[variant.variant_id]) for variant in variants]
    versions.sort(key=lambda row: (row["metrics"]["log_loss"], row["metrics"]["brier_score"]))
    baseline = next((row for row in versions if row["variant_id"] == "baseline"), None)
    best = versions[0] if versions else None
    recommendation = build_recommendation(best, baseline)
    oos_gate = build_oos_slice_gate(best, baseline)
    candidate_calibration_gate = build_oos_calibration_gate(best)
    return {
        "summary": {
            "race_count": len(race_ids),
            "folds": len(fold_rows),
            "min_train_races": min_train_races,
            "best_variant_id": best["variant_id"] if best else None,
            "best_label": best["label"] if best else None,
            "recommendation": recommendation,
            "oos_slice_gate": oos_gate["gate"],
            "oos_slice_gate_label": oos_gate["label"],
            "candidate_calibration_gate": candidate_calibration_gate["gate"],
            "candidate_calibration_gate_label": candidate_calibration_gate["label"],
        },
        "versions": versions,
        "oos_gate": oos_gate,
        "candidate_artifact": {
            "artifact_type": "walk_forward_candidate_oos_evidence",
            "best_variant_id": best["variant_id"] if best else None,
            "baseline_variant_id": baseline["variant_id"] if baseline else None,
            "slice_dimensions": ["場地", "跑道", "途程", "班次", "馬匹數", "市場熱門度"],
            "slice_gate": oos_gate,
            "calibration_gate": candidate_calibration_gate,
        },
        "candidate_calibration_artifact": {
            "artifact_type": "walk_forward_oos_candidate_reliability",
            "best_variant_id": best["variant_id"] if best else None,
            "best_label": best["label"] if best else None,
            "gate": candidate_calibration_gate,
        },
        "candidate_calibration_gate": candidate_calibration_gate,
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


def race_metadata(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        str(row["race_id"]): dict(row)
        for row in fetch_all(
            conn,
            """
            SELECT race_id, date, track, course, distance_m, going, class_rating
            FROM races
            """
        )
    }


def new_model_for_variant(variant: ModelVariant) -> RankingModel:
    return RankingModel(
        feature_names=list(variant.feature_names),
        weights={name: 0.0 for name in variant.feature_names},
        means={name: 0.0 for name in variant.feature_names},
        scales={name: 1.0 for name in variant.feature_names},
        temperature=variant.temperature,
    )


def predict_for_variant(
    model: RankingModel,
    variant: ModelVariant,
    runners: list[RunnerFeatures],
) -> list[dict[str, Any]]:
    previous_temperature = model.temperature
    model.temperature = variant.temperature
    try:
        return model.predict_race(runners)
    finally:
        model.temperature = previous_temperature


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
    top3_hit = any(result_by_horse.get(str(row["horse_id"]), 99) == 1 for row in predictions[:3])
    brier = sum(
        (float(row["win_probability"]) - (1.0 if row["horse_id"] == winner.horse_id else 0.0)) ** 2
        for row in predictions
    )
    calibration_points = [
        {
            "probability": float(row["win_probability"]),
            "outcome": 1.0 if row["horse_id"] == winner.horse_id else 0.0,
        }
        for row in predictions
    ]
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
        "top3_hits": 1 if top3_hit else 0,
        "winner_rank": predictions.index(winner_prediction) + 1,
        "winner_probability": winner_probability,
        "log_loss_total": -math.log(winner_probability),
        "brier_total": brier,
        "calibration_points": calibration_points,
        "value_bets": value_bets,
        "value_wins": value_wins,
        "value_staked": value_staked,
        "value_returned": value_returned,
        "value_profit": value_returned - value_staked,
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
        "top3_hits": 0,
        "winner_rank": 0,
        "winner_probability": 0.0,
        "log_loss_total": 0.0,
        "brier_total": 0.0,
        "calibration_points": [],
        "slice_keys": [],
        "value_bets": 0,
        "value_wins": 0,
        "value_staked": 0.0,
        "value_returned": 0.0,
        "value_profit": 0.0,
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
        "top3_hits": 0,
        "winner_rank_total": 0.0,
        "winner_probability_total": 0.0,
        "log_loss_total": 0.0,
        "brier_total": 0.0,
        "calibration_bins": new_calibration_bins(),
        "slices": {},
        "value_bets": 0,
        "value_wins": 0,
        "value_staked": 0.0,
        "value_returned": 0.0,
        "value_profit": 0.0,
        "equity": 0.0,
        "peak_equity": 0.0,
        "max_drawdown": 0.0,
    }


def update_variant_state(state: dict[str, Any], metrics: dict[str, Any]) -> None:
    state["races"] += int(metrics["races"])
    state["runners"] += int(metrics["runners"])
    state["top_pick_wins"] += int(metrics["top_pick_wins"])
    state["top3_hits"] += int(metrics.get("top3_hits", 0))
    state["winner_rank_total"] += float(metrics["winner_rank"])
    state["winner_probability_total"] += float(metrics["winner_probability"])
    state["log_loss_total"] += float(metrics["log_loss_total"])
    state["brier_total"] += float(metrics["brier_total"])
    state["value_bets"] += int(metrics["value_bets"])
    state["value_wins"] += int(metrics["value_wins"])
    state["value_staked"] += float(metrics["value_staked"])
    state["value_returned"] += float(metrics["value_returned"])
    state["value_profit"] += float(metrics["value_profit"])
    state["equity"] += float(metrics["value_profit"])
    state["peak_equity"] = max(float(state["peak_equity"]), float(state["equity"]))
    state["max_drawdown"] = max(
        float(state["max_drawdown"]),
        float(state["peak_equity"]) - float(state["equity"]),
    )
    for point in metrics.get("calibration_points", []):
        add_calibration_point(
            state["calibration_bins"],
            float(point.get("probability", 0.0) or 0.0),
            float(point.get("outcome", 0.0) or 0.0),
        )
    for slice_key in metrics.get("slice_keys", []):
        update_slice_state(state["slices"], slice_key, metrics)


def finalize_variant_state(state: dict[str, Any]) -> dict[str, Any]:
    races = int(state["races"])
    staked = float(state["value_staked"])
    metrics = {
        "races": races,
        "runners": int(state["runners"]),
        "top_pick_wins": int(state["top_pick_wins"]),
        "top_pick_hit_rate": safe_div(float(state["top_pick_wins"]), races),
        "top3_hit_rate": safe_div(float(state["top3_hits"]), races),
        "avg_winner_rank": safe_div(float(state["winner_rank_total"]), races),
        "mean_winner_probability": safe_div(float(state["winner_probability_total"]), races),
        "log_loss": safe_div(float(state["log_loss_total"]), races),
        "brier_score": safe_div(float(state["brier_total"]), races),
        "value_bets": int(state["value_bets"]),
        "value_wins": int(state["value_wins"]),
        "value_hit_rate": safe_div(float(state["value_wins"]), float(state["value_bets"])),
        "value_profit": float(state["value_profit"]),
        "value_roi": safe_div(float(state["value_returned"]) - staked, staked),
        "max_drawdown": float(state["max_drawdown"]),
        "max_drawdown_pct": safe_div(float(state["max_drawdown"]), staked),
        "clv_status": "需要分離下注時賠率同收市前賠率後先可計算",
    }
    verdict = variant_verdict(metrics)
    return {
        "variant_id": state["variant_id"],
        "label": state["label"],
        "description": state["description"],
        "feature_count": state["feature_count"],
        "temperature": state["temperature"],
        "metrics": metrics,
        "calibration_bins": finalize_calibration_bins(state["calibration_bins"]),
        "slice_scorecard": finalize_slice_scorecard(state["slices"]),
        "verdict": verdict,
    }


def public_fold_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metrics.items()
        if key not in {"calibration_points", "slice_keys"}
    }


def slice_keys_for_race(
    race: dict[str, Any],
    runners: list[RunnerFeatures],
    predictions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    track = str(race.get("track") or "unknown")
    course = str(race.get("course") or "unknown")
    class_rating = str(race.get("class_rating") or "unknown")
    distance = int(race.get("distance_m") or 0)
    field_size = len(runners)
    market_odds = [float(runner.latest_win_odds) for runner in runners if runner.latest_win_odds]
    favourite_odds = min(market_odds) if market_odds else None
    return [
        slice_key("track", "場地", track),
        slice_key("course", "跑道", course),
        slice_key("distance", "途程", distance_bucket(distance)),
        slice_key("class", "班次", class_rating),
        slice_key("field_size", "馬匹數", field_size_bucket(field_size)),
        slice_key("market_favourite", "市場熱門度", odds_bucket(favourite_odds)),
    ]


def slice_key(dimension: str, dimension_label: str, value: str) -> dict[str, str]:
    return {
        "slice_id": f"{dimension}:{value}",
        "dimension": dimension,
        "dimension_label": dimension_label,
        "value": value,
        "label": f"{dimension_label}：{value}",
    }


def distance_bucket(distance: int) -> str:
    if distance <= 0:
        return "未知"
    if distance <= 1200:
        return "短途 <=1200米"
    if distance <= 1600:
        return "中短途 1400-1600米"
    if distance <= 2000:
        return "中長途 1800-2000米"
    return "長途 >=2200米"


def field_size_bucket(field_size: int) -> str:
    if field_size <= 0:
        return "未知"
    if field_size <= 8:
        return "細場 <=8匹"
    if field_size <= 12:
        return "中場 9-12匹"
    return "大場 >=13匹"


def odds_bucket(odds: float | None) -> str:
    if odds is None:
        return "無市場賠率"
    if odds <= 3.0:
        return "明顯熱門 <=3.0"
    if odds <= 6.0:
        return "中價熱門 3.1-6.0"
    return "冷門主導 >6.0"


def new_calibration_bins() -> list[dict[str, Any]]:
    edges = [(0.0, 0.05), (0.05, 0.1), (0.1, 0.15), (0.15, 0.2), (0.2, 0.3), (0.3, 0.5), (0.5, 1.01)]
    return [
        {
            "low": low,
            "high": high,
            "label": f"{int(low * 100)}-{int(min(high, 1.0) * 100)}%",
            "count": 0,
            "expected": 0.0,
            "actual": 0.0,
        }
        for low, high in edges
    ]


def add_calibration_point(bins: list[dict[str, Any]], probability: float, outcome: float) -> None:
    for item in bins:
        if float(item["low"]) <= probability < float(item["high"]):
            item["count"] += 1
            item["expected"] += probability
            item["actual"] += outcome
            return


def finalize_calibration_bins(bins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in bins:
        count = int(item["count"])
        avg_prediction = safe_div(float(item["expected"]), count)
        observed_rate = safe_div(float(item["actual"]), count)
        rows.append(
            {
                "label": item["label"],
                "count": count,
                "avg_prediction": avg_prediction,
                "observed_rate": observed_rate,
                "gap": observed_rate - avg_prediction if count else 0.0,
            }
        )
    return rows


def update_slice_state(slices: dict[str, dict[str, Any]], slice_key: dict[str, str], metrics: dict[str, Any]) -> None:
    row = slices.setdefault(
        slice_key["slice_id"],
        {
            **slice_key,
            "races": 0,
            "runners": 0,
            "top_pick_wins": 0,
            "top3_hits": 0,
            "winner_rank_total": 0.0,
            "winner_probability_total": 0.0,
            "log_loss_total": 0.0,
            "brier_total": 0.0,
            "value_bets": 0,
            "value_wins": 0,
            "value_staked": 0.0,
            "value_returned": 0.0,
            "value_profit": 0.0,
        },
    )
    row["races"] += int(metrics["races"])
    row["runners"] += int(metrics["runners"])
    row["top_pick_wins"] += int(metrics["top_pick_wins"])
    row["top3_hits"] += int(metrics.get("top3_hits", 0))
    row["winner_rank_total"] += float(metrics["winner_rank"])
    row["winner_probability_total"] += float(metrics["winner_probability"])
    row["log_loss_total"] += float(metrics["log_loss_total"])
    row["brier_total"] += float(metrics["brier_total"])
    row["value_bets"] += int(metrics["value_bets"])
    row["value_wins"] += int(metrics["value_wins"])
    row["value_staked"] += float(metrics["value_staked"])
    row["value_returned"] += float(metrics["value_returned"])
    row["value_profit"] += float(metrics["value_profit"])


def finalize_slice_scorecard(slices: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [finalize_slice(row) for row in slices.values()]
    return sorted(rows, key=lambda row: (str(row["dimension"]), -int(row["races"]), str(row["value"])))


def finalize_slice(row: dict[str, Any]) -> dict[str, Any]:
    races = int(row["races"])
    staked = float(row["value_staked"])
    return {
        "slice_id": row["slice_id"],
        "dimension": row["dimension"],
        "dimension_label": row["dimension_label"],
        "value": row["value"],
        "label": row["label"],
        "races": races,
        "runners": int(row["runners"]),
        "top_pick_hit_rate": safe_div(float(row["top_pick_wins"]), races),
        "top3_hit_rate": safe_div(float(row["top3_hits"]), races),
        "avg_winner_rank": safe_div(float(row["winner_rank_total"]), races),
        "mean_winner_probability": safe_div(float(row["winner_probability_total"]), races),
        "log_loss": safe_div(float(row["log_loss_total"]), races),
        "brier_score": safe_div(float(row["brier_total"]), races),
        "value_bets": int(row["value_bets"]),
        "value_hit_rate": safe_div(float(row["value_wins"]), float(row["value_bets"])),
        "value_profit": float(row["value_profit"]),
        "value_roi": safe_div(float(row["value_returned"]) - staked, staked),
    }


def build_oos_slice_gate(
    best: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    min_slice_races: int = 5,
    max_log_loss_regression: float = 0.12,
    max_brier_regression: float = 0.05,
    max_top_pick_regression: float = 0.12,
) -> dict[str, Any]:
    if not best:
        return oos_gate_payload("no_data", "未有 OOS 分片證據", [], min_slice_races)
    if best.get("variant_id") == "baseline":
        return oos_gate_payload("pass", "最佳版本仍然係 Baseline，分片 gate 不需要阻擋。", [], min_slice_races)
    if not baseline:
        return oos_gate_payload("unverified", "未有 Baseline 分片可比較，禁止自動升級。", [], min_slice_races)

    baseline_by_id = {row["slice_id"]: row for row in baseline.get("slice_scorecard", [])}
    compared = []
    for row in best.get("slice_scorecard", []):
        base = baseline_by_id.get(row.get("slice_id"))
        if not base:
            continue
        compared.append(compare_slice(row, base, min_slice_races, max_log_loss_regression, max_brier_regression, max_top_pick_regression))

    eligible = [row for row in compared if row["gate"] != "sample_small"]
    blocked = [row for row in eligible if row["gate"] == "blocked"]
    if blocked:
        message = f"{len(blocked)} 個重要分片退化，candidate 暫停升級。"
        return oos_gate_payload("blocked", message, compared, min_slice_races)
    if not eligible:
        return oos_gate_payload("unverified", "分片樣本未達門檻，candidate 暫時只可觀察不可升級。", compared, min_slice_races)
    return oos_gate_payload("pass", "主要分片未見明顯退化，可進入下一個 gate。", compared, min_slice_races)


def compare_slice(
    row: dict[str, Any],
    baseline: dict[str, Any],
    min_slice_races: int,
    max_log_loss_regression: float,
    max_brier_regression: float,
    max_top_pick_regression: float,
) -> dict[str, Any]:
    best_races = int(row.get("races", 0) or 0)
    baseline_races = int(baseline.get("races", 0) or 0)
    log_loss_delta = float(row.get("log_loss", 0.0) or 0.0) - float(baseline.get("log_loss", 0.0) or 0.0)
    brier_delta = float(row.get("brier_score", 0.0) or 0.0) - float(baseline.get("brier_score", 0.0) or 0.0)
    top_pick_delta = float(row.get("top_pick_hit_rate", 0.0) or 0.0) - float(baseline.get("top_pick_hit_rate", 0.0) or 0.0)
    roi_delta = float(row.get("value_roi", 0.0) or 0.0) - float(baseline.get("value_roi", 0.0) or 0.0)
    gate = "pass"
    reasons = []
    if best_races < min_slice_races or baseline_races < min_slice_races:
        gate = "sample_small"
        reasons.append("分片樣本不足")
    else:
        if log_loss_delta > max_log_loss_regression:
            reasons.append("Log Loss 退化")
        if brier_delta > max_brier_regression:
            reasons.append("Brier 退化")
        if top_pick_delta < -max_top_pick_regression:
            reasons.append("首選命中退化")
        if reasons:
            gate = "blocked"
    return {
        "slice_id": row.get("slice_id"),
        "dimension": row.get("dimension"),
        "dimension_label": row.get("dimension_label"),
        "value": row.get("value"),
        "label": row.get("label"),
        "gate": gate,
        "reason": "、".join(reasons) if reasons else "通過",
        "races": best_races,
        "baseline_races": baseline_races,
        "log_loss": row.get("log_loss"),
        "baseline_log_loss": baseline.get("log_loss"),
        "log_loss_delta": log_loss_delta,
        "brier_score": row.get("brier_score"),
        "baseline_brier_score": baseline.get("brier_score"),
        "brier_delta": brier_delta,
        "top_pick_hit_rate": row.get("top_pick_hit_rate"),
        "baseline_top_pick_hit_rate": baseline.get("top_pick_hit_rate"),
        "top_pick_delta": top_pick_delta,
        "value_roi": row.get("value_roi"),
        "baseline_value_roi": baseline.get("value_roi"),
        "roi_delta": roi_delta,
    }


def oos_gate_payload(gate: str, message: str, slices: list[dict[str, Any]], min_slice_races: int) -> dict[str, Any]:
    labels = {
        "pass": "分片 OOS 通過",
        "blocked": "分片 OOS 阻擋",
        "unverified": "分片 OOS 樣本不足",
        "no_data": "未有 OOS 分片",
    }
    blocked = [row for row in slices if row.get("gate") == "blocked"]
    eligible = [row for row in slices if row.get("gate") != "sample_small"]
    return {
        "gate": gate,
        "label": labels.get(gate, gate),
        "message": message,
        "min_slice_races": min_slice_races,
        "eligible_slices": len(eligible),
        "blocked_slices": len(blocked),
        "sample_small_slices": sum(1 for row in slices if row.get("gate") == "sample_small"),
        "slices": sorted(
            slices,
            key=lambda row: (
                0 if row.get("gate") == "blocked" else 1 if row.get("gate") == "pass" else 2,
                -int(row.get("races", 0) or 0),
                str(row.get("label") or ""),
            ),
        )[:24],
    }


def build_oos_calibration_gate(
    best: dict[str, Any] | None,
    min_points: int = 30,
    min_bin_count: int = 8,
    max_abs_gap: float = 0.15,
    max_overconfidence_gap: float = 0.08,
) -> dict[str, Any]:
    if not best:
        return oos_calibration_gate_payload("no_data", "未有候選版本可靠度證據。", [], min_points, min_bin_count)

    bins = list(best.get("calibration_bins", []))
    total = sum(int(row.get("count", 0) or 0) for row in bins)
    compared = [
        compare_calibration_bin(row, min_bin_count, max_abs_gap, max_overconfidence_gap)
        for row in bins
    ]
    eligible = [row for row in compared if row["gate"] != "sample_small"]
    blocked = [row for row in eligible if row["gate"] == "blocked"]
    if total < min_points:
        return oos_calibration_gate_payload(
            "unverified",
            f"候選 OOS 校準點只有 {total} 個，未足 {min_points} 個。",
            compared,
            min_points,
            min_bin_count,
        )
    if not eligible:
        return oos_calibration_gate_payload(
            "unverified",
            f"候選 OOS 未有分桶達到 {min_bin_count} 個樣本。",
            compared,
            min_points,
            min_bin_count,
        )
    if blocked:
        worst = max(blocked, key=lambda row: abs(float(row.get("gap") or 0.0)))
        return oos_calibration_gate_payload(
            "blocked",
            f"候選 OOS 校準未過關：{worst.get('label')} 分桶偏差 {float(worst.get('gap') or 0.0) * 100:.1f}%。",
            compared,
            min_points,
            min_bin_count,
        )
    return oos_calibration_gate_payload(
        "pass",
        "候選 OOS reliability bins 未見明顯失準，可進入下一個 gate。",
        compared,
        min_points,
        min_bin_count,
    )


def compare_calibration_bin(
    row: dict[str, Any],
    min_bin_count: int,
    max_abs_gap: float,
    max_overconfidence_gap: float,
) -> dict[str, Any]:
    count = int(row.get("count", 0) or 0)
    gap = float(row.get("gap") or 0.0)
    avg_prediction = float(row.get("avg_prediction") or 0.0)
    gate = "pass"
    reason = "通過"
    if count < min_bin_count:
        gate = "sample_small"
        reason = "分桶樣本不足"
    elif avg_prediction >= 0.15 and gap <= -max_overconfidence_gap:
        gate = "blocked"
        reason = "候選過度自信"
    elif abs(gap) >= max_abs_gap:
        gate = "blocked"
        reason = "候選可靠度偏差過大"
    return {
        "label": row.get("label"),
        "gate": gate,
        "reason": reason,
        "count": count,
        "avg_prediction": row.get("avg_prediction"),
        "observed_rate": row.get("observed_rate"),
        "gap": gap,
    }


def oos_calibration_gate_payload(
    gate: str,
    message: str,
    bins: list[dict[str, Any]],
    min_points: int,
    min_bin_count: int,
) -> dict[str, Any]:
    labels = {
        "pass": "候選校準通過",
        "blocked": "候選校準阻擋",
        "unverified": "候選校準樣本不足",
        "no_data": "未有候選校準",
    }
    blocked = [row for row in bins if row.get("gate") == "blocked"]
    eligible = [row for row in bins if row.get("gate") != "sample_small"]
    total = sum(int(row.get("count", 0) or 0) for row in bins)
    return {
        "gate": gate,
        "label": labels.get(gate, gate),
        "message": message,
        "points": total,
        "min_points": min_points,
        "min_bin_count": min_bin_count,
        "eligible_bins": len(eligible),
        "blocked_bins": len(blocked),
        "sample_small_bins": sum(1 for row in bins if row.get("gate") == "sample_small"),
        "bins": sorted(
            bins,
            key=lambda row: (
                0 if row.get("gate") == "blocked" else 1 if row.get("gate") == "pass" else 2,
                -abs(float(row.get("gap") or 0.0)),
                str(row.get("label") or ""),
            ),
        ),
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
