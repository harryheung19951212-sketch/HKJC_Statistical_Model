from __future__ import annotations

import sqlite3
from typing import Any

from .evolution import evaluate_model_evolution
from .model import RankingModel


GATE_LABELS = {
    "pass": "校準通過",
    "unverified": "校準樣本不足",
    "blocked": "校準未過關",
}


def build_calibration_gate(
    conn: sqlite3.Connection,
    model: RankingModel,
    min_races: int = 30,
    min_runners: int = 200,
    min_bin_count: int = 8,
) -> dict[str, Any]:
    report = evaluate_model_evolution(conn, model)
    return calibration_gate_from_report(
        report,
        min_races=min_races,
        min_runners=min_runners,
        min_bin_count=min_bin_count,
    )


def calibration_gate_from_report(
    report: dict[str, Any],
    min_races: int = 30,
    min_runners: int = 200,
    min_bin_count: int = 8,
) -> dict[str, Any]:
    metrics = report.get("metrics", {}) if isinstance(report, dict) else {}
    calibration = list(report.get("calibration", [])) if isinstance(report, dict) else []
    races = int(metrics.get("races", 0) or 0)
    runners = int(metrics.get("runners", 0) or 0)
    usable_bins = [row for row in calibration if int(row.get("count", 0) or 0) >= min_bin_count]
    worst_bin = worst_calibration_bin(usable_bins)

    if races < min_races or runners < min_runners:
        status = "unverified"
        stake_factor = 0.5
        message = f"校準樣本 {races}/{min_races} 場、{runners}/{min_runners} 匹，注碼先減半，禁止正式升級。"
    elif not usable_bins:
        status = "unverified"
        stake_factor = 0.5
        message = f"未有任何分桶達到 {min_bin_count} 匹樣本，注碼先減半，禁止正式升級。"
    elif worst_bin and is_blocked_bin(worst_bin):
        status = "blocked"
        stake_factor = 0.25
        message = (
            f"{worst_bin.get('label')} 勝率分桶偏差 {float(worst_bin.get('gap') or 0) * 100:.1f}%，"
            "校準未過關，注碼降至四分之一並禁止升級。"
        )
    else:
        status = "pass"
        stake_factor = 1.0
        message = "勝率校準 gate 通過，注碼不需額外打折。"

    return {
        "status": status,
        "label": GATE_LABELS.get(status, status),
        "message": message,
        "stake_factor": stake_factor,
        "promote_allowed": status == "pass",
        "min_races": min_races,
        "min_runners": min_runners,
        "min_bin_count": min_bin_count,
        "races": races,
        "runners": runners,
        "usable_bins": len(usable_bins),
        "worst_bin": compact_bin(worst_bin),
    }


def worst_calibration_bin(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: abs(float(row.get("gap") or 0.0)))


def is_blocked_bin(row: dict[str, Any]) -> bool:
    gap = float(row.get("gap") or 0.0)
    avg_prediction = float(row.get("avg_prediction") or 0.0)
    if avg_prediction >= 0.15 and gap <= -0.08:
        return True
    return abs(gap) >= 0.15


def compact_bin(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "label": row.get("label"),
        "count": int(row.get("count", 0) or 0),
        "avg_prediction": row.get("avg_prediction"),
        "observed_rate": row.get("observed_rate"),
        "gap": row.get("gap"),
    }
