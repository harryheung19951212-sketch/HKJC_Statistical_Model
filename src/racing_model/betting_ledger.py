from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .betting import EXOTIC_PRODUCTS
from .storage import fetch_all, insert_rows


LIVE_ODDS_SOURCES = {"hkjc_graphql", "hkjc_mqtt"}


def record_betting_payload(
    conn: sqlite3.Connection,
    race: dict[str, Any],
    payload: dict[str, Any],
    model_path: Path | str,
    source: str = "api_betting",
) -> dict[str, Any]:
    tickets = [ticket for ticket in payload.get("tickets", []) if float(ticket.get("recommended_stake") or 0) > 0]
    if not tickets:
        return {"recorded": 0, "tickets": 0, "message": "no_active_tickets"}
    annotate_pool_choice_context(tickets, payload)
    for ticket in tickets:
        sync_ticket_current_pool_odds(conn, str(race.get("race_id") or ""), ticket)
    now = utc_now()
    rows = [
        recommendation_row(
            ticket=ticket,
            race=race,
            payload=payload,
            model_path=model_path,
            source=source,
            now=now,
        )
        for ticket in tickets
    ]
    existing_rows = existing_logical_recommendations(conn, rows)
    for row in rows:
        existing = existing_rows.get(row["recommendation_id"])
        if existing:
            row["recommendation_id"] = existing["recommendation_id"]
            preserve_existing_state(row, existing)
        ticket = next((item for item in tickets if recommendation_key(item, race, payload, model_path) == row["recommendation_id"]), None)
        if ticket is None and existing:
            ticket = next((item for item in tickets if logical_ticket_matches(item, row)), None)
        if ticket is not None:
            ticket["recommendation_id"] = row["recommendation_id"]
            ticket["execution_status"] = row.get("execution_status")
            ticket["executed_at"] = row.get("executed_at")
            ticket["execution_odds"] = row.get("execution_odds")
            ticket["execution_stake"] = row.get("execution_stake")
            ticket["execution_source"] = row.get("execution_source")
    inserted = insert_rows(conn, "betting_recommendations", rows)
    conn.commit()
    return {"recorded": inserted, "tickets": len(tickets)}


def sync_ticket_current_pool_odds(conn: sqlite3.Connection, race_id: str, ticket: dict[str, Any]) -> None:
    odds, source = current_ticket_pool_odds(conn, race_id, ticket)
    if odds is None or odds <= 1:
        return
    ticket["odds"] = odds
    ticket["odds_source"] = source
    probability = optional_float(ticket.get("probability"))
    if probability is not None:
        ticket["market_probability"] = round(1.0 / odds, 6)
        ticket["edge"] = round(probability - (1.0 / odds), 6)
        ticket["expected_value"] = round(probability * odds - 1.0, 6)


def current_ticket_pool_odds(conn: sqlite3.Connection, race_id: str, ticket: dict[str, Any]) -> tuple[float | None, str]:
    market = str(ticket.get("market") or "")
    if market in EXOTIC_PRODUCTS:
        rows = fetch_all(
            conn,
            """
            SELECT dividend, source
            FROM exotic_dividends
            WHERE race_id = ? AND market = ? AND combination_key = ? AND dividend_status = 'probable'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (race_id, market, ticket.get("horse_id")),
        )
        if rows:
            return optional_float(rows[0]["dividend"]), str(rows[0]["source"] or "exotic_dividend")
        return optional_float(ticket.get("odds")), str(ticket.get("odds_source") or "recommended")

    column = "win_odds" if market == "WIN" else "place_odds"
    rows = fetch_all(
        conn,
        f"""
        SELECT {column} AS odds, source
        FROM odds_ticks
        WHERE race_id = ? AND horse_id = ? AND {column} IS NOT NULL AND source <> 'hkjc_results_final'
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (race_id, ticket.get("horse_id")),
    )
    if rows:
        return optional_float(rows[0]["odds"]), str(rows[0]["source"] or "odds_tick")
    return optional_float(ticket.get("odds")), str(ticket.get("odds_source") or "recommended")


