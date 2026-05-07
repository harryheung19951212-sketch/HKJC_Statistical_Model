from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from .betting import EXOTIC_PRODUCTS
from .storage import fetch_all, insert_rows


def upsert_exotic_dividends(
    conn: sqlite3.Connection,
    race_id: str,
    rows: list[dict[str, Any]],
    source: str = "manual",
    dividend_status: str = "probable",
) -> dict[str, Any]:
    now = utc_now()
    clean_rows = []
    for row in rows:
        market = str(row.get("market") or "").upper()
        numbers = row.get("horse_numbers") or row.get("combination") or row.get("combination_key")
        key = normalize_combination_key(market, numbers)
        if not market or market not in EXOTIC_PRODUCTS or not key:
            continue
        dividend = safe_float(row.get("dividend"))
        if dividend is None or dividend <= 0:
            continue
        clean_rows.append(
            {
                "race_id": race_id,
                "market": market,
                "combination_key": key,
                "combination": str(row.get("combination") or key),
                "dividend": dividend,
                "dividend_status": str(row.get("dividend_status") or dividend_status),
                "source": str(row.get("source") or source),
                "fetched_at": str(row.get("fetched_at") or now),
                "updated_at": now,
                "notes": str(row.get("notes") or ""),
            }
        )
    inserted = insert_rows(conn, "exotic_dividends", clean_rows)
    conn.commit()
    return {"race_id": race_id, "imported": inserted, "rows": clean_rows}


def exotic_dividend_report(conn: sqlite3.Connection, race_id: str) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in fetch_all(
            conn,
            """
            SELECT *
            FROM exotic_dividends
            WHERE race_id = ?
            ORDER BY market, combination_key, dividend_status
            """,
            (race_id,),
        )
    ]
    return {
        "race_id": race_id,
        "count": len(rows),
        "items": rows,
    }


def exotic_dividend_lookup(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    priority = {"probable": 3, "estimated": 2, "final": 1}
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["market"]), str(row["combination_key"]))
        current = output.get(key)
        if current is None or priority.get(str(row.get("dividend_status")), 0) > priority.get(
            str(current.get("dividend_status")),
            0,
        ):
            output[key] = row
    return output


def load_exotic_dividend_lookup(conn: sqlite3.Connection, race_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    return exotic_dividend_report(conn, race_id)["by_key"]


def normalize_combination_key(market: str, value: object) -> str:
    market = market.upper()
    ordered = bool(EXOTIC_PRODUCTS.get(market, {}).get("ordered"))
    numbers = parse_numbers(value)
    if not numbers:
        return ""
    if not ordered:
        numbers = sorted(numbers)
    separator = ">" if ordered else "+"
    return separator.join(str(number) for number in numbers)


def parse_numbers(value: object) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        output = []
        for item in value:
            try:
                output.append(int(item))
            except (TypeError, ValueError):
                continue
        return output
    text = str(value)
    normalized = text.replace(">", "+").replace(",", "+").replace("/", "+").replace("-", "+")
    output = []
    for part in normalized.split("+"):
        part = part.strip()
        if part.isdigit():
            output.append(int(part))
    return output


def safe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
