from __future__ import annotations

import sqlite3
from typing import Any

from .pool_replay import pool_replay_report
from .walk_forward import run_walk_forward_versions


STATUS_LABELS = {
    "pass": "通過",
    "warn": "要覆核",
    "block": "阻擋升級",
    "unverified": "樣本不足",
}

GATE_LABELS = {
    "pass": "可升級觀察",
    "review": "要人工覆核",
    "blocked": "暫停升級",
    "unverified": "樣本不足",
    "no_data": "未有資料",
}

SECTION_LABELS = {
    "accuracy": "命中 / 機率準確度",
    "calibration": "校準可靠度",
    "pool": "分彩池下注表現",
    "slice": "場地 / 距離 / 班次分片",
    "risk": "回撤 / Sharpe-like 風險",
}

SLICE_DIMENSION_LABELS = {
    "track": "場地",
    "course": "賽道",
    "distance": "途程",
    "class": "班次",
    "field_size": "馬匹數",
    "market_favourite": "熱門分布",
}


def promotion_scorecard(
    conn: sqlite3.Connection,
    min_train_races: int = 1,
    epochs: int = 80,
    min_expected_value: float = 0.05,
    stake: float = 10.0,
) -> dict[str, Any]:
    model_versions = run_walk_forward_versions(
        conn,
        min_train_races=min_train_races,
        epochs=epochs,
        min_expected_value=min_expected_value,
        stake=stake,
    )
    pool_replay = pool_replay_report(conn)
    return build_promotion_scorecard(model_versions, pool_replay)