def betting_ledger_report(conn: sqlite3.Connection, race_id: str | None = None, limit: int = 40) -> dict[str, Any]:
    where = ""
    params: tuple[Any, ...] = (limit,)
    if race_id:
        where = "WHERE race_id = ?"
        params = (race_id, limit)
    rows = fetch_all(
        conn,
        f"""
        SELECT *
        FROM betting_recommendations
        {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        params,
    )
    raw_items = [public_row(dict(row)) for row in rows]
    items = dedupe_logical_recommendations(raw_items)[:limit]
    return {
        "summary": ledger_summary(items),
        "items": items,
        "raw_count": len(raw_items),
        "deduped_count": max(len(raw_items) - len(items), 0),
        "clv_note": "香港彩池不鎖入飛賠率；已入飛項目的賠率會跟最新 tick/派彩推進，直到開跑後以最終派彩對數。",
    }


def reconcile_betting_ledger(conn: sqlite3.Connection, race_id: str | None = None) -> dict[str, Any]:
    where = ""
    params: tuple[Any, ...] = ()
    if race_id:
        where = "WHERE race_id = ?"
        params = (race_id,)
    rows = [dict(row) for row in fetch_all(conn, f"SELECT * FROM betting_recommendations {where}", params)]
    if not rows:
        return {"checked": 0, "updated": 0, "pending": 0}

    now = utc_now()
    updated_rows = []
    pending = 0
    for row in rows:
        result = result_for_recommendation(conn, row)
        if result is None:
            pending += 1
            continue
        updated = dict(row)
        updated.update(result)
        updated["reconciled_at"] = now
        updated["updated_at"] = now
        updated["reconciliation_status"] = "reconciled"
        updated_rows.append(updated)
    if updated_rows:
        insert_rows(conn, "betting_recommendations", updated_rows)
        conn.commit()
    return {"checked": len(rows), "updated": len(updated_rows), "pending": pending}


def recommendation_row(
    ticket: dict[str, Any],
    race: dict[str, Any],
    payload: dict[str, Any],
    model_path: Path | str,
    source: str,
    now: str,
) -> dict[str, Any]:
    key = recommendation_key(ticket, race, payload, model_path)
    execution = auto_execution_payload(ticket, now)
    return {
        "recommendation_id": key,
        "created_at": now,
        "updated_at": now,
        "source": source,
        "model_path": str(model_path),
        "race_id": str(race.get("race_id") or ""),
        "race_date": str(race.get("date") or ""),
        "market": str(ticket.get("market") or ""),
        "market_label": str(ticket.get("market_label") or ""),
        "horse_id": str(ticket.get("horse_id") or ""),
        "horse_no": ticket.get("horse_no"),
        "horse_name": str(ticket.get("horse_name") or ""),
        "model_rank": int(ticket.get("model_rank") or 0),
        "risk_profile": str(payload.get("risk_profile") or ""),
        "bankroll": float(payload.get("bankroll") or 0),
        "probability": optional_float(ticket.get("probability")),
        "recommended_odds": optional_float(ticket.get("odds")),
        "odds_source": str(ticket.get("odds_source") or ""),
        "fair_odds": optional_float(ticket.get("fair_odds")),
        "market_probability": optional_float(ticket.get("market_probability")),
        "edge": optional_float(ticket.get("edge")),
        "expected_value": optional_float(ticket.get("expected_value")),
        "cost_adjusted_expected_value": optional_float(ticket.get("cost_adjusted_expected_value")),
        "required_dividend": optional_float(ticket.get("required_dividend")),
        "minimum_ticket_cost": optional_float(ticket.get("minimum_ticket_cost")),
        "pool_choice_score": optional_float(ticket.get("pool_choice_score")),
        "pool_choice_rank": optional_int(ticket.get("pool_choice_rank")),
        "pool_choice_verdict": str(ticket.get("pool_choice_verdict") or ""),
        "slip_strategy": str(ticket.get("slip_strategy") or ""),
        "slip_rank": optional_int(ticket.get("slip_rank")),
        "slip_priority_score": optional_float(ticket.get("slip_priority_score")),
        "risk_tier": str(ticket.get("risk_tier") or ""),
        "portfolio_role": str(ticket.get("portfolio_role") or ""),
        "expected_profit": optional_float(ticket.get("expected_profit")),
        "hit_probability": optional_float(ticket.get("hit_probability")),
        "recommended_stake": float(ticket.get("recommended_stake") or 0),
        "race_status_at_recommendation": str(payload.get("race_status") or ""),
        "action": str(ticket.get("action") or ""),
        "reason": str(ticket.get("reason") or ""),
        "execution_status": execution["execution_status"],
        "executed_at": execution["executed_at"],
        "execution_odds": execution["execution_odds"],
        "execution_stake": execution["execution_stake"],
        "execution_source": execution["execution_source"],
        "execution_slippage": None,
        "execution_clv": None,
        "execution_value_status": execution["execution_value_status"],
        "execution_value_message": execution["execution_value_message"],
        "execution_edge_at_bet": execution["execution_edge_at_bet"],
        "execution_expected_value_at_bet": execution["execution_expected_value_at_bet"],
        "final_odds": None,
        "finish_position": None,
        "outcome_win": None,
        "returned": None,
        "profit": None,
        "clv": None,
        "slippage": None,
        "reconciled_at": None,
        "reconciliation_status": "pending",
    }


def recommendation_key(
    ticket: dict[str, Any],
    race: dict[str, Any],
    payload: dict[str, Any],
    model_path: Path | str,
) -> str:
    parts = [
        str(race.get("race_id") or ""),
        str(ticket.get("market") or ""),
        str(ticket.get("horse_id") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def auto_execution_payload(ticket: dict[str, Any], now: str) -> dict[str, Any]:
    stake = optional_float(ticket.get("recommended_stake")) or 0.0
    odds = optional_float(ticket.get("odds"))
    if stake <= 0:
        return {
            "execution_status": "suggested",
            "executed_at": None,
            "execution_odds": None,
            "execution_stake": None,
            "execution_source": "",
            "execution_value_status": "",
            "execution_value_message": "",
            "execution_edge_at_bet": None,
            "execution_expected_value_at_bet": None,
        }
    execution_value = execution_value_check(ticket, odds)
    return {
        "execution_status": "confirmed",
        "executed_at": now,
        "execution_odds": odds,
        "execution_stake": stake,
        "execution_source": str(ticket.get("odds_source") or "auto_recommended"),
        **execution_value,
    }


def dedupe_logical_recommendations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[object, ...], dict[str, Any]] = {}
    for item in items:
        key = (
            item.get("race_id"),
            item.get("market"),
            item.get("horse_id"),
        )
        current = latest.get(key)
        if current is None or str(item.get("updated_at") or item.get("created_at") or "") >= str(
            current.get("updated_at") or current.get("created_at") or ""
        ):
            latest[key] = item
    return sorted(latest.values(), key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)


def existing_recommendations(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = fetch_all(
        conn,
        f"SELECT * FROM betting_recommendations WHERE recommendation_id IN ({placeholders})",
        tuple(ids),
    )
    return {str(row["recommendation_id"]): dict(row) for row in rows}


def existing_logical_recommendations(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    existing_by_id = existing_recommendations(conn, [str(row["recommendation_id"]) for row in rows])
    existing: dict[str, dict[str, Any]] = {}
    for row in rows:
        generated_id = str(row["recommendation_id"])
        direct = existing_by_id.get(generated_id)
        if direct:
            existing[generated_id] = direct
            continue
        matches = fetch_all(
            conn,
            """
            SELECT *
            FROM betting_recommendations
            WHERE race_id = ?
              AND market = ?
              AND horse_id = ?
            ORDER BY
              CASE WHEN execution_status = 'confirmed' THEN 0 ELSE 1 END,
              COALESCE(execution_stake, recommended_stake, 0) DESC,
              created_at DESC,
              updated_at DESC,
              recommendation_id DESC
            LIMIT 1
            """,
            (
                row.get("race_id"),
                row.get("market"),
                row.get("horse_id"),
            ),
        )
        if matches:
            existing[generated_id] = dict(matches[0])
    return existing


def logical_ticket_matches(ticket: dict[str, Any], row: dict[str, Any]) -> bool:
    return (
        str(ticket.get("market") or "") == str(row.get("market") or "")
        and str(ticket.get("horse_id") or "") == str(row.get("horse_id") or "")
    )


def preserve_existing_state(row: dict[str, Any], existing: dict[str, Any]) -> None:
    previous_execution_stake = optional_float(existing.get("execution_stake")) or 0.0
    next_execution_stake = optional_float(row.get("execution_stake")) or 0.0
    previous_recommended_stake = optional_float(existing.get("recommended_stake")) or 0.0
    next_recommended_stake = optional_float(row.get("recommended_stake")) or 0.0
    if next_recommended_stake < previous_recommended_stake:
        row["recommended_stake"] = existing.get("recommended_stake")
    automatic_existing = str(existing.get("execution_status") or "") == "confirmed" and not manual_execution(existing)
    preserve_fields = [
        "created_at",
        "execution_status",
        "executed_at",
        "final_odds",
        "finish_position",
        "outcome_win",
        "returned",
        "profit",
        "clv",
        "slippage",
        "reconciled_at",
        "reconciliation_status",
    ]
    for field in preserve_fields:
        if field in existing:
            row[field] = existing[field]
    if automatic_existing:
        if next_execution_stake > previous_execution_stake:
            row["execution_stake"] = next_execution_stake
            row["execution_source"] = "auto_add_stake"
            row["execution_value_message"] = append_message(
                row.get("execution_value_message"),
                f"同一張飛加注：${previous_execution_stake:.1f} -> ${next_execution_stake:.1f}",
            )
            row["reason"] = append_message(row.get("reason"), "同一張飛加注，已更新注碼；沒有新增重覆飛。")
        else:
            row["execution_stake"] = existing.get("execution_stake")
            row["execution_source"] = "auto_same_ticket"
            latest_odds = optional_float(row.get("execution_odds"))
            if latest_odds is not None:
                row.update(execution_value_check(row, latest_odds))
                row["execution_slippage"] = odds_delta(latest_odds, row.get("recommended_odds"))
            else:
                row["execution_odds"] = existing.get("execution_odds")
            row["execution_value_message"] = append_message(
                row.get("execution_value_message") or existing.get("execution_value_message"),
                "同一張飛刷新，保留原入飛注碼，賠率跟最新彩池更新；沒有新增重覆飛。",
            )
            row["reason"] = append_message(row.get("reason"), "同一張飛刷新，沒有新增重覆飛。")
        return
    if manual_execution(existing):
        row["execution_stake"] = existing.get("execution_stake")
    if str(existing.get("reconciliation_status") or "") == "reconciled":
        row["execution_stake"] = existing.get("execution_stake")
        for field in [
            "execution_odds",
            "execution_source",
            "execution_slippage",
            "execution_clv",
            "execution_value_status",
            "execution_value_message",
            "execution_edge_at_bet",
            "execution_expected_value_at_bet",
        ]:
            if field in existing:
                row[field] = existing[field]
        return


def manual_execution(existing: dict[str, Any]) -> bool:
    source = str(existing.get("execution_source") or "")
    if source in {"manual_confirm", "ui_confirm"}:
        return True
    executed_at = str(existing.get("executed_at") or "")
    created_at = str(existing.get("created_at") or "")
    return bool(executed_at and created_at and executed_at != created_at)


def append_message(value: object, message: str) -> str:
    text = str(value or "").strip()
    return f"{text}；{message}" if text else message


def confirm_betting_recommendation(
    conn: sqlite3.Connection,
    recommendation_id: str,
    execution_odds: float | None = None,
    execution_stake: float | None = None,
    source: str = "manual_confirm",
) -> dict[str, Any]:
    rows = fetch_all(
        conn,
        "SELECT * FROM betting_recommendations WHERE recommendation_id = ?",
        (recommendation_id,),
    )
    if not rows:
        return {"status": "missing", "message": "找不到投注建議"}

    row = dict(rows[0])
    if row.get("reconciliation_status") == "reconciled":
        return {"status": "locked", "message": "已完成派彩對數，不能再確認下注", "item": public_row(row)}

    resolved_odds = optional_float(execution_odds)
    resolved_source = source
    if resolved_odds is None:
        resolved_odds, resolved_source = current_execution_odds(conn, row)
    if resolved_odds is None or resolved_odds <= 1:
        return {"status": "no_odds", "message": "未有可確認的最新賠率", "item": public_row(row)}

    stake = optional_float(execution_stake)
    if stake is None or stake <= 0:
        stake = float(row.get("recommended_stake") or 0)
    now = utc_now()
    recommended_odds = optional_float(row.get("recommended_odds"))
    final_odds = optional_float(row.get("final_odds"))
    execution_value = execution_value_check(row, resolved_odds)
    row.update(
        {
            "updated_at": now,
            "execution_status": "confirmed",
            "executed_at": now,
            "execution_odds": resolved_odds,
            "execution_stake": stake,
            "execution_source": resolved_source,
            "execution_slippage": odds_delta(resolved_odds, recommended_odds),
            "execution_clv": (resolved_odds / final_odds - 1.0) if resolved_odds and final_odds and final_odds > 0 else None,
            **execution_value,
        }
    )
    insert_rows(conn, "betting_recommendations", [row])
    conn.commit()
    return {"status": "confirmed", "message": "已確認下注；賠率會跟最新彩池更新，直到最終派彩對數", "item": public_row(row)}


def refresh_open_betting_prices(conn: sqlite3.Connection, race_id: str | None = None) -> dict[str, Any]:
    where = "WHERE reconciliation_status != 'reconciled' AND execution_status = 'confirmed'"
    params: tuple[Any, ...] = ()
    if race_id:
        where += " AND race_id = ?"
        params = (race_id,)
    rows = [dict(row) for row in fetch_all(conn, f"SELECT * FROM betting_recommendations {where}", params)]
    updated = []
    now = utc_now()
    for row in rows:
        odds, source = current_live_execution_odds(conn, row)
        if odds is None or odds <= 1:
            continue
        old_odds = optional_float(row.get("execution_odds"))
        old_source = str(row.get("execution_source") or "")
        if old_odds == odds and old_source == source:
            continue
        row["updated_at"] = now
        row["execution_odds"] = odds
        row["execution_source"] = source
        row["execution_slippage"] = odds_delta(odds, row.get("recommended_odds"))
        row.update(execution_value_check(row, odds))
        updated.append(row)
    if updated:
        insert_rows(conn, "betting_recommendations", updated)
        conn.commit()
    return {"checked": len(rows), "updated": len(updated)}


def current_live_execution_odds(conn: sqlite3.Connection, row: dict[str, Any]) -> tuple[float | None, str]:
    market = str(row.get("market") or "")
    if market in EXOTIC_PRODUCTS:
        rows = fetch_all(
            conn,
            """
            SELECT dividend, source
            FROM exotic_dividends
            WHERE race_id = ? AND market = ? AND combination_key = ? AND dividend_status = 'probable'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (row.get("race_id"), market, row.get("horse_id")),
        )
        if rows:
            return optional_float(rows[0]["dividend"]), str(rows[0]["source"] or "exotic_dividend")
        return None, ""

    column = "win_odds" if market == "WIN" else "place_odds"
    placeholders = ", ".join("?" for _ in LIVE_ODDS_SOURCES)
    rows = fetch_all(
        conn,
        f"""
        SELECT {column} AS odds, source
        FROM odds_ticks
        WHERE race_id = ? AND horse_id = ? AND {column} IS NOT NULL AND source IN ({placeholders})
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (row.get("race_id"), row.get("horse_id"), *sorted(LIVE_ODDS_SOURCES)),
    )
    if rows:
        return optional_float(rows[0]["odds"]), str(rows[0]["source"] or "odds_tick")
    return None, ""


def current_execution_odds(conn: sqlite3.Connection, row: dict[str, Any]) -> tuple[float | None, str]:
    return current_live_execution_odds(conn, row)


def annotate_pool_choice_context(tickets: list[dict[str, Any]], payload: dict[str, Any]) -> None:
    market_rows = list((payload.get("pool_choice") or {}).get("markets") or [])
    by_market = {str(row.get("market") or ""): row for row in market_rows}
    rank_by_market = {str(row.get("market") or ""): index for index, row in enumerate(market_rows, start=1)}
    for ticket in tickets:
        market = str(ticket.get("market") or "")
        context = by_market.get(market, {})
        if context:
            ticket.setdefault("pool_choice_score", context.get("choice_score"))
            ticket.setdefault("pool_choice_rank", rank_by_market.get(market))
            ticket.setdefault("pool_choice_verdict", context.get("verdict"))
            ticket.setdefault("required_dividend", context.get("best_required_dividend"))
            ticket.setdefault("minimum_ticket_cost", context.get("best_minimum_ticket_cost"))
        ticket.setdefault("cost_adjusted_expected_value", ticket.get("expected_value"))


def execution_value_check(row: dict[str, Any], execution_odds: float | None) -> dict[str, Any]:
    probability = optional_float(row.get("probability"))
    required_dividend = optional_float(row.get("required_dividend"))
    if execution_odds is None or execution_odds <= 1:
        return {
            "execution_value_status": "no_execution_odds",
            "execution_value_message": "未有最新賠率",
            "execution_edge_at_bet": None,
            "execution_expected_value_at_bet": None,
        }
    expected_at_bet = probability * execution_odds - 1.0 if probability is not None else None
    edge_at_bet = probability - (1.0 / execution_odds) if probability is not None else None
    if required_dividend is not None and execution_odds < required_dividend:
        status = "stale_price"
        message = f"最新賠率 {execution_odds:.2f} 低過所需 {required_dividend:.2f}，應標記為不合格執行。"
    elif expected_at_bet is not None and expected_at_bet <= 0:
        status = "negative_ev_at_execution"
        message = "最新賠率已跌至負期望值。"
    else:
        status = "valid_execution"
        message = "最新賠率仍符合建議條件。"
    return {
        "execution_value_status": status,
        "execution_value_message": message,
        "execution_edge_at_bet": round(edge_at_bet, 6) if edge_at_bet is not None else None,
        "execution_expected_value_at_bet": round(expected_at_bet, 6) if expected_at_bet is not None else None,
    }


def result_for_recommendation(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any] | None:
    if str(row.get("market")) in EXOTIC_PRODUCTS:
        return result_for_exotic_recommendation(conn, row)
    result_rows = fetch_all(
        conn,
        "SELECT finish_position FROM results WHERE race_id = ? AND horse_id = ?",
        (row["race_id"], row["horse_id"]),
    )
    if not result_rows:
        return None
    final_odds = final_odds_for_market(conn, str(row["race_id"]), str(row["horse_id"]), str(row["market"]))
    finish_position = int(result_rows[0]["finish_position"])
    market = str(row["market"])
    outcome = finish_position == 1 if market == "WIN" else finish_position <= 3
    if outcome and final_odds is None:
        return None
    stake = optional_float(row.get("execution_stake")) or float(row["recommended_stake"] or 0)
    returned = stake * final_odds if outcome and final_odds else 0.0
    profit = returned - stake
    recommended_odds = optional_float(row.get("recommended_odds"))
    execution_odds = optional_float(row.get("execution_odds"))
    clv = None
    if recommended_odds and final_odds and final_odds > 0:
        clv = recommended_odds / final_odds - 1.0
    return {
        "final_odds": final_odds,
        "finish_position": finish_position,
        "outcome_win": 1 if outcome else 0,
        "returned": returned,
        "profit": profit,
        "clv": clv,
        "execution_clv": (execution_odds / final_odds - 1.0) if execution_odds and final_odds and final_odds > 0 else None,
        "slippage": odds_delta(final_odds, recommended_odds),
    }


def result_for_exotic_recommendation(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any] | None:
    result_rows = fetch_all(
        conn,
        """
        SELECT ru.horse_no, x.finish_position
        FROM results x
        JOIN runners ru ON ru.race_id = x.race_id AND ru.horse_id = x.horse_id
        WHERE x.race_id = ?
        """,
        (row["race_id"],),
    )
    if not result_rows:
        return None
    market = str(row["market"])
    selected = parse_combination_key(str(row["horse_id"]))
    if not selected:
        return None
    finish_by_no = {int(result["horse_no"]): int(result["finish_position"]) for result in result_rows if result["horse_no"]}
    outcome = exotic_outcome(market, selected, finish_by_no)
    final_odds = final_exotic_dividend(conn, str(row["race_id"]), market, str(row["horse_id"]))
    if outcome and final_odds is None:
        return None
    stake = optional_float(row.get("execution_stake")) or float(row["recommended_stake"] or 0)
    returned = stake * final_odds if outcome and final_odds else 0.0
    profit = returned - stake
    recommended_odds = optional_float(row.get("recommended_odds"))
    execution_odds = optional_float(row.get("execution_odds"))
    clv = recommended_odds / final_odds - 1.0 if recommended_odds and final_odds and final_odds > 0 else None
    best_finish = min((finish_by_no.get(number, 99) for number in selected), default=None)
    return {
        "final_odds": final_odds,
        "finish_position": best_finish,
        "outcome_win": 1 if outcome else 0,
        "returned": returned,
        "profit": profit,
        "clv": clv,
        "execution_clv": (execution_odds / final_odds - 1.0) if execution_odds and final_odds and final_odds > 0 else None,
        "slippage": odds_delta(final_odds, recommended_odds),
    }


def final_odds_for_market(conn: sqlite3.Connection, race_id: str, horse_id: str, market: str) -> float | None:
    column = "win_odds" if market == "WIN" else "place_odds"
    rows = fetch_all(
        conn,
        f"""
        SELECT {column} AS odds
        FROM odds_ticks
        WHERE race_id = ? AND horse_id = ? AND source = 'hkjc_results_final' AND {column} IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (race_id, horse_id),
    )
    if not rows or rows[0]["odds"] is None:
        return None
    return float(rows[0]["odds"])


