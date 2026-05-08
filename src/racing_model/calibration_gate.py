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
    min_slice_races: int = 5,
    min_slice_runners: int = 40,
) -> dict[str, Any]:
    report = evaluate_model_evolution(conn, model)
    return calibration_gate_from_report(
        report,
        min_races=min_races,
        min_runners=min_runners,
        min_bin_count=min_bin_count,
        min_slice_races=min_slice_races,
        min_slice_runners=min_slice_runners,
    )


def calibration_gate_from_report(
    report: dict[str, Any],
    min_races: int = 30,
    min_runners: int = 200,
    min_bin_count: int = 8,
    min_slice_races: int = 5,
    min_slice_runners: int = 40,
) -> dict[str, Any]:
    metrics = report.get("metrics", {}) if isinstance(report, dict) else {}
    calibration = list(report.get("calibration", [])) if isinstance(report, dict) else []
    calibration_slices = list(report.get("calibration_slices", [])) if isinstance(report, dict) else []
    races = int(metrics.get("races", 0) or 0)
    runners = int(metrics.get("runners", 0) or 0)
    usable_bins = [row for row in calibration if int(row.get("count", 0) or 0) >= min_bin_count]
    worst_bin = worst_calibration_bin(usable_bins)
    usable_slices = usable_calibration_slices(
        calibration_slices,
        min_slice_races=min_slice_races,
        min_slice_runners=min_slice_runners,
        min_bin_count=min_bin_count,
    )
    worst_slice = worst_calibration_slice(usable_slices)

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
    elif calibration_slices and not usable_slices:
        status = "unverified"
        stake_factor = 0.5
        message = (
            f"整體校準可用，但未有分片同時達到 {min_slice_races} 場、"
            f"{min_slice_runners} 匹及每桶 {min_bin_count} 匹，注碼先減半。"
        )
    elif worst_slice and is_blocked_slice(worst_slice):
        status = "blocked"
        stake_factor = 0.25
        worst_slice_bin = worst_slice.get("worst_bin") or {}
        message = (
            f"分片校準未過關：{worst_slice.get('label')} / {worst_slice_bin.get('label')} "
            f"偏差 {float(worst_slice_bin.get('gap') or 0) * 100:.1f}%，"
            "注碼降至四分之一並禁止升級。"
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
        "min_slice_races": min_slice_races,
        "min_slice_runners": min_slice_runners,
        "races": races,
        "runners": runners,
        "usable_bins": len(usable_bins),
        "worst_bin": compact_bin(worst_bin),
        "slice_count": len(calibration_slices),
        "usable_slices": len(usable_slices),
        "worst_slice": compact_slice(worst_slice),
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


def usable_calibration_slices(
    rows: list[dict[str, Any]],
    min_slice_races: int,
    min_slice_runners: int,
    min_bin_count: int,
) -> list[dict[str, Any]]:
    usable = []
    for row in rows:
        if int(row.get("races", 0) or 0) < min_slice_races:
            continue
        if int(row.get("runners", 0) or 0) < min_slice_runners:
            continue
        qualified_bins = [
            item
            for item in row.get("bins", [])
            if isinstance(item, dict) and int(item.get("count", 0) or 0) >= min_bin_count
        ]
        worst_bin = worst_calibration_bin(qualified_bins)
        if not worst_bin or int(worst_bin.get("count", 0) or 0) < min_bin_count:
            continue
        usable.append({**row, "worst_bin": worst_bin})
    return usable


def worst_calibration_slice(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: abs(float((row.get("worst_bin") or {}).get("gap") or 0.0)))


def is_blocked_slice(row: dict[str, Any]) -> bool:
    worst_bin = row.get("worst_bin") if isinstance(row.get("worst_bin"), dict) else None
    return bool(worst_bin and is_blocked_bin(worst_bin))


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


def compact_slice(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "slice_id": row.get("slice_id"),
        "dimension": row.get("dimension"),
        "dimension_label": row.get("dimension_label"),
        "value": row.get("value"),
        "label": row.get("label"),
        "races": int(row.get("races", 0) or 0),
        "runners": int(row.get("runners", 0) or 0),
        "worst_bin": compact_bin(row.get("worst_bin") if isinstance(row.get("worst_bin"), dict) else None),
    }
