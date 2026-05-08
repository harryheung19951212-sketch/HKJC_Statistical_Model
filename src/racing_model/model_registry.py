from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .calibration_gate import build_calibration_gate
from .features import build_training_races
from .pool_replay import pool_replay_report
from .storage import fetch_all, insert_rows
from .walk_forward import build_oos_calibration_gate, build_oos_slice_gate, default_variants, new_model_for_variant, run_walk_forward_versions


GATE_LABELS = {
    "no_data": "未有資料",
    "sample_insufficient": "樣本不足",
    "execution_unverified": "下注時樣本不足",
    "execution_blocked": "下注時表現未過關",
    "upgrade_candidate": "可列入升級候選",
    "hold_baseline": "保持現有基線",
}
GATE_LABELS.update(
    {
        "slice_unverified": "分片 OOS 樣本不足",
        "slice_blocked": "分片 OOS 阻擋",
        "calibration_unverified": "候選 OOS 校準樣本不足",
        "calibration_blocked": "候選 OOS 校準阻擋",
    }
)

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
        "latest_oos_gate": latest_report.get("oos_gate") if isinstance(latest_report, dict) else None,
        "latest_candidate_calibration_gate": latest_report.get("candidate_calibration_gate") if isinstance(latest_report, dict) else None,
        "clv_status": "升級 gate 已加入下注時 execution ROI / 回撤。未有足夠已確認下注樣本時，任何候選只可列為研究，不能正式替換模型。",
    }


def promote_latest_model(
    conn: sqlite3.Connection,
    model_path: Path | str,
    epochs: int = 400,
    backup: bool = True,
    enforce_calibration: bool = True,
) -> dict[str, Any]:
    latest = latest_registry_row(conn)
    if not latest:
        return {
            "status": "refused",
            "reason": "未有已保存 out-of-sample 評估，不能替換模型。",
            "promotion_gate": "no_data",
            "promotion_gate_label": GATE_LABELS["no_data"],
        }

    gate = str(latest.get("promotion_gate") or "no_data")
    if gate != "upgrade_candidate":
        return {
            "status": "refused",
            "reason": f"最新 gate 是「{GATE_LABELS.get(gate, gate)}」，未批准正式替換模型。",
            "run": public_registry_row(latest),
            "promotion_gate": gate,
            "promotion_gate_label": GATE_LABELS.get(gate, gate),
        }

    report = parse_report_json(latest.get("report_json"))
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    variant_id = str(summary.get("best_variant_id") or latest.get("best_variant_id") or "")
    variants = {variant.variant_id: variant for variant in default_variants()}
    variant = variants.get(variant_id)
    if not variant:
        return {
            "status": "refused",
            "reason": f"找不到候選模型版本：{variant_id or '-'}。",
            "run": public_registry_row(latest),
        }
    if variant.temperature != 1.0:
        return {
            "status": "refused",
            "reason": "此候選版本需要溫度校準，但現時模型檔未能保存該執行策略；先保持基線。",
            "run": public_registry_row(latest),
            "variant_id": variant.variant_id,
            "variant_label": variant.label,
        }

    races = build_training_races(conn)
    if not races:
        return {
            "status": "refused",
            "reason": "未有可訓練賽果，不能輸出新模型檔。",
            "run": public_registry_row(latest),
            "variant_id": variant.variant_id,
            "variant_label": variant.label,
        }

    target = Path(model_path)
    model = new_model_for_variant(variant)
    model.fit(races, epochs=epochs)
    calibration = build_calibration_gate(conn, model)
    if enforce_calibration and not calibration.get("promote_allowed"):
        return {
            "status": "refused",
            "reason": f"候選模型未通過校準 gate：{calibration.get('message')}",
            "run": public_registry_row(latest),
            "variant_id": variant.variant_id,
            "variant_label": variant.label,
            "calibration_gate": calibration,
        }

    backup_path = backup_model_file(target) if backup else None
    model.save(target)
    return {
        "status": "promoted",
        "message": f"已用「{variant.label}」重訓 {len(races)} 場並替換模型檔。",
        "model_path": str(target),
        "backup_path": str(backup_path) if backup_path else None,
        "trained_races": len(races),
        "epochs": epochs,
        "variant_id": variant.variant_id,
        "variant_label": variant.label,
        "feature_count": len(variant.feature_names),
        "calibration_gate": calibration,
        "run": public_registry_row(latest),
    }


def latest_registry_row(conn: sqlite3.Connection) -> dict[str, Any] | None:
    rows = fetch_all(
        conn,
        """
        SELECT *
        FROM model_registry_runs
        ORDER BY created_at DESC
        LIMIT 1
        """,
    )
    return dict(rows[0]) if rows else None