def build_promotion_scorecard(
    model_versions: dict[str, Any],
    pool_replay: dict[str, Any],
    model_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = model_versions.get("summary", {}) if isinstance(model_versions, dict) else {}
    versions = model_versions.get("versions", []) if isinstance(model_versions, dict) else []
    best = find_best_version(summary, versions)
    baseline = next((row for row in versions if row.get("variant_id") == "baseline"), None)
    metrics = overall_metrics(summary, best, baseline)
    oos_gate = model_versions.get("oos_gate") if isinstance(model_versions.get("oos_gate"), dict) else {}
    sections = [
        accuracy_section(metrics, best, baseline),
        calibration_section(best),
        pool_section(pool_replay),
        slice_section(oos_gate),
        risk_section(metrics, best, baseline),
    ]
    gate = promotion_gate_from_sections(sections)
    registry_summary = model_registry.get("summary", {}) if isinstance(model_registry, dict) else {}
    result_summary = {
        "gate": gate,
        "gate_label": GATE_LABELS.get(gate, gate),
        "message": promotion_message(gate, sections, metrics),
        "race_count": metrics["race_count"],
        "folds": metrics["folds"],
        "best_variant_id": metrics["best_variant_id"],
        "best_label": metrics["best_label"],
        "best_multi_objective_score": metrics["best_multi_objective_score"],
        "baseline_variant_id": metrics["baseline_variant_id"],
        "blocked_sections": sum(1 for row in sections if row["status"] == "block"),
        "warning_sections": sum(1 for row in sections if row["status"] == "warn"),
        "unverified_sections": sum(1 for row in sections if row["status"] == "unverified"),
        "metric_count": 10,
        "latest_registry_gate": registry_summary.get("latest_gate"),
        "latest_registry_gate_label": registry_summary.get("latest_gate_label"),
    }
    return {
        "summary": result_summary,
        "overall_metrics": metrics,
        "sections": sections,
        "pool_scorecard": pool_scorecard(pool_replay),
        "slice_scorecard": slice_scorecard(best, baseline),
        "oos_gate": oos_gate,
        "calibration_bins": best.get("calibration_bins", []) if isinstance(best, dict) else [],
    }


def find_best_version(summary: dict[str, Any], versions: list[dict[str, Any]]) -> dict[str, Any] | None:
    best_id = summary.get("best_variant_id")
    if best_id:
        found = next((row for row in versions if row.get("variant_id") == best_id), None)
        if found:
            return found
    return versions[0] if versions else None


def overall_metrics(
    summary: dict[str, Any],
    best: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    best_metrics = best.get("metrics", {}) if isinstance(best, dict) else {}
    baseline_metrics = baseline.get("metrics", {}) if isinstance(baseline, dict) else {}
    best_profit = optional_float(best_metrics.get("value_profit"))
    baseline_profit = optional_float(baseline_metrics.get("value_profit"))
    best_drawdown = optional_float(best_metrics.get("max_drawdown"))
    baseline_drawdown = optional_float(baseline_metrics.get("max_drawdown"))
    return {
        "race_count": int(summary.get("race_count", 0) or 0),
        "folds": int(summary.get("folds", 0) or 0),
        "best_variant_id": best.get("variant_id") if isinstance(best, dict) else None,
        "best_label": best.get("label") if isinstance(best, dict) else None,
        "baseline_variant_id": baseline.get("variant_id") if isinstance(baseline, dict) else None,
        "baseline_log_loss": optional_float(baseline_metrics.get("log_loss")),
        "best_log_loss": optional_float(best_metrics.get("log_loss")),
        "log_loss_delta": delta(best_metrics, baseline_metrics, "log_loss"),
        "log_loss_improvement_pct": improvement_pct(baseline_metrics.get("log_loss"), best_metrics.get("log_loss")),
        "baseline_brier_score": optional_float(baseline_metrics.get("brier_score")),
        "best_brier_score": optional_float(best_metrics.get("brier_score")),
        "brier_delta": delta(best_metrics, baseline_metrics, "brier_score"),
        "baseline_top_pick_hit_rate": optional_float(baseline_metrics.get("top_pick_hit_rate")),
        "best_top_pick_hit_rate": optional_float(best_metrics.get("top_pick_hit_rate")),
        "top_pick_delta": delta(best_metrics, baseline_metrics, "top_pick_hit_rate"),
        "baseline_top3_hit_rate": optional_float(baseline_metrics.get("top3_hit_rate")),
        "best_top3_hit_rate": optional_float(best_metrics.get("top3_hit_rate")),
        "top3_delta": delta(best_metrics, baseline_metrics, "top3_hit_rate"),
        "baseline_value_roi": optional_float(baseline_metrics.get("value_roi")),
        "best_value_roi": optional_float(best_metrics.get("value_roi")),
        "roi_delta": delta(best_metrics, baseline_metrics, "value_roi"),
        "baseline_value_profit": baseline_profit,
        "best_value_profit": best_profit,
        "baseline_max_drawdown": baseline_drawdown,
        "best_max_drawdown": best_drawdown,
        "drawdown_delta": nullable_delta(best_drawdown, baseline_drawdown),
        "baseline_return_to_drawdown": return_to_drawdown(baseline_profit, baseline_drawdown),
        "best_return_to_drawdown": return_to_drawdown(best_profit, best_drawdown),
        "baseline_multi_objective_score": optional_float(baseline.get("multi_objective_score")) if isinstance(baseline, dict) else None,
        "best_multi_objective_score": optional_float(best.get("multi_objective_score")) if isinstance(best, dict) else None,
    }


def accuracy_section(
    metrics: dict[str, Any],
    best: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    if not best:
        return section("accuracy", "unverified", "未有 out-of-sample 模型版本。", {"min_folds": 30})
    if metrics["folds"] < 30:
        return section(
            "accuracy",
            "unverified",
            f"現時只有 {metrics['folds']} 個 walk-forward fold，未足 30 個升級門檻。",
            {"min_folds": 30},
        )
    if not baseline:
        return section("accuracy", "unverified", "未有 baseline 可比較。", {"min_folds": 30})

    log_loss_delta = metrics.get("log_loss_delta")
    brier_delta = metrics.get("brier_delta")
    top_pick_delta = metrics.get("top_pick_delta")
    if greater_than(log_loss_delta, 0.02) or greater_than(brier_delta, 0.02) or less_than(top_pick_delta, -0.05):
        status = "block"
        message = "準確度相對 baseline 明顯退步。"
    elif less_or_equal(log_loss_delta, 0.0) and less_or_equal(brier_delta, 0.0) and not less_than(top_pick_delta, -0.02):
        status = "pass"
        message = "Log loss / Brier 未比 baseline 差，首選命中亦保持。"
    else:
        status = "warn"
        message = "整體準確度接近 baseline，要配合其他分片覆核。"
    return section(
        "accuracy",
        status,
        message,
        {
            "log_loss_delta": log_loss_delta,
            "brier_delta": brier_delta,
            "top_pick_delta": top_pick_delta,
            "top3_delta": metrics.get("top3_delta"),
        },
    )


def calibration_section(best: dict[str, Any] | None, min_points: int = 30) -> dict[str, Any]:
    bins = best.get("calibration_bins", []) if isinstance(best, dict) else []
    total = sum(int(row.get("count", 0) or 0) for row in bins)
    eligible = [row for row in bins if int(row.get("count", 0) or 0) >= 3]
    max_gap = max((abs(float(row.get("gap", 0.0) or 0.0)) for row in eligible), default=None)
    if total < min_points or max_gap is None:
        return section(
            "calibration",
            "unverified",
            f"校準點只有 {total} 個，未足 {min_points} 個可靠分桶樣本。",
            {"points": total, "min_points": min_points, "max_abs_gap": max_gap},
        )
    if max_gap > 0.16:
        status = "block"
        message = "有分桶實際命中率與預測機率差距過大。"
    elif max_gap > 0.08:
        status = "warn"
        message = "校準可用，但要留意高差距分桶。"
    else:
        status = "pass"
        message = "校準分桶誤差在可接受範圍。"
    return section("calibration", status, message, {"points": total, "min_points": min_points, "max_abs_gap": max_gap})


def pool_section(pool_replay: dict[str, Any], min_executed: int = 20) -> dict[str, Any]:
    summary = pool_replay.get("summary", {}) if isinstance(pool_replay, dict) else {}
    executed = int(summary.get("executed", 0) or 0)
    tickets = int(summary.get("tickets", 0) or 0)
    execution_roi = optional_float(summary.get("execution_roi"))
    execution_drawdown = optional_float(summary.get("execution_max_drawdown"))
    invalid = int(summary.get("stale_price", 0) or 0) + int(summary.get("negative_ev_at_execution", 0) or 0)
    invalid_rate = safe_div(invalid, executed) if executed else None
    if tickets == 0:
        status = "unverified"
        message = "未有已保存投注建議，分彩池 ROI 未可驗證。"
    elif executed < min_executed or execution_roi is None:
        status = "unverified"
        message = f"下注時確認樣本 {executed}/{min_executed}，未足升級門檻。"
    elif execution_roi < 0 or (invalid_rate is not None and invalid_rate > 0.25):
        status = "block"
        message = "下注時 ROI 或有效執行率未過關。"
    elif execution_roi >= 0:
        status = "pass"
        message = "下注時 ROI 暫時非負，執行樣本可納入升級評分。"
    else:
        status = "warn"
        message = "分彩池結果接近門檻，要等待更多對數。"
    return section(
        "pool",
        status,
        message,
        {
            "tickets": tickets,
            "reconciled": int(summary.get("reconciled", 0) or 0),
            "executed": executed,
            "min_executed": min_executed,
            "execution_roi": execution_roi,
            "execution_max_drawdown": execution_drawdown,
            "invalid_execution_rate": invalid_rate,
        },
    )


def slice_section(oos_gate: dict[str, Any]) -> dict[str, Any]:
    gate = str(oos_gate.get("gate") or "no_data")
    if gate == "blocked":
        status = "block"
    elif gate == "pass":
        status = "pass"
    elif gate in {"unverified", "no_data"}:
        status = "unverified"
    else:
        status = "warn"
    return section(
        "slice",
        status,
        str(oos_gate.get("message") or "未有分片 gate 資料。"),
        {
            "gate": gate,
            "eligible_slices": int(oos_gate.get("eligible_slices", 0) or 0),
            "blocked_slices": int(oos_gate.get("blocked_slices", 0) or 0),
            "sample_small_slices": int(oos_gate.get("sample_small_slices", 0) or 0),
        },
    )


def risk_section(
    metrics: dict[str, Any],
    best: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    if not best:
        return section("risk", "unverified", "未有候選模型風險數據。", {})
    if metrics["folds"] < 30:
        return section(
            "risk",
            "unverified",
            "回撤 / Sharpe-like 指標要累積更多 OOS fold 才穩定。",
            {
                "best_max_drawdown": metrics.get("best_max_drawdown"),
                "best_return_to_drawdown": metrics.get("best_return_to_drawdown"),
            },
        )
    if not baseline:
        return section("risk", "unverified", "未有 baseline 回撤可比較。", {})

    roi_delta = metrics.get("roi_delta")
    drawdown_delta = metrics.get("drawdown_delta")
    baseline_drawdown = metrics.get("baseline_max_drawdown")
    best_drawdown = metrics.get("best_max_drawdown")
    if less_than(roi_delta, -0.05) or drawdown_regressed(best_drawdown, baseline_drawdown, 1.25):
        status = "block"
        message = "ROI 退步或最大回撤擴大太多。"
    elif not less_than(roi_delta, 0.0) and not drawdown_regressed(best_drawdown, baseline_drawdown, 1.1):
        status = "pass"
        message = "ROI 未差過 baseline，回撤未明顯放大。"
    else:
        status = "warn"
        message = "風險回報接近 baseline，要人工覆核。"
    return section(
        "risk",
        status,
        message,
        {
            "roi_delta": roi_delta,
            "drawdown_delta": drawdown_delta,
            "best_max_drawdown": best_drawdown,
            "baseline_max_drawdown": baseline_drawdown,
            "best_return_to_drawdown": metrics.get("best_return_to_drawdown"),
            "baseline_return_to_drawdown": metrics.get("baseline_return_to_drawdown"),
        },
    )


def pool_scorecard(pool_replay: dict[str, Any]) -> list[dict[str, Any]]:
    markets = pool_replay.get("markets", []) if isinstance(pool_replay, dict) else []
    return [pool_market_score(row) for row in markets]


def pool_market_score(row: dict[str, Any]) -> dict[str, Any]:
    tickets = int(row.get("tickets", 0) or 0)
    reconciled = int(row.get("reconciled", 0) or 0)
    executed = int(row.get("executed", 0) or 0)
    roi = optional_float(row.get("roi"))
    execution_roi = optional_float(row.get("execution_roi"))
    if tickets == 0:
        gate = "unverified"
        message = "未有樣本"
    elif reconciled == 0:
        gate = "unverified"
        message = "等待派彩對數"
    elif execution_roi is not None and execution_roi < 0:
        gate = "block"
        message = "下注時 ROI 為負"
    elif roi is not None and roi < -0.1:
        gate = "block"
        message = "結算 ROI 明顯虧損"
    elif (execution_roi is not None and execution_roi >= 0) or (roi is not None and roi >= 0):
        gate = "pass"
        message = "暫時有正回報"
    else:
        gate = "warn"
        message = "接近打和"
    return {
        "market": row.get("market"),
        "market_label": row.get("market_label") or row.get("market"),
        "status": gate,
        "status_label": STATUS_LABELS.get(gate, gate),
        "message": message,
        "tickets": tickets,
        "reconciled": reconciled,
        "executed": executed,
        "roi": roi,
        "execution_roi": execution_roi,
        "hit_rate": optional_float(row.get("hit_rate")),
        "profit": optional_float(row.get("profit")),
        "max_drawdown": optional_float(row.get("max_drawdown")),
        "execution_max_drawdown": optional_float(row.get("execution_max_drawdown")),
        "settlement_rate": optional_float(row.get("settlement_rate")),
        "execution_rate": optional_float(row.get("execution_rate")),
    }


def slice_scorecard(best: dict[str, Any] | None, baseline: dict[str, Any] | None, limit: int = 24) -> list[dict[str, Any]]:
    if not isinstance(best, dict):
        return []
    baseline_by_id = {
        row.get("slice_id"): row
        for row in baseline.get("slice_scorecard", [])
    } if isinstance(baseline, dict) else {}
    rows = []
    for row in best.get("slice_scorecard", []):
        base = baseline_by_id.get(row.get("slice_id"))
        scored = slice_score(row, base)
        rows.append(scored)
    return sorted(
        rows,
        key=lambda row: (
            0 if row["status"] == "block" else 1 if row["status"] == "warn" else 2 if row["status"] == "pass" else 3,
            -int(row.get("races", 0) or 0),
            str(row.get("label") or ""),
        ),
    )[:limit]


def slice_score(row: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    races = int(row.get("races", 0) or 0)
    baseline_races = int((baseline or {}).get("races", 0) or 0)
    log_loss_delta = nullable_delta(optional_float(row.get("log_loss")), optional_float((baseline or {}).get("log_loss")))
    brier_delta = nullable_delta(optional_float(row.get("brier_score")), optional_float((baseline or {}).get("brier_score")))
    top_pick_delta = nullable_delta(optional_float(row.get("top_pick_hit_rate")), optional_float((baseline or {}).get("top_pick_hit_rate")))
    if races < 5 or baseline_races < 5:
        status = "unverified"
        message = "分片樣本少"
    elif greater_than(log_loss_delta, 0.12) or greater_than(brier_delta, 0.05) or less_than(top_pick_delta, -0.12):
        status = "block"
        message = "分片退化"
    elif less_or_equal(log_loss_delta, 0.0) and less_or_equal(brier_delta, 0.0):
        status = "pass"
        message = "分片通過"
    else:
        status = "warn"
        message = "分片接近門檻"
    dimension = str(row.get("dimension") or "")
    value = str(row.get("value") or "")
    return {
        "slice_id": row.get("slice_id"),
        "dimension": dimension,
        "dimension_label": SLICE_DIMENSION_LABELS.get(dimension, row.get("dimension_label") or dimension),
        "value": value,
        "label": slice_label(row),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "message": message,
        "races": races,
        "baseline_races": baseline_races,
        "log_loss": optional_float(row.get("log_loss")),
        "baseline_log_loss": optional_float((baseline or {}).get("log_loss")),
        "log_loss_delta": log_loss_delta,
        "brier_score": optional_float(row.get("brier_score")),
        "baseline_brier_score": optional_float((baseline or {}).get("brier_score")),
        "brier_delta": brier_delta,
        "top_pick_hit_rate": optional_float(row.get("top_pick_hit_rate")),
        "baseline_top_pick_hit_rate": optional_float((baseline or {}).get("top_pick_hit_rate")),
        "top_pick_delta": top_pick_delta,
        "value_roi": optional_float(row.get("value_roi")),
        "baseline_value_roi": optional_float((baseline or {}).get("value_roi")),
        "roi_delta": nullable_delta(optional_float(row.get("value_roi")), optional_float((baseline or {}).get("value_roi"))),
    }


def section(section_id: str, status: str, message: str, metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_id": section_id,
        "label": SECTION_LABELS.get(section_id, section_id),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "message": message,
        "metrics": metrics,
    }


def promotion_gate_from_sections(sections: list[dict[str, Any]]) -> str:
    if not sections:
        return "no_data"
    if any(row["status"] == "block" for row in sections):
        return "blocked"
    if any(row["status"] == "unverified" for row in sections):
        return "unverified"
    if any(row["status"] == "warn" for row in sections):
        return "review"
    return "pass"


def promotion_message(gate: str, sections: list[dict[str, Any]], metrics: dict[str, Any]) -> str:
    if gate == "blocked":
        blocked = [row["label"] for row in sections if row["status"] == "block"]
        return f"先處理 {', '.join(blocked)}，暫時不可升級。"
    if gate == "unverified":
        return f"最佳版本 {metrics.get('best_label') or '-'} 仍要累積 OOS / 下注時樣本。"
    if gate == "review":
        warnings = [row["label"] for row in sections if row["status"] == "warn"]
        return f"{metrics.get('best_label') or '-'} 可列觀察，但 {', '.join(warnings)} 要人工覆核。"
    if gate == "pass":
        return f"{metrics.get('best_label') or '-'} 通過多目標 scorecard，可進入升級觀察。"
    return "未有足夠資料建立升級評分表。"


def slice_label(row: dict[str, Any]) -> str:
    dimension = str(row.get("dimension") or "")
    value = str(row.get("value") or "")
    label = SLICE_DIMENSION_LABELS.get(dimension)
    return f"{label}: {value}" if label else str(row.get("label") or row.get("slice_id") or "-")


def delta(best_metrics: dict[str, Any], baseline_metrics: dict[str, Any], key: str) -> float | None:
    return nullable_delta(optional_float(best_metrics.get(key)), optional_float(baseline_metrics.get(key)))


def nullable_delta(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None:
        return None
    return value - baseline


def improvement_pct(baseline: object, value: object) -> float | None:
    baseline_number = optional_float(baseline)
    value_number = optional_float(value)
    if baseline_number is None or value_number is None or baseline_number == 0:
        return None
    return (baseline_number - value_number) / baseline_number


def return_to_drawdown(profit: float | None, drawdown: float | None) -> float | None:
    if profit is None or drawdown is None:
        return None
    if drawdown <= 0:
        return profit if profit > 0 else 0.0
    return profit / drawdown


def drawdown_regressed(value: float | None, baseline: float | None, multiplier: float) -> bool:
    if value is None or baseline is None:
        return False
    if baseline <= 0:
        return value > 0
    return value > baseline * multiplier


def greater_than(value: float | None, threshold: float) -> bool:
    return value is not None and value > threshold


def less_than(value: float | None, threshold: float) -> bool:
    return value is not None and value < threshold


def less_or_equal(value: float | None, threshold: float) -> bool:
    return value is not None and value <= threshold


def optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def safe_div(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None
