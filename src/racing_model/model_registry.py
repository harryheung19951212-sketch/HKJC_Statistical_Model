from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import fetch_all, insert_rows
from .walk_forward import run_walk_forward_versions


GATE_LABELS = {
    "no_data": "未有資料",
    "sample_insufficient": "樣本不足",
    "upgrade_candidate": "可列入升級候選",
    "hold_baseline": "保持現有基線",
}


def run_and_record_model_registry(
    conn: sqlite3.Connection,
    model_path: Path | str,
    trigger: str = "manual",
    min_train_races: int = 1,
    epochs: int = 80,
    min_expected_value: float = 0.05,
    stake: float = 10.0,
) -> dict[str, Any]:
    report = run_walk_forward_versions(
        conn,
        min_train_races=min_train_races,
        epochs=epochs,
        min_expected_value=min_expected_value,
        stake=stake,
    )
    row = build_registry_row(
        report=report,
        model_path=model_path,
        trigger=trigger,
        min_train_races=min_train_races,
        epochs=epochs,
        min_expected_value=min_expected_value,
        stake=stake,
    )
    insert_rows(conn, "model_registry_runs", [row])
    conn.commit()
    return {
        "status": "recorded",
        "run": public_registry_row(row),
        "report": report,
    }


def model_registry_report(conn: sqlite3.Connection, limit: int = 12) -> dict[str, Any]:
    rows = fetch_all(
        conn,
        """
        SELECT *
        FROM model_registry_runs
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    runs = [public_registry_row(dict(row)) for row in rows]
    latest_report = None
    if rows:
        try:
            latest_report = json.loads(rows[0]["report_json"])
        except (TypeError, json.JSONDecodeError):
            latest_report = None
    return {
        "summary": registry_summary(runs),
        "runs": runs,
        "latest_report": latest_report,
        "clv_status": "投注決策留痕已可保存建議賠率並對照最後賠率；未累積足夠已對數建議前，CLV 只作監控，不作強制升級 gate。",
    }


def build_registry_row(
    report: dict[str, Any],
    model_path: Path | str,
    trigger: str,
    min_train_races: int,
    epochs: int,
    min_expected_value: float,
    stake: float,
) -> dict[str, Any]:
    summary = report.get("summary", {})
    versions = report.get("versions", [])
    best = next((row for row in versions if row.get("variant_id") == summary.get("best_variant_id")), None)
    if best is None and versions:
        best = versions[0]
    baseline = next((row for row in versions if row.get("variant_id") == "baseline"), None)
    gate = promotion_gate(summary, best, baseline)
    best_metrics = (best or {}).get("metrics", {})
    baseline_metrics = (baseline or {}).get("metrics", {})
    return {
        "run_id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "model_path": str(model_path),
        "min_train_races": min_train_races,
        "epochs": epochs,
        "min_expected_value": min_expected_value,
        "stake": stake,
        "race_count": int(summary.get("race_count", 0) or 0),
        "folds": int(summary.get("folds", 0) or 0),
        "best_variant_id": str(summary.get("best_variant_id") or ""),
        "best_label": str(summary.get("best_label") or ""),
        "baseline_log_loss": metric_or_none(baseline_metrics, "log_loss"),
        "best_log_loss": metric_or_none(best_metrics, "log_loss"),
        "baseline_value_roi": metric_or_none(baseline_metrics, "value_roi"),
        "best_value_roi": metric_or_none(best_metrics, "value_roi"),
        "best_top_pick_hit_rate": metric_or_none(best_metrics, "top_pick_hit_rate"),
        "best_max_drawdown": metric_or_none(best_metrics, "max_drawdown"),
        "promotion_gate": gate,
        "recommendation": str(summary.get("recommendation") or ""),
        "report_json": json.dumps(report, ensure_ascii=False, sort_keys=True),
    }


def promotion_gate(summary: dict[str, Any], best: dict[str, Any] | None, baseline: dict[str, Any] | None) -> str:
    folds = int(summary.get("folds", 0) or 0)
    if not best:
        return "no_data"
    if folds < 30:
        return "sample_insufficient"
    if not baseline:
        return "hold_baseline"
    best_metrics = best.get("metrics", {})
    baseline_metrics = baseline.get("metrics", {})
    best_loss = float(best_metrics.get("log_loss", 0.0) or 0.0)
    baseline_loss = float(baseline_metrics.get("log_loss", 0.0) or 0.0)
    best_roi = float(best_metrics.get("value_roi", 0.0) or 0.0)
    baseline_roi = float(baseline_metrics.get("value_roi", 0.0) or 0.0)
    best_drawdown = float(best_metrics.get("max_drawdown", 0.0) or 0.0)
    baseline_drawdown = float(baseline_metrics.get("max_drawdown", 0.0) or 0.0)
    improvement = (baseline_loss - best_loss) / baseline_loss if baseline_loss else 0.0
    if (
        best.get("variant_id") != "baseline"
        and improvement >= 0.02
        and best_roi >= baseline_roi
        and best_drawdown <= baseline_drawdown * 1.1
    ):
        return "upgrade_candidate"
    return "hold_baseline"


def public_registry_row(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in row.items() if key != "report_json"}
    gate = str(result.get("promotion_gate") or "sample_insufficient")
    result["promotion_gate_label"] = GATE_LABELS.get(gate, gate)
    return result


def registry_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    latest = runs[0] if runs else None
    candidates = [row for row in runs if row.get("promotion_gate") == "upgrade_candidate"]
    return {
        "run_count": len(runs),
        "latest_run_id": latest.get("run_id") if latest else None,
        "latest_gate": latest.get("promotion_gate") if latest else "no_data",
        "latest_gate_label": latest.get("promotion_gate_label") if latest else GATE_LABELS["no_data"],
        "latest_recommendation": latest.get("recommendation") if latest else "未有已保存 out-of-sample 評估。",
        "upgrade_candidates": len(candidates),
    }


def metric_or_none(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    return float(value)
