from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .betting import EXOTIC_PRODUCTS
from .storage import fetch_all, insert_rows


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
    existing_created = existing_created_at(conn, [row["recommendation_id"] for row in rows])
    for row in rows:
        if row["recommendation_id"] in existing_created:
            row["created_at"] = existing_created[row["recommendation_id"]]
    inserted = insert_rows(conn, "betting_recommendations", rows)
    conn.commit()
    return {"recorded": inserted, "tickets": len(tickets)}


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
    items = [public_row(dict(row)) for row in rows]
    return {
        "summary": ledger_summary(items),
        "items": items,
        "clv_note": "CLV 用建議當刻賠率對比最後/派彩賠率；香港彩池不保證鎖價，現階段用作市場驗證及 slippage 監控。",
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
        "recommended_stake": float(ticket.get("recommended_stake") or 0),
        "race_status_at_recommendation": str(payload.get("race_status") or ""),
        "action": str(ticket.get("action") or ""),
        "reason": str(ticket.get("reason") or ""),
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
        str(payload.get("risk_profile") or ""),
        str(model_path),
        stable_number(ticket.get("probability")),
        stable_number(ticket.get("odds")),
        stable_number(ticket.get("expected_value")),
        stable_number(ticket.get("recommended_stake")),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def existing_created_at(conn: sqlite3.Connection, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = fetch_all(
        conn,
        f"SELECT recommendation_id, created_at FROM betting_recommendations WHERE recommendation_id IN ({placeholders})",
        tuple(ids),
    )
    return {str(row["recommendation_id"]): str(row["created_at"]) for row in rows}


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
    stake = float(row["recommended_stake"] or 0)
    returned = stake * final_odds if outcome and final_odds else 0.0
    profit = returned - stake
    recommended_odds = optional_float(row.get("recommended_odds"))
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
        "slippage": (final_odds - recommended_odds) if recommended_odds and final_odds else None,
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
    stake = float(row["recommended_stake"] or 0)
    returned = stake * final_odds if outcome and final_odds else 0.0
    profit = returned - stake
    recommended_odds = optional_float(row.get("recommended_odds"))
    clv = recommended_odds / final_odds - 1.0 if recommended_odds and final_odds and final_odds > 0 else None
    best_finish = min((finish_by_no.get(number, 99) for number in selected), default=None)
    return {
        "final_odds": final_odds,
        "finish_position": best_finish,
        "outcome_win": 1 if outcome else 0,
        "returned": returned,
        "profit": profit,
        "clv": clv,
        "slippage": (final_odds - recommended_odds) if recommended_odds and final_odds else None,
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
    reconciled = [row for row in items if row.get("reconciliation_status") == "reconciled"]
    reconciled_staked = sum(float(row.get("recommended_stake") or 0) for row in reconciled)
    returned = sum(float(row.get("returned") or 0) for row in reconciled)
    profit = sum(float(row.get("profit") or 0) for row in reconciled)
    clv_rows = [row for row in reconciled if row.get("clv") is not None]
    return {
        "recommendations": len(items),
        "reconciled": len(reconciled),
        "pending": len(items) - len(reconciled),
        "staked": round(staked, 2),
        "reconciled_staked": round(reconciled_staked, 2),
        "returned": round(returned, 2),
        "profit": round(profit, 2),
        "roi": profit / reconciled_staked if reconciled_staked else 0.0,
        "hit_rate": sum(1 for row in reconciled if row.get("outcome_win")) / len(reconciled) if reconciled else 0.0,
        "avg_clv": sum(float(row["clv"]) for row in clv_rows) / len(clv_rows) if clv_rows else None,
    }


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    return row


def optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def stable_number(value: object) -> str:
    numeric = optional_float(value)
    return "" if numeric is None else f"{numeric:.6f}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
