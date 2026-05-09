from __future__ import annotations

import sqlite3
import re
import math
from typing import Any

from .betting import RISK_PROFILES
from .betting_ledger import dedupe_logical_recommendations, exotic_outcome, final_exotic_dividend, parse_combination_key
from .pool_rules import POOL_RULES, cost_adjusted_expected_value, pool_rule_payload
from .storage import fetch_all


POOL_LABELS_ZH = {
    "WIN": "獨贏",
    "PLACE": "位置",
    "QIN": "連贏",
    "QPL": "位置Q",
    "FCT": "二重彩",
    "TRIO": "單T",
    "TCE": "三重彩",
    "FIRST4": "四連環",
    "QUARTET": "四重彩",
}

POOL_REPLAY_MIN_SAMPLES = {
    "WIN": 12,
    "PLACE": 12,
    "QIN": 10,
    "QPL": 10,
    "FCT": 10,
    "TRIO": 8,
    "TCE": 8,
    "FIRST4": 6,
    "QUARTET": 6,
}


def pool_replay_report(conn: sqlite3.Connection, race_id: str | None = None) -> dict[str, Any]:
    rows = load_recommendations(conn, race_id)
    dividend_audit = final_dividend_audit(conn, rows)
    market_rows = {market: [] for market in POOL_RULES}
    for row in rows:
        market = str(row.get("market") or "").upper()
        if market in market_rows:
            market_rows[market].append(row)

    markets = [market_replay(market, market_rows[market], dividend_audit["by_market"].get(market, {})) for market in POOL_RULES]
    ranked = sorted(
        [row for row in markets if row["reconciled"] > 0],
        key=lambda row: (
            row["roi"] if row["roi"] is not None else -999.0,
            row["profit"],
            row["hit_rate"] if row["hit_rate"] is not None else 0.0,
        ),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    summary = overall_summary(rows, markets, best)
    bankroll_replay = bankroll_replay_report(rows)
    context_segments = contextual_replay_segments(rows)
    optimizer = pool_choice_optimizer(rows)
    return {
        "race_id": race_id,
        "summary": summary,
        "markets": markets,
        "context_segments": context_segments,
        "pool_choice_optimizer": optimizer,
        "ranking": ranked,
        "bankroll_replay": bankroll_replay,
        "final_dividend_audit": dividend_audit,
        "insights": replay_insights(markets, best, optimizer),
    }


def pool_replay_calibration(report: dict[str, Any] | None, race_context: dict[str, Any] | None = None) -> dict[str, Any]:
    markets = list((report or {}).get("markets") or [])
    optimizer_by_market = {
        str(row.get("market") or "").upper(): row
        for row in (((report or {}).get("pool_choice_optimizer") or {}).get("markets") or [])
        if isinstance(row, dict)
    }
    global_rows = [pool_replay_market_gate(row) for row in markets if isinstance(row, dict)]
    global_by_market = {str(row["market"]): row for row in global_rows}
    context = normalize_race_context(race_context or {})
    context_rows = contextual_pool_replay_gates(report or {}, context, global_by_market) if context else {}
    rows = [
        attach_pool_choice_optimizer(context_rows.get(market, row), optimizer_by_market.get(market))
        for market, row in global_by_market.items()
    ]
    by_market = {str(row["market"]): row for row in rows}
    blocked = [row for row in rows if row["status"] == "replay_block"]
    reduced = [row for row in rows if row["stake_factor"] < 1.0 and row["status"] != "replay_block"]
    sample_building = [row for row in rows if row["status"] in {"no_replay_data", "sample_building"}]
    return {
        "status": "blocked" if blocked else "reduced" if reduced else "sample_building" if sample_building else "pass",
        "label": "分彩池 replay 校準",
        "message": pool_replay_calibration_message(blocked, reduced, sample_building, bool(context_rows)),
        "context": context,
        "markets": by_market,
        "blocked_markets": [row["market"] for row in blocked],
        "reduced_markets": [row["market"] for row in reduced],
        "sample_building_markets": [row["market"] for row in sample_building],
    }


def attach_pool_choice_optimizer(gate: dict[str, Any], optimizer: dict[str, Any] | None) -> dict[str, Any]:
    row = dict(gate)
    optimizer = optimizer or {}
    row.update(
        {
            "optimizer_status": optimizer.get("optimizer_status", "no_sample"),
            "optimizer_policy": optimizer.get("policy", "collect"),
            "optimizer_reason": optimizer.get("reason", "未有 walk-forward optimizer 樣本。"),
            "optimizer_delta_roi": optimizer.get("delta_roi"),
            "optimizer_baseline_roi": optimizer.get("baseline_roi"),
            "optimizer_gated_roi": optimizer.get("gated_roi"),
            "optimizer_retention_rate": optimizer.get("retention_rate"),
        }
    )
    return row


def pool_replay_market_gate(
    row: dict[str, Any],
    min_samples_override: int | None = None,
    context_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market = str(row.get("market") or "").upper()
    label = str(row.get("market_label") or POOL_LABELS_ZH.get(market, market))
    reconciled = int(row.get("reconciled") or 0)
    executed = int(row.get("executed") or 0)
    waiting_final = int(row.get("final_dividend_waiting_hits") or 0)
    ready_final = int(row.get("final_dividend_ready_hits") or 0)
    min_samples = int(min_samples_override or POOL_REPLAY_MIN_SAMPLES.get(market, 10))
    roi = safe_float(row.get("roi"))
    execution_roi = safe_float(row.get("execution_roi"))
    primary_roi = execution_roi if executed >= max(3, min_samples // 2) else roi
    status = "pass"
    stake_factor = 1.0
    reason = f"{label} replay 樣本達標，未觸發分池降注。"

    if reconciled <= 0:
        status = "no_replay_data"
        reason = f"{label} 未有已結算 replay 樣本；可以保存候選，但未能證明長期 ROI。"
    elif waiting_final > 0:
        status = "waiting_final_dividend"
        stake_factor = 0.5
        reason = f"{label} 有 {waiting_final} 張中票等 final dividend，真實 ROI 暫未可信，先減半注碼。"
    elif ready_final > 0:
        status = "ready_to_reconcile"
        stake_factor = 0.75
        reason = f"{label} 有 {ready_final} 張組合票已有 final dividend 等待對數，先降注直至 replay 更新。"
    elif reconciled < min_samples:
        status = "sample_building"
        reason = f"{label} 已結算 {reconciled}/{min_samples} 張，樣本不足，只能作初步分池參考。"
    elif primary_roi is not None and primary_roi <= -0.15:
        status = "replay_block"
        stake_factor = 0.0
        reason = f"{label} replay ROI {format_pct(primary_roi)} 低於 -15%，暫停真注，只保留觀察。"
    elif primary_roi is not None and primary_roi < 0:
        status = "replay_reduce"
        stake_factor = 0.5
        reason = f"{label} replay ROI {format_pct(primary_roi)} 暫時為負，先減半注碼。"

    return {
        "market": market,
        "market_label": label,
        "status": status,
        "label": pool_replay_gate_label(status),
        "reason": reason,
        "stake_factor": stake_factor,
        "reconciled": reconciled,
        "executed": executed,
        "min_samples": min_samples,
        "roi": roi,
        "execution_roi": execution_roi,
        "final_dividend_waiting_hits": waiting_final,
        "final_dividend_ready_hits": ready_final,
        "context_status": (context_source or {}).get("context_status", "global"),
        "context_segment_type": (context_source or {}).get("segment_type"),
        "context_segment_label": (context_source or {}).get("segment_label"),
        "context_sample_size": (context_source or {}).get("reconciled"),
        "context_min_samples": (context_source or {}).get("min_samples"),
        "context_roi": (context_source or {}).get("roi"),
        "global_status": (context_source or {}).get("global_status"),
        "global_roi": (context_source or {}).get("global_roi"),
    }


def pool_replay_gate_label(status: str) -> str:
    return {
        "pass": "Replay 通過",
        "no_replay_data": "未有 replay 樣本",
        "sample_building": "樣本累積中",
        "waiting_final_dividend": "等待最終派彩",
        "ready_to_reconcile": "等待對數",
        "replay_reduce": "Replay 減注",
        "replay_block": "Replay 封鎖",
    }.get(status, "Replay 待檢查")


def pool_replay_calibration_message(
    blocked: list[dict[str, Any]],
    reduced: list[dict[str, Any]],
    sample_building: list[dict[str, Any]],
    has_context: bool = False,
) -> str:
    prefix = "已按本場條件切片；" if has_context else ""
    if blocked:
        labels = "、".join(str(row["market_label"]) for row in blocked[:3])
        return f"{prefix}{labels} 的已結算 replay ROI 太差，暫停真注。"
    if reduced:
        labels = "、".join(str(row["market_label"]) for row in reduced[:3])
        return f"{prefix}{labels} replay 未完全可信，下注前會自動降注。"
    if sample_building:
        return f"{prefix}部分彩池仍在累積 replay 樣本；系統會顯示樣本狀態但不當成已驗證 edge。"
    return f"{prefix}分彩池 replay 暫未觸發限制。"


def load_recommendations(conn: sqlite3.Connection, race_id: str | None) -> list[dict[str, Any]]:
    where = ""
    params: tuple[Any, ...] = ()
    if race_id:
        where = "WHERE br.race_id = ?"
        params = (race_id,)
    rows = [
        dict(row)
        for row in fetch_all(
            conn,
            f"""
            SELECT br.*,
                   r.track AS race_track,
                   r.course AS race_course,
                   r.distance_m AS race_distance_m,
                   r.going AS race_going,
                   r.class_rating AS race_class_rating
            FROM betting_recommendations br
            LEFT JOIN races r ON r.race_id = br.race_id
            {where}
            ORDER BY br.race_date, br.race_id, br.created_at, br.recommendation_id
            """,
            params,
        )
    ]
    return dedupe_logical_recommendations(rows)


def contextual_replay_segments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = [
        ("track_distance_class", "馬場/路程/班次", lambda row: (race_track(row), distance_bucket(row.get("race_distance_m")), race_class(row))),
        ("track_distance", "馬場/路程", lambda row: (race_track(row), distance_bucket(row.get("race_distance_m")))),
        ("track", "馬場", lambda row: (race_track(row),)),
        ("distance_bucket", "路程桶", lambda row: (distance_bucket(row.get("race_distance_m")),)),
        ("class_rating", "班次", lambda row: (race_class(row),)),
    ]
    segments: list[dict[str, Any]] = []
    for market in POOL_RULES:
        market_rows = [row for row in rows if str(row.get("market") or "").upper() == market]
        if not market_rows:
            continue
        for segment_type, segment_name, key_func in specs:
            buckets: dict[tuple[str, ...], list[dict[str, Any]]] = {}
            for row in market_rows:
                key = tuple(str(part or "") for part in key_func(row))
                if not key or any(not part for part in key):
                    continue
                buckets.setdefault(key, []).append(row)
            for key, bucket_rows in buckets.items():
                replay = market_replay(market, bucket_rows)
                replay.update(
                    {
                        "segment_type": segment_type,
                        "segment_name": segment_name,
                        "segment_key": "|".join(key),
                        "segment_label": segment_label(segment_type, key),
                        "context_min_samples": contextual_min_samples(market),
                    }
                )
                segments.append(replay)
    return sorted(
        segments,
        key=lambda row: (
            str(row.get("market") or ""),
            -int(row.get("reconciled") or 0),
            str(row.get("segment_type") or ""),
            str(row.get("segment_key") or ""),
        ),
    )


def contextual_pool_replay_gates(
    report: dict[str, Any],
    context: dict[str, Any],
    global_by_market: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    segments = [row for row in list(report.get("context_segments") or []) if isinstance(row, dict)]
    if not context:
        return {}
    output: dict[str, dict[str, Any]] = {}
    for market, global_gate in global_by_market.items():
        selected = select_context_segment(market, segments, context)
        if not selected:
            output[market] = context_fallback_gate(global_gate, "未有符合本場條件的 replay 切片，沿用全局分池 gate。")
            continue
        context_min = contextual_min_samples(market)
        if int(selected.get("reconciled") or 0) < context_min:
            message = f"{selected['segment_label']} 切片已結算 {int(selected.get('reconciled') or 0)}/{context_min} 張，樣本未夠，沿用全局分池 gate。"
            output[market] = context_fallback_gate(global_gate, message, selected, context_min)
            continue
        context_gate = pool_replay_market_gate(
            selected,
            min_samples_override=context_min,
            context_source={
                "context_status": "context_applied",
                "segment_type": selected.get("segment_type"),
                "segment_label": selected.get("segment_label"),
                "reconciled": selected.get("reconciled"),
                "min_samples": context_min,
                "roi": selected.get("roi"),
                "global_status": global_gate.get("status"),
                "global_roi": global_gate.get("roi"),
            },
        )
        context_gate["reason"] = f"{selected['segment_label']} 切片：{context_gate['reason']}"
        output[market] = combine_global_and_context_gate(global_gate, context_gate)
    return output


def select_context_segment(market: str, segments: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    wanted = {
        "track_distance_class": (context.get("track"), context.get("distance_bucket"), context.get("class_rating")),
        "track_distance": (context.get("track"), context.get("distance_bucket")),
        "track": (context.get("track"),),
        "distance_bucket": (context.get("distance_bucket"),),
        "class_rating": (context.get("class_rating"),),
    }
    priority = {key: index for index, key in enumerate(wanted)}
    candidates = []
    for row in segments:
        if str(row.get("market") or "").upper() != market:
            continue
        segment_type = str(row.get("segment_type") or "")
        key = tuple(part for part in str(row.get("segment_key") or "").split("|") if part)
        if segment_type in wanted and key == tuple(part for part in wanted[segment_type] if part):
            candidates.append(row)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda row: (
            priority.get(str(row.get("segment_type") or ""), 99),
            -int(row.get("reconciled") or 0),
        ),
    )[0]


def context_fallback_gate(
    global_gate: dict[str, Any],
    message: str,
    selected: dict[str, Any] | None = None,
    context_min: int | None = None,
) -> dict[str, Any]:
    gate = dict(global_gate)
    gate.update(
        {
            "context_status": "fallback_global",
            "context_segment_type": (selected or {}).get("segment_type"),
            "context_segment_label": (selected or {}).get("segment_label"),
            "context_sample_size": (selected or {}).get("reconciled"),
            "context_min_samples": context_min,
            "context_roi": (selected or {}).get("roi"),
            "global_status": global_gate.get("status"),
            "global_roi": global_gate.get("roi"),
            "reason": f"{message} {global_gate.get('reason') or ''}".strip(),
        }
    )
    return gate


def combine_global_and_context_gate(global_gate: dict[str, Any], context_gate: dict[str, Any]) -> dict[str, Any]:
    if gate_risk_rank(global_gate) > gate_risk_rank(context_gate):
        gate = dict(global_gate)
        gate.update(
            {
                "context_status": "context_limited_by_global",
                "context_segment_type": context_gate.get("context_segment_type"),
                "context_segment_label": context_gate.get("context_segment_label"),
                "context_sample_size": context_gate.get("context_sample_size"),
                "context_min_samples": context_gate.get("context_min_samples"),
                "context_roi": context_gate.get("context_roi"),
                "global_status": global_gate.get("status"),
                "global_roi": global_gate.get("roi"),
                "reason": f"全局 replay 風險較高，優先採用全局限制；本場切片 {context_gate.get('context_segment_label') or '-'} ROI {format_pct(context_gate.get('context_roi'))}。",
            }
        )
        return gate
    gate = dict(context_gate)
    gate["stake_factor"] = min(gate_stake_factor(global_gate), gate_stake_factor(context_gate))
    gate["global_status"] = global_gate.get("status")
    gate["global_roi"] = global_gate.get("roi")
    if gate["stake_factor"] < gate_stake_factor(context_gate):
        gate["reason"] = f"{gate.get('reason') or ''}；全局 replay 亦要求降注，已採用較保守注碼。"
    return gate


def gate_stake_factor(gate: dict[str, Any]) -> float:
    value = gate.get("stake_factor")
    if value is None:
        return 1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def gate_risk_rank(gate: dict[str, Any]) -> int:
    return {
        "replay_block": 6,
        "waiting_final_dividend": 5,
        "replay_reduce": 4,
        "ready_to_reconcile": 3,
        "sample_building": 2,
        "no_replay_data": 1,
        "pass": 0,
    }.get(str(gate.get("status") or ""), 1)


def normalize_race_context(row: dict[str, Any]) -> dict[str, Any]:
    track = clean_context_value(row.get("track") or row.get("race_track"))
    class_rating = clean_context_value(row.get("class_rating") or row.get("race_class_rating"))
    bucket = distance_bucket(row.get("distance_m") or row.get("race_distance_m"))
    context = {
        "track": track,
        "course": clean_context_value(row.get("course") or row.get("race_course")),
        "distance_m": safe_int(row.get("distance_m") or row.get("race_distance_m")),
        "distance_bucket": bucket,
        "going": clean_context_value(row.get("going") or row.get("race_going")),
        "class_rating": class_rating,
    }
    return {key: value for key, value in context.items() if value not in {None, ""}}


def contextual_min_samples(market: str) -> int:
    return max(3, math.ceil(POOL_REPLAY_MIN_SAMPLES.get(market, 10) * 0.5))


def race_track(row: dict[str, Any]) -> str:
    return clean_context_value(row.get("race_track") or row.get("track"))


def race_class(row: dict[str, Any]) -> str:
    return clean_context_value(row.get("race_class_rating") or row.get("class_rating"))


def clean_context_value(value: Any) -> str:
    return str(value or "").strip()


def distance_bucket(value: Any) -> str:
    distance = safe_int(value)
    if distance is None or distance <= 0:
        return ""
    if distance <= 1200:
        return "短途"
    if distance <= 1600:
        return "一哩"
    if distance <= 2000:
        return "中距離"
    return "長途"


def segment_label(segment_type: str, key: tuple[str, ...]) -> str:
    if segment_type == "track_distance_class":
        return f"{key[0]} / {key[1]} / {key[2]}"
    if segment_type == "track_distance":
        return f"{key[0]} / {key[1]}"
    if segment_type == "track":
        return f"{key[0]}"
    if segment_type == "distance_bucket":
        return f"{key[0]}"
    if segment_type == "class_rating":
        return f"{key[0]}"
    return " / ".join(key)


def market_replay(market: str, rows: list[dict[str, Any]], dividend_audit: dict[str, Any] | None = None) -> dict[str, Any]:
    reconciled = [row for row in rows if str(row.get("reconciliation_status")) == "reconciled"]
    executed = [row for row in reconciled if str(row.get("execution_status")) == "confirmed"]
    hits = [row for row in reconciled if int(row.get("outcome_win") or 0) == 1]
    staked_all = sum(safe_float(row.get("recommended_stake")) or 0.0 for row in rows)
    staked = sum(safe_float(row.get("recommended_stake")) or 0.0 for row in reconciled)
    returned = sum(safe_float(row.get("returned")) or 0.0 for row in reconciled)
    profit = sum(safe_float(row.get("profit")) or 0.0 for row in reconciled)
    execution = execution_replay(executed)
    execution_drawdown = max_drawdown(executed, profit_key="execution_profit")
    expected_values = [safe_float(row.get("expected_value")) for row in rows]
    expected_values = [value for value in expected_values if value is not None]
    adjusted_values = [
        cost_adjusted_expected_value(safe_float(row.get("expected_value")), market)
        for row in rows
    ]
    adjusted_values = [value for value in adjusted_values if value is not None]
    clv_values = [safe_float(row.get("clv")) for row in reconciled]
    clv_values = [value for value in clv_values if value is not None]
    execution_clv_values = [safe_float(row.get("execution_clv")) for row in executed]
    execution_clv_values = [value for value in execution_clv_values if value is not None]
    execution_slippage_values = [safe_float(row.get("execution_slippage")) for row in executed]
    execution_slippage_values = [value for value in execution_slippage_values if value is not None]
    valid_execution = [row for row in executed if str(row.get("execution_value_status") or "") == "valid_execution"]
    stale_price = [row for row in executed if str(row.get("execution_value_status") or "") == "stale_price"]
    negative_execution = [row for row in executed if str(row.get("execution_value_status") or "") == "negative_ev_at_execution"]
    execution_ev_values = [safe_float(row.get("execution_expected_value_at_bet")) for row in executed]
    execution_ev_values = [value for value in execution_ev_values if value is not None]
    execution_edge_values = [safe_float(row.get("execution_edge_at_bet")) for row in executed]
    execution_edge_values = [value for value in execution_edge_values if value is not None]
    final_odds = [safe_float(row.get("final_odds")) for row in reconciled if safe_float(row.get("final_odds"))]
    low_return_hits = [
        row
        for row in hits
        if (safe_float(row.get("profit")) or 0.0) <= (safe_float(row.get("recommended_stake")) or 0.0) * 0.25
    ]
    roi = safe_divide(profit, staked)
    hit_rate = safe_divide(len(hits), len(reconciled))
    return {
        "market": market,
        "market_label": POOL_LABELS_ZH.get(market, market),
        "pool_rule": pool_rule_payload(market),
        "tickets": len(rows),
        "reconciled": len(reconciled),
        "executed": len(executed),
        "pending": len(rows) - len(reconciled),
        "staked_all": round(staked_all, 2),
        "staked": round(staked, 2),
        "returned": round(returned, 2),
        "profit": round(profit, 2),
        "roi": roi,
        "execution_staked": execution["staked"],
        "execution_returned": execution["returned"],
        "execution_profit": execution["profit"],
        "execution_roi": execution["roi"],
        "hit_rate": hit_rate,
        "avg_expected_value": average(expected_values),
        "avg_cost_adjusted_expected_value": average(adjusted_values),
        "avg_final_dividend": average(final_odds),
        "avg_clv": average(clv_values),
        "avg_execution_clv": average(execution_clv_values),
        "avg_execution_slippage": average(execution_slippage_values),
        "valid_execution": len(valid_execution),
        "stale_price": len(stale_price),
        "negative_ev_at_execution": len(negative_execution),
        "execution_valid_rate": safe_divide(len(valid_execution), len(executed)),
        "avg_execution_expected_value_at_bet": average(execution_ev_values),
        "avg_execution_edge_at_bet": average(execution_edge_values),
        "low_return_hits": len(low_return_hits),
        "max_drawdown": max_drawdown(reconciled),
        "execution_max_drawdown": max_drawdown(executed, profit_key="execution_profit"),
        "settlement_rate": safe_divide(len(reconciled), len(rows)),
        "execution_rate": safe_divide(len(executed), len(reconciled)),
        "final_dividend_waiting_hits": int((dividend_audit or {}).get("waiting_final_dividend") or 0),
        "final_dividend_ready_hits": int((dividend_audit or {}).get("final_ready_unreconciled") or 0),
        "exotic_unsettled_known_losses": int((dividend_audit or {}).get("known_loss_unreconciled") or 0),
        "verdict": market_verdict(len(rows), len(reconciled), roi, hit_rate),
    }


def final_dividend_audit(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> dict[str, Any]:
    pending = [
        row
        for row in rows
        if str(row.get("market") or "").upper() in {"QIN", "QPL", "FCT", "TRIO", "TCE", "FIRST4", "QUARTET"}
        and str(row.get("execution_status") or "") == "confirmed"
        and str(row.get("reconciliation_status") or "") != "reconciled"
    ]
    by_market = {market: empty_dividend_audit_market(market) for market in ["QIN", "QPL", "FCT", "TRIO", "TCE", "FIRST4", "QUARTET"]}
    items: list[dict[str, Any]] = []
    for row in pending:
        market = str(row.get("market") or "").upper()
        selected = parse_combination_key(str(row.get("horse_id") or ""))
        race_id = str(row.get("race_id") or "")
        market_summary = by_market.setdefault(market, empty_dividend_audit_market(market))
        market_summary["pending_executed"] += 1
        result_state = exotic_result_state(conn, race_id, market, selected)
        status = str(result_state["status"])
        final_odds = final_exotic_dividend(conn, race_id, market, str(row.get("horse_id") or ""))
        if status == "no_results":
            market_summary["no_results"] += 1
            reason = "未有賽果，未能判斷是否中票。"
        elif status == "loss":
            market_summary["known_loss_unreconciled"] += 1
            reason = "已知不中，下一次對數應可結算為輸票。"
        elif final_odds is None:
            market_summary["waiting_final_dividend"] += 1
            reason = "已知中票，但未有 final dividend，不能計回報。"
        else:
            market_summary["final_ready_unreconciled"] += 1
            reason = "final dividend 已有，下一次對數應可結算。"
        items.append(
            {
                "race_id": race_id,
                "market": market,
                "market_label": POOL_LABELS_ZH.get(market, market),
                "horse_id": row.get("horse_id"),
                "horse_name": row.get("horse_name"),
                "result_status": status,
                "finish_positions": result_state.get("finish_positions"),
                "final_dividend": final_odds,
                "stake": settled_stake(row),
                "reason": reason,
            }
        )
    summary = {
        "pending_executed": sum(int(row["pending_executed"]) for row in by_market.values()),
        "waiting_final_dividend": sum(int(row["waiting_final_dividend"]) for row in by_market.values()),
        "final_ready_unreconciled": sum(int(row["final_ready_unreconciled"]) for row in by_market.values()),
        "known_loss_unreconciled": sum(int(row["known_loss_unreconciled"]) for row in by_market.values()),
        "no_results": sum(int(row["no_results"]) for row in by_market.values()),
    }
    summary["status"] = (
        "waiting_final_dividend"
        if summary["waiting_final_dividend"]
        else "ready_to_reconcile"
        if summary["final_ready_unreconciled"] or summary["known_loss_unreconciled"]
        else "no_pending_exotic"
    )
    summary["message"] = final_dividend_audit_message(summary)
    return {
        "summary": summary,
        "by_market": by_market,
        "items": sorted(items, key=lambda row: (str(row.get("race_id") or ""), str(row.get("market") or ""), str(row.get("horse_id") or "")))[:40],
    }


def empty_dividend_audit_market(market: str) -> dict[str, Any]:
    return {
        "market": market,
        "market_label": POOL_LABELS_ZH.get(market, market),
        "pending_executed": 0,
        "waiting_final_dividend": 0,
        "final_ready_unreconciled": 0,
        "known_loss_unreconciled": 0,
        "no_results": 0,
    }


def exotic_result_state(conn: sqlite3.Connection, race_id: str, market: str, selected: list[int]) -> dict[str, Any]:
    if not race_id or not selected:
        return {"status": "no_results", "finish_positions": []}
    rows = fetch_all(
        conn,
        """
        SELECT ru.horse_no, x.finish_position
        FROM results x
        JOIN runners ru ON ru.race_id = x.race_id AND ru.horse_id = x.horse_id
        WHERE x.race_id = ?
        """,
        (race_id,),
    )
    if not rows:
        return {"status": "no_results", "finish_positions": []}
    finish_by_no = {int(row["horse_no"]): int(row["finish_position"]) for row in rows if row["horse_no"]}
    if any(number not in finish_by_no for number in selected):
        return {"status": "no_results", "finish_positions": []}
    finishes = [finish_by_no[number] for number in selected]
    return {
        "status": "hit" if exotic_outcome(market, selected, finish_by_no) else "loss",
        "finish_positions": finishes,
    }


def final_dividend_audit_message(summary: dict[str, Any]) -> str:
    if int(summary.get("waiting_final_dividend") or 0) > 0:
        return f"{summary['waiting_final_dividend']} 張組合中票等待 final dividend，暫不可計入真實 ROI。"
    ready = int(summary.get("final_ready_unreconciled") or 0) + int(summary.get("known_loss_unreconciled") or 0)
    if ready > 0:
        return f"{ready} 張組合票已有足夠資料，下一次對數可結算。"
    if int(summary.get("no_results") or 0) > 0:
        return "仍有組合票未有賽果，暫時等待官方結果。"
    return "未有等待 final dividend 的組合票。"


def overall_summary(rows: list[dict[str, Any]], markets: list[dict[str, Any]], best: dict[str, Any] | None) -> dict[str, Any]:
    reconciled = [row for row in rows if str(row.get("reconciliation_status")) == "reconciled"]
    executed = [row for row in reconciled if str(row.get("execution_status")) == "confirmed"]
    staked = sum(safe_float(row.get("recommended_stake")) or 0.0 for row in reconciled)
    returned = sum(safe_float(row.get("returned")) or 0.0 for row in reconciled)
    profit = sum(safe_float(row.get("profit")) or 0.0 for row in reconciled)
    execution = execution_replay(executed)
    hits = sum(1 for row in reconciled if int(row.get("outcome_win") or 0) == 1)
    valid_execution = [row for row in executed if str(row.get("execution_value_status") or "") == "valid_execution"]
    stale_price = [row for row in executed if str(row.get("execution_value_status") or "") == "stale_price"]
    negative_execution = [row for row in executed if str(row.get("execution_value_status") or "") == "negative_ev_at_execution"]
    execution_ev_values = [safe_float(row.get("execution_expected_value_at_bet")) for row in executed]
    execution_ev_values = [value for value in execution_ev_values if value is not None]
    execution_edge_values = [safe_float(row.get("execution_edge_at_bet")) for row in executed]
    execution_edge_values = [value for value in execution_edge_values if value is not None]
    active_markets = [row for row in markets if row["tickets"] > 0]
    settled_markets = [row for row in markets if row["reconciled"] > 0]
    return {
        "tickets": len(rows),
        "reconciled": len(reconciled),
        "executed": len(executed),
        "pending": len(rows) - len(reconciled),
        "active_markets": len(active_markets),
        "settled_markets": len(settled_markets),
        "staked": round(staked, 2),
        "returned": round(returned, 2),
        "profit": round(profit, 2),
        "roi": safe_divide(profit, staked),
        "execution_staked": execution["staked"],
        "execution_returned": execution["returned"],
        "execution_profit": execution["profit"],
        "execution_roi": execution["roi"],
        "execution_max_drawdown": max_drawdown(executed, profit_key="execution_profit"),
        "valid_execution": len(valid_execution),
        "stale_price": len(stale_price),
        "negative_ev_at_execution": len(negative_execution),
        "execution_valid_rate": safe_divide(len(valid_execution), len(executed)),
        "avg_execution_expected_value_at_bet": average(execution_ev_values),
        "avg_execution_edge_at_bet": average(execution_edge_values),
        "hit_rate": safe_divide(hits, len(reconciled)),
        "best_market": best["market"] if best else None,
        "best_market_label": best["market_label"] if best else None,
        "best_market_roi": best["roi"] if best else None,
        "settlement_rate": safe_divide(len(reconciled), len(rows)),
        "execution_rate": safe_divide(len(executed), len(reconciled)),
    }


def replay_insights(
    markets: list[dict[str, Any]],
    best: dict[str, Any] | None,
    optimizer: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    by_market = {row["market"]: row for row in markets}
    insights: list[dict[str, str]] = []
    if best:
        insights.append(
            {
                "level": "focus",
                "title": f"{best['market_label']}暫時排第一",
                "body": f"已結算 ROI {format_pct(best['roi'])}，命中率 {format_pct(best['hit_rate'])}。下一步應增加樣本，確認唔係單一高派彩造成。",
            }
        )
    qpl = by_market.get("QPL")
    trio = by_market.get("TRIO")
    if qpl and qpl["reconciled"] > 0 and qpl["low_return_hits"] > 0:
        insights.append(
            {
                "level": "upgrade",
                "title": "位置Q有命中但回報偏薄",
                "body": "系統會標記中獎但利潤細嘅位置Q，之後可以比較同一組馬升級做單T是否有更好風險回報。",
            }
        )
    if qpl and trio and qpl["reconciled"] > 0 and trio["reconciled"] == 0:
        insights.append(
            {
                "level": "data",
                "title": "單T仍缺結算樣本",
                "body": "要判斷位置Q升級單T是否值得，必須持續保存單T建議及 final dividend。",
            }
        )
    executed = [row for row in markets if row.get("executed", 0) > 0]
    weak_execution = [row for row in executed if row.get("execution_roi") is not None and row.get("roi") is not None and row["execution_roi"] < row["roi"]]
    if weak_execution:
        worst = sorted(weak_execution, key=lambda row: row["execution_roi"] or -999.0)[0]
        insights.append(
            {
                "level": "risk",
                "title": f"{worst['market_label']}下注時 ROI 低過建議回測",
                "body": f"建議 ROI {format_pct(worst['roi'])}，下注時 ROI {format_pct(worst['execution_roi'])}。要檢查賠率滑價同確認時間。",
            }
        )
    optimizer_summary = (optimizer or {}).get("summary") if isinstance(optimizer, dict) else {}
    if optimizer_summary and int(optimizer_summary.get("passed_markets") or 0) > 0:
        insights.append(
            {
                "level": "focus",
                "title": "彩池 optimizer 有樣本外改善",
                "body": f"{optimizer_summary.get('passed_markets')} 個玩法的 walk-forward gate 未差過 baseline；最佳改善 {format_pct(optimizer_summary.get('best_delta_roi'))}。",
            }
        )
    if not insights:
        insights.append(
            {
                "level": "data",
                "title": "等待更多結算樣本",
                "body": "目前投注留痕不足，先累積每個 pool 的建議、派彩與賽果，先可以可靠比較玩法。",
            }
        )
    return insights


def pool_choice_optimizer(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_market = {market: [] for market in POOL_RULES}
    reconciled = [
        row
        for row in rows
        if str(row.get("reconciliation_status") or "") == "reconciled"
        and settled_stake(row) > 0
        and safe_float(row.get("profit")) is not None
    ]
    for row in reconciled:
        market = str(row.get("market") or "").upper()
        if market in by_market:
            by_market[market].append(row)
    markets = [pool_choice_optimizer_market(market, by_market[market]) for market in POOL_RULES]
    active = [row for row in markets if int(row.get("settled_tickets") or 0) > 0]
    passed = [row for row in active if row.get("optimizer_status") == "pass"]
    best = max(
        [row for row in active if row.get("delta_roi") is not None],
        key=lambda row: float(row.get("delta_roi") or -999.0),
        default=None,
    )
    return {
        "summary": {
            "settled_tickets": len(reconciled),
            "active_markets": len(active),
            "passed_markets": len(passed),
            "best_market": best.get("market") if best else None,
            "best_market_label": best.get("market_label") if best else None,
            "best_delta_roi": best.get("delta_roi") if best else None,
            "status": "pass" if passed else "sample_building" if active else "no_sample",
        },
        "markets": markets,
    }


def pool_choice_optimizer_market(market: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (str(row.get("race_date") or ""), str(row.get("race_id") or ""), str(row.get("created_at") or "")))
    min_samples = POOL_REPLAY_MIN_SAMPLES.get(market, 10)
    baseline = replay_profit_summary(ordered)
    gated = walk_forward_gate_summary(ordered, min_samples)
    delta_roi = None
    if baseline["roi"] is not None and gated["roi"] is not None:
        delta_roi = gated["roi"] - baseline["roi"]
    optimizer_status = "no_sample"
    policy = "collect"
    if len(ordered) < min_samples:
        optimizer_status = "sample_building" if ordered else "no_sample"
        policy = "collect"
    elif delta_roi is not None and delta_roi >= -0.02 and gated["max_drawdown"] >= baseline["max_drawdown"]:
        optimizer_status = "pass"
        policy = latest_optimizer_policy(ordered, min_samples)
    elif delta_roi is not None and delta_roi < -0.02:
        optimizer_status = "failed"
        policy = "do_not_optimize"
    else:
        optimizer_status = "watch"
        policy = "watch"
    return {
        "market": market,
        "market_label": POOL_LABELS_ZH.get(market, market),
        "settled_tickets": len(ordered),
        "min_samples": min_samples,
        "optimizer_status": optimizer_status,
        "policy": policy,
        "baseline_staked": baseline["staked"],
        "baseline_profit": baseline["profit"],
        "baseline_roi": baseline["roi"],
        "baseline_max_drawdown": baseline["max_drawdown"],
        "gated_staked": gated["staked"],
        "gated_profit": gated["profit"],
        "gated_roi": gated["roi"],
        "gated_max_drawdown": gated["max_drawdown"],
        "delta_roi": delta_roi,
        "kept_tickets": gated["kept_tickets"],
        "reduced_tickets": gated["reduced_tickets"],
        "blocked_tickets": gated["blocked_tickets"],
        "retention_rate": safe_divide(gated["kept_tickets"] + gated["reduced_tickets"], len(ordered)),
        "reason": optimizer_reason(market, optimizer_status, policy, delta_roi, len(ordered), min_samples),
    }


def replay_profit_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    staked = 0.0
    profit = 0.0
    curve_rows: list[dict[str, Any]] = []
    for row in rows:
        stake = settled_stake(row)
        row_profit = safe_float(row.get("profit")) or 0.0
        staked += stake
        profit += row_profit
        curve_rows.append({"profit": row_profit, "race_date": row.get("race_date"), "race_id": row.get("race_id"), "created_at": row.get("created_at")})
    return {
        "staked": round(staked, 2),
        "profit": round(profit, 2),
        "roi": safe_divide(profit, staked),
        "max_drawdown": max_drawdown(curve_rows),
    }


def walk_forward_gate_summary(rows: list[dict[str, Any]], min_samples: int) -> dict[str, Any]:
    staked = 0.0
    profit = 0.0
    kept = 0
    reduced = 0
    blocked = 0
    curve_rows: list[dict[str, Any]] = []
    prior: list[dict[str, Any]] = []
    for row in rows:
        policy = prior_policy(prior, min_samples)
        factor = {"block": 0.0, "reduce": 0.5}.get(policy, 1.0)
        if factor <= 0.0:
            blocked += 1
        elif factor < 1.0:
            reduced += 1
        else:
            kept += 1
        stake = settled_stake(row) * factor
        row_profit = (safe_float(row.get("profit")) or 0.0) * factor
        staked += stake
        profit += row_profit
        curve_rows.append({"profit": row_profit, "race_date": row.get("race_date"), "race_id": row.get("race_id"), "created_at": row.get("created_at")})
        prior.append(row)
    return {
        "staked": round(staked, 2),
        "profit": round(profit, 2),
        "roi": safe_divide(profit, staked),
        "max_drawdown": max_drawdown(curve_rows),
        "kept_tickets": kept,
        "reduced_tickets": reduced,
        "blocked_tickets": blocked,
    }


def latest_optimizer_policy(rows: list[dict[str, Any]], min_samples: int) -> str:
    return prior_policy(rows, min_samples)


def prior_policy(rows: list[dict[str, Any]], min_samples: int) -> str:
    if len(rows) < min_samples:
        return "collect"
    summary = replay_profit_summary(rows)
    roi = summary["roi"]
    if roi is not None and roi <= -0.15:
        return "block"
    if roi is not None and roi < 0:
        return "reduce"
    return "pass"


def optimizer_reason(market: str, status: str, policy: str, delta_roi: float | None, settled: int, min_samples: int) -> str:
    label = POOL_LABELS_ZH.get(market, market)
    if settled <= 0:
        return f"{label} 未有已結算樣本，optimizer 暫時只收集資料。"
    if settled < min_samples:
        return f"{label} 已結算 {settled}/{min_samples} 張，未達 walk-forward optimizer 最低樣本。"
    if status == "pass":
        return f"{label} walk-forward gate 對 baseline 未見傷害，當前政策：{optimizer_policy_label(policy)}，ROI 改善 {format_pct(delta_roi)}。"
    if status == "failed":
        return f"{label} walk-forward gate 暫時差過 baseline，暫不使用 optimizer 強化限制。"
    return f"{label} optimizer 樣本可用但仍需觀察，ROI 改善 {format_pct(delta_roi)}。"


def optimizer_policy_label(policy: str) -> str:
    return {
        "collect": "累積樣本",
        "pass": "正常",
        "reduce": "降注",
        "block": "封池",
        "watch": "觀察",
        "do_not_optimize": "不用 optimizer",
    }.get(policy, policy or "-")


def bankroll_replay_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (str(row.get("race_date") or ""), str(row.get("race_id") or ""), str(row.get("created_at") or "")))
    reconciled = [row for row in ordered if str(row.get("reconciliation_status")) == "reconciled"]
    starting_bankroll = first_bankroll(ordered)
    equity = starting_bankroll
    peak = starting_bankroll
    max_drawdown = 0.0
    staked = 0.0
    returned = 0.0
    curve = []
    for row in reconciled:
        stake = settled_stake(row)
        row_returned = safe_float(row.get("returned"))
        profit = safe_float(row.get("profit"))
        if row_returned is None and profit is not None:
            row_returned = stake + profit
        staked += stake
        returned += row_returned or 0.0
        equity += profit or 0.0
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        curve.append(
            {
                "race_date": row.get("race_date"),
                "race_id": row.get("race_id"),
                "market": row.get("market"),
                "stake": round(stake, 2),
                "profit": round(profit or 0.0, 2),
                "equity": round(equity, 2),
                "drawdown": round(equity - peak, 2),
            }
        )
    risk_audit = bankroll_risk_audit(ordered)
    return {
        "starting_bankroll": round(starting_bankroll, 2),
        "ending_bankroll": round(equity, 2),
        "settled_tickets": len(reconciled),
        "staked": round(staked, 2),
        "returned": round(returned, 2),
        "profit": round(equity - starting_bankroll, 2),
        "roi": safe_divide(equity - starting_bankroll, staked),
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_pct": safe_divide(abs(max_drawdown), starting_bankroll),
        "equity_curve": curve[-40:],
        "risk_audit": risk_audit,
    }


def bankroll_risk_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    race_exposure: dict[str, dict[str, Any]] = {}
    daily_exposure: dict[str, dict[str, Any]] = {}
    horse_exposure: dict[str, dict[str, Any]] = {}
    pool_exposure: dict[str, dict[str, Any]] = {}
    combination_exposure: dict[str, dict[str, Any]] = {}
    for row in rows:
        stake = safe_float(row.get("recommended_stake")) or 0.0
        if stake <= 0:
            continue
        caps = exposure_caps_for_row(row)
        race_id = str(row.get("race_id") or "")
        race_date = str(row.get("race_date") or "")
        market = str(row.get("market") or "")
        add_exposure(race_exposure, race_id, stake, caps["race"], {"race_id": race_id, "race_date": race_date})
        add_exposure(daily_exposure, race_date, stake, caps["daily"], {"race_date": race_date})
        add_exposure(pool_exposure, f"{race_id}:{market}", stake, caps["pool"], {"race_id": race_id, "race_date": race_date, "market": market, "market_label": row.get("market_label") or POOL_LABELS_ZH.get(market, market)})
        horse_ids = row_horse_ids(row)
        for horse_id in horse_ids:
            add_exposure(horse_exposure, f"{race_id}:{horse_id}", stake, caps["horse"], {"race_id": race_id, "race_date": race_date, "horse_id": horse_id, "horse_name": row.get("horse_name") or horse_id})
        signature = combination_signature(row)
        if signature:
            add_exposure(combination_exposure, f"{race_id}:{signature}", stake, caps["combination"], {"race_id": race_id, "race_date": race_date, "signature": signature, "label": row.get("horse_name") or signature})

    race_breaches = exposure_breaches(race_exposure.values())
    daily_breaches = exposure_breaches(daily_exposure.values())
    horse_breaches = exposure_breaches(horse_exposure.values())
    pool_breaches = exposure_breaches(pool_exposure.values())
    combination_breaches = exposure_breaches(combination_exposure.values())
    breach_count = len(race_breaches) + len(daily_breaches) + len(horse_breaches) + len(pool_breaches) + len(combination_breaches)
    return {
        "status": "breached" if breach_count else "pass",
        "breach_count": breach_count,
        "daily_max": max_exposure_row(daily_exposure.values()),
        "race_max": max_exposure_row(race_exposure.values()),
        "race_breaches": race_breaches[:8],
        "daily_breaches": daily_breaches[:8],
        "horse_breaches": horse_breaches[:8],
        "pool_breaches": pool_breaches[:8],
        "combination_breaches": combination_breaches[:8],
    }


def add_exposure(target: dict[str, dict[str, Any]], key: str, stake: float, cap: float, fields: dict[str, Any]) -> None:
    if not key:
        return
    row = target.setdefault(key, {"stake": 0.0, "cap": round(cap, 2), "ticket_count": 0, **fields})
    row["stake"] = round(float(row["stake"]) + stake, 2)
    row["cap"] = max(float(row.get("cap") or 0.0), round(cap, 2))
    row["ticket_count"] = int(row["ticket_count"]) + 1
    row["usage"] = safe_divide(float(row["stake"]), float(row["cap"]) or 0.0)


def exposure_breaches(rows: Any) -> list[dict[str, Any]]:
    return sorted(
        [
            {**row, "over_by": round(float(row.get("stake") or 0.0) - float(row.get("cap") or 0.0), 2)}
            for row in rows
            if float(row.get("stake") or 0.0) > float(row.get("cap") or 0.0) > 0
        ],
        key=lambda row: float(row.get("over_by") or 0.0),
        reverse=True,
    )


def max_exposure_row(rows: Any) -> dict[str, Any] | None:
    rows = list(rows)
    if not rows:
        return None
    return max(rows, key=lambda row: float(row.get("stake") or 0.0))


def exposure_caps_for_row(row: dict[str, Any]) -> dict[str, float]:
    profile = RISK_PROFILES.get(str(row.get("risk_profile") or "standard"), RISK_PROFILES["standard"])
    bankroll = safe_float(row.get("bankroll")) or 10_000.0
    race_cap = bankroll * profile.max_race_fraction
    return {
        "race": race_cap,
        "daily": race_cap * 3.0,
        "horse": race_cap * 0.50,
        "pool": race_cap * 0.65,
        "combination": bankroll * profile.max_bet_fraction * 1.25,
    }


def first_bankroll(rows: list[dict[str, Any]]) -> float:
    for row in rows:
        bankroll = safe_float(row.get("bankroll"))
        if bankroll and bankroll > 0:
            return bankroll
    return 10_000.0


def settled_stake(row: dict[str, Any]) -> float:
    if str(row.get("execution_status") or "") == "confirmed":
        execution_stake = safe_float(row.get("execution_stake"))
        if execution_stake is not None:
            return execution_stake
    return safe_float(row.get("recommended_stake")) or 0.0


def row_horse_ids(row: dict[str, Any]) -> list[str]:
    market = str(row.get("market") or "")
    horse_id = str(row.get("horse_id") or "")
    if market in {"WIN", "PLACE"}:
        return [horse_id] if horse_id else []
    return re.findall(r"\d+", horse_id)


def combination_signature(row: dict[str, Any]) -> str | None:
    market = str(row.get("market") or "")
    horse_id = str(row.get("horse_id") or "")
    if market in {"WIN", "PLACE"} or not horse_id:
        return None
    if market in {"FCT", "TCE", "QUARTET"}:
        return horse_id
    legs = sorted(re.findall(r"\d+", horse_id), key=lambda value: int(value))
    return "+".join(legs) if legs else horse_id


def market_verdict(tickets: int, reconciled: int, roi: float | None, hit_rate: float | None) -> str:
    if tickets == 0:
        return "no_sample"
    if reconciled == 0:
        return "pending"
    if roi is not None and roi > 0 and hit_rate is not None and hit_rate > 0:
        return "profitable"
    if roi is not None and roi > -0.1:
        return "near_break_even"
    return "losing"


def execution_replay(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    staked = 0.0
    returned = 0.0
    profit = 0.0
    for row in rows:
        stake = safe_float(row.get("execution_stake")) or 0.0
        final_odds = safe_float(row.get("final_odds"))
        outcome = int(row.get("outcome_win") or 0) == 1
        row_returned = stake * final_odds if outcome and final_odds else 0.0
        row_profit = row_returned - stake
        row["execution_returned"] = row_returned
        row["execution_profit"] = row_profit
        staked += stake
        returned += row_returned
        profit += row_profit
    return {
        "staked": round(staked, 2),
        "returned": round(returned, 2),
        "profit": round(profit, 2),
        "roi": safe_divide(profit, staked),
    }


def max_drawdown(rows: list[dict[str, Any]], profit_key: str = "profit") -> float:
    ordered = sorted(rows, key=lambda row: (str(row.get("race_date") or ""), str(row.get("race_id") or ""), str(row.get("created_at") or "")))
    peak = 0.0
    equity = 0.0
    drawdown = 0.0
    for row in ordered:
        equity += safe_float(row.get(profit_key)) or 0.0
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return round(drawdown, 2)


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def safe_divide(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def safe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def format_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"