def final_exotic_dividend(conn: sqlite3.Connection, race_id: str, market: str, combination_key: str) -> float | None:
    rows = fetch_all(
        conn,
        """
        SELECT dividend
        FROM exotic_dividends
        WHERE race_id = ? AND market = ? AND combination_key = ? AND dividend_status = 'final'
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (race_id, market, combination_key),
    )
    if not rows:
        return None
    return float(rows[0]["dividend"])


def exotic_outcome(market: str, selected: list[int], finish_by_no: dict[int, int]) -> bool:
    if any(number not in finish_by_no for number in selected):
        return False
    finishes = [finish_by_no[number] for number in selected]
    if market == "QIN":
        return set(finishes) == {1, 2}
    if market == "QPL":
        return all(position <= 3 for position in finishes)
    if market == "FCT":
        return finishes == [1, 2]
    if market == "TRIO":
        return set(finishes) == {1, 2, 3}
    if market == "TCE":
        return finishes == [1, 2, 3]
    if market == "FIRST4":
        return set(finishes) == {1, 2, 3, 4}
    if market == "QUARTET":
        return finishes == [1, 2, 3, 4]
    return False


def parse_combination_key(value: str) -> list[int]:
    normalized = value.replace(">", "+")
    numbers = []
    for part in normalized.split("+"):
        if part.strip().isdigit():
            numbers.append(int(part.strip()))
    return numbers


def ledger_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    staked = sum(float(row.get("recommended_stake") or 0) for row in items)
    confirmed = [row for row in items if row.get("execution_status") == "confirmed"]
    valid_execution = [row for row in confirmed if row.get("execution_value_status") == "valid_execution"]
    stale_price = [row for row in confirmed if row.get("execution_value_status") == "stale_price"]
    negative_execution = [row for row in confirmed if row.get("execution_value_status") == "negative_ev_at_execution"]
    execution_ev_rows = [row for row in confirmed if row.get("execution_expected_value_at_bet") is not None]
    execution_edge_rows = [row for row in confirmed if row.get("execution_edge_at_bet") is not None]
    executed_staked = sum(float(row.get("execution_stake") or 0) for row in confirmed)
    reconciled = [row for row in items if row.get("reconciliation_status") == "reconciled"]
    reconciled_staked = sum(float(row.get("execution_stake") or row.get("recommended_stake") or 0) for row in reconciled)
    returned = sum(float(row.get("returned") or 0) for row in reconciled)
    profit = sum(float(row.get("profit") or 0) for row in reconciled)
    clv_rows = [row for row in reconciled if row.get("clv") is not None]
    return {
        "recommendations": len(items),
        "confirmed": len(confirmed),
        "valid_execution": len(valid_execution),
        "stale_price": len(stale_price),
        "negative_ev_at_execution": len(negative_execution),
        "execution_valid_rate": len(valid_execution) / len(confirmed) if confirmed else 0.0,
        "avg_execution_expected_value_at_bet": (
            sum(float(row["execution_expected_value_at_bet"]) for row in execution_ev_rows) / len(execution_ev_rows)
            if execution_ev_rows
            else None
        ),
        "avg_execution_edge_at_bet": (
            sum(float(row["execution_edge_at_bet"]) for row in execution_edge_rows) / len(execution_edge_rows)
            if execution_edge_rows
            else None
        ),
        "reconciled": len(reconciled),
        "pending": len(items) - len(reconciled),
        "staked": round(staked, 2),
        "executed_staked": round(executed_staked, 2),
        "reconciled_staked": round(reconciled_staked, 2),
        "returned": round(returned, 2),
        "profit": round(profit, 2),
        "roi": profit / reconciled_staked if reconciled_staked else 0.0,
        "hit_rate": sum(1 for row in reconciled if row.get("outcome_win")) / len(reconciled) if reconciled else 0.0,
        "avg_clv": sum(float(row["clv"]) for row in clv_rows) / len(clv_rows) if clv_rows else None,
    }


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    source = str(row.get("execution_source") or "")
    if source == "auto_add_stake":
        row["ticket_update_label"] = "同飛加注"
    elif source == "auto_same_ticket":
        row["ticket_update_label"] = "同飛刷新"
    else:
        row["ticket_update_label"] = ""
    return row


def optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def odds_delta(new_odds: object, base_odds: object) -> float | None:
    new_value = optional_float(new_odds)
    base_value = optional_float(base_odds)
    if new_value is None or base_value is None:
        return None
    return round(new_value - base_value, 6)


def stable_number(value: object) -> str:
    numeric = optional_float(value)
    return "" if numeric is None else f"{numeric:.6f}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
