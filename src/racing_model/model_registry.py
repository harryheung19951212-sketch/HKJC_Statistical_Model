from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .pool_replay import pool_replay_report
from .storage import fetch_all, insert_rows
from .walk_forward import run_walk_forward_versions


GATE_LABELS = {
    "no_data": "未有資料",
    "sample_insufficient": "樣本不足",
    "execution_unverified": "下注時樣本不足",
    "execution_blocked": "下注時表現未過關",
    "upgrade_candidate": "可列入升級候選",
    "hold_baseline": "保持現有基線",
}

EXECUTION_GATE_LABELS = {
    "pass": "下注時 gate 通過",
    "unverified": "下注時樣本不足",
    "blocked": "下注時 ROI / 回撤未過關",
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
    execution = execution_gate_report(conn)
    report["execution_gate"] = execution
    row = build_registry_row(
        report=report,
        execution=execution,
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
        "clv_status": "升級 gate 已加入下注時 execution ROI / 回撤。未有足夠已確認下注樣本時，任何候選只可列為研究，不能正式替換模型。",
    }


def build_registry_row(
    report: dict[str, Any],
    execution: dict[str, Any],
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
    stats_gate = statistical_promotion_gate(summary, best, baseline)
    gate = apply_execution_gate(stats_gate, execution)
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
        "execution_confirmed": int(execution.get("confirmed", 0) or 0),
        "execution_roi": optional_float(execution.get("execution_roi")),
        "execution_max_drawdown": optional_float(execution.get("execution_max_drawdown")),
        "execution_gate": str(execution.get("gate") or "unverified"),
        "promotion_gate": gate,
        "recommendation": str(summary.get("recommendation") or ""),
        "report_json": json.dumps(report, ensure_ascii=False, sort_keys=True),
    }


def statistical_promotion_gate(summary: dict[str, Any], best: dict[str, Any] | None, baseline: dict[str, Any] | None) -> str:
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


def promotion_gate(summary: dict[str, Any], best: dict[str, Any] | None, baseline: dict[str, Any] | None) -> str:
    return statistical_promotion_gate(summary, best, baseline)


def execution_gate_report(conn: sqlite3.Connection, min_confirmed: int = 20) -> dict[str, Any]:
    replay = pool_replay_report(conn)
    summary = replay.get("summary", {})
    confirmed = int(summary.get("executed", 0) or 0)
    execution_roi = optional_float(summary.get("execution_roi"))
    execution_drawdown = optional_float(summary.get("execution_max_drawdown"))
    if confirmed < min_confirmed or execution_roi is None:
        gate = "unverified"
        message = f"已確認下注樣本 {confirmed}/{min_confirmed}，暫時不足以批准模型替換。"
    elif execution_roi < 0:
        gate = "blocked"
        message = f"下注時 ROI {execution_roi * 100:.1f}% 為負，禁止模型升級。"
    else:
        gate = "pass"
        message = f"下注時 ROI {execution_roi * 100:.1f}% 通過最低執行 gate。"
    return {
        "gate": gate,
        "gate_label": EXECUTION_GATE_LABELS.get(gate, gate),
        "message": message,
        "confirmed": confirmed,
        "min_confirmed": min_confirmed,
        "execution_roi": execution_roi,
        "execution_max_drawdown": execution_drawdown,
    }


def apply_execution_gate(stats_gate: str, execution: dict[str, Any]) -> str:
    if stats_gate != "upgrade_candidate":
        return stats_gate
    execution_gate = str(execution.get("gate") or "unverified")
    if execution_gate == "pass":
        return stats_gate
    if execution_gate == "blocked":
        return "execution_blocked"
    return "execution_unverified"


def public_registry_row(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in row.items() if key != "report_json"}
    gate = str(result.get("promotion_gate") or "sample_insufficient")
    result["promotion_gate_label"] = GATE_LABELS.get(gate, gate)
    execution_gate = str(result.get("execution_gate") or "unverified")
    result["execution_gate_label"] = EXECUTION_GATE_LABELS.get(execution_gate, execution_gate)
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


def optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