def parse_report_json(value: object) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def backup_model_file(target: Path) -> Path | None:
    if not target.exists():
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = target.with_name(f"{target.stem}.backup-{timestamp}{target.suffix}")
    shutil.copy2(target, backup_path)
    return backup_path


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
    oos_gate = report.get("oos_gate") if isinstance(report.get("oos_gate"), dict) else build_oos_slice_gate(best, baseline)
    candidate_calibration_gate = (
        report.get("candidate_calibration_gate")
        if isinstance(report.get("candidate_calibration_gate"), dict)
        else build_oos_calibration_gate(best)
    )
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
        "recommendation": registry_recommendation(summary, oos_gate, candidate_calibration_gate, gate),
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
    oos_gate = build_oos_slice_gate(best, baseline)
    if oos_gate["gate"] == "blocked":
        return "slice_blocked"
    if oos_gate["gate"] == "unverified":
        return "slice_unverified"
    calibration_gate = build_oos_calibration_gate(best)
    if calibration_gate["gate"] == "blocked":
        return "calibration_blocked"
    if calibration_gate["gate"] in {"unverified", "no_data"}:
        return "calibration_unverified"
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


def registry_recommendation(
    summary: dict[str, Any],
    oos_gate: dict[str, Any],
    candidate_calibration_gate: dict[str, Any],
    gate: str,
) -> str:
    if gate == "slice_blocked":
        return f"分片 OOS gate 阻擋升級：{oos_gate.get('message')}"
    if gate == "slice_unverified":
        return f"分片 OOS gate 未確認：{oos_gate.get('message')}"
    if gate == "calibration_blocked":
        return f"候選 OOS 校準阻擋升級：{candidate_calibration_gate.get('message')}"
    if gate == "calibration_unverified":
        return f"候選 OOS 校準未確認：{candidate_calibration_gate.get('message')}"
    return str(summary.get("recommendation") or "")


def promotion_gate(summary: dict[str, Any], best: dict[str, Any] | None, baseline: dict[str, Any] | None) -> str:
    return statistical_promotion_gate(summary, best, baseline)


def execution_gate_report(
    conn: sqlite3.Connection,
    min_confirmed: int = 20,
    max_invalid_execution_rate: float = 0.25,
) -> dict[str, Any]:
    replay = pool_replay_report(conn)
    summary = replay.get("summary", {})
    confirmed = int(summary.get("executed", 0) or 0)
    execution_roi = optional_float(summary.get("execution_roi"))
    execution_drawdown = optional_float(summary.get("execution_max_drawdown"))
    valid_execution = int(summary.get("valid_execution", 0) or 0)
    stale_price = int(summary.get("stale_price", 0) or 0)
    negative_execution = int(summary.get("negative_ev_at_execution", 0) or 0)
    invalid_execution = stale_price + negative_execution
    execution_valid_rate = optional_float(summary.get("execution_valid_rate"))
    invalid_execution_rate = invalid_execution / confirmed if confirmed else None
    avg_execution_ev = optional_float(summary.get("avg_execution_expected_value_at_bet"))
    avg_execution_edge = optional_float(summary.get("avg_execution_edge_at_bet"))
    if confirmed < min_confirmed or execution_roi is None:
        gate = "unverified"
        message = f"已確認下注樣本 {confirmed}/{min_confirmed}，暫時不足以批准模型替換。"
    elif invalid_execution_rate is not None and invalid_execution_rate > max_invalid_execution_rate:
        gate = "blocked"
        message = (
            f"下注價失效比例 {invalid_execution_rate * 100:.1f}% "
            f"高過上限 {max_invalid_execution_rate * 100:.1f}%，禁止模型升級。"
        )
    elif execution_roi < 0:
        gate = "blocked"
        message = f"下注時 ROI {execution_roi * 100:.1f}% 為負，禁止模型升級。"
    else:
        gate = "pass"
        message = (
            f"下注時 ROI {execution_roi * 100:.1f}%、"
            f"執行合格率 {(execution_valid_rate or 0.0) * 100:.1f}% 通過最低執行 gate。"
        )
    return {
        "gate": gate,
        "gate_label": EXECUTION_GATE_LABELS.get(gate, gate),
        "message": message,
        "confirmed": confirmed,
        "min_confirmed": min_confirmed,
        "execution_roi": execution_roi,
        "execution_max_drawdown": execution_drawdown,
        "valid_execution": valid_execution,
        "stale_price": stale_price,
        "negative_ev_at_execution": negative_execution,
        "invalid_execution": invalid_execution,
        "execution_valid_rate": execution_valid_rate,
        "invalid_execution_rate": invalid_execution_rate,
        "max_invalid_execution_rate": max_invalid_execution_rate,
        "avg_execution_expected_value_at_bet": avg_execution_ev,
        "avg_execution_edge_at_bet": avg_execution_edge,
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
