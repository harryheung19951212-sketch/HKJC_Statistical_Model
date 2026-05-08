from __future__ import annotations

import sqlite3
from typing import Any

from .pool_rules import POOL_RULES
from .storage import fetch_all


CALIBRATION_BINS = [
    (0.00, 0.05),
    (0.05, 0.10),
    (0.10, 0.15),
    (0.15, 0.20),
    (0.20, 0.30),
    (0.30, 0.50),
    (0.50, 1.01),
]

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


def pool_calibration_report(conn: sqlite3.Connection, race_id: str | None = None) -> dict[str, Any]:
    rows = load_settled_recommendations(conn, race_id)
    by_market: dict[str, list[dict[str, Any]]] = {market: [] for market in POOL_RULES}
    for row in rows:
        market = str(row.get("market") or "").upper()
        if market in by_market:
            by_market[market].append(row)

    markets = [market_calibration(market, by_market[market]) for market in POOL_RULES]
    qualified = [row for row in markets if row["tickets"] > 0]
    worst = worst_market_calibration(qualified)
    return {
        "race_id": race_id,
        "summary": {
            "tickets": len(rows),
            "markets": len(qualified),
            "worst_market": worst.get("market") if worst else None,
            "worst_market_label": worst.get("market_label") if worst else None,
            "worst_gap": (worst.get("worst_bin") or {}).get("gap") if worst else None,
        },
        "markets": markets,
    }


def load_settled_recommendations(conn: sqlite3.Connection, race_id: str | None) -> list[dict[str, Any]]:
    where = ""
    params: tuple[Any, ...] = ()
    if race_id:
        where = "AND race_id = ?"
        params = (race_id,)
    return [
        dict(row)
        for row in fetch_all(
            conn,
            f"""
            SELECT market, probability, outcome_win, reconciliation_status
            FROM betting_recommendations
            WHERE reconciliation_status = 'reconciled'
              AND probability IS NOT NULL
              AND outcome_win IS NOT NULL
              {where}
            ORDER BY race_date, race_id, created_at, recommendation_id
            """,
            params,
        )
    ]


def market_calibration(market: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    bins = [new_bin(low, high) for low, high in CALIBRATION_BINS]
    for row in rows:
        probability = clamp_probability(row.get("probability"))
        outcome = 1.0 if int(row.get("outcome_win") or 0) == 1 else 0.0
        add_to_bins(bins, probability, outcome)
    finalized = finalize_bins(bins)
    worst_bin = worst_bin_with_sample(finalized)
    tickets = len(rows)
    expected = sum(float(row.get("probability") or 0.0) for row in rows)
    actual = sum(1 for row in rows if int(row.get("outcome_win") or 0) == 1)
    return {
        "market": market,
        "market_label": POOL_LABELS_ZH.get(market, market),
        "tickets": tickets,
        "avg_probability": expected / tickets if tickets else None,
        "observed_rate": actual / tickets if tickets else None,
        "gap": (actual - expected) / tickets if tickets else None,
        "bins": finalized,
        "worst_bin": compact_bin(worst_bin),
    }


def new_bin(low: float, high: float) -> dict[str, Any]:
    return {
        "low": low,
        "high": high,
        "label": f"{int(low * 100)}-{int(min(high, 1.0) * 100)}%",
        "count": 0,
        "expected": 0.0,
        "actual": 0.0,
    }


def add_to_bins(bins: list[dict[str, Any]], probability: float, outcome: float) -> None:
    for row in bins:
        if float(row["low"]) <= probability < float(row["high"]):
            row["count"] += 1
            row["expected"] += probability
            row["actual"] += outcome
            return


def finalize_bins(bins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in bins:
        count = int(row["count"])
        avg_prediction = float(row["expected"]) / count if count else 0.0
        observed_rate = float(row["actual"]) / count if count else 0.0
        rows.append(
            {
                "label": row["label"],
                "count": count,
                "avg_prediction": avg_prediction,
                "observed_rate": observed_rate,
                "gap": observed_rate - avg_prediction if count else 0.0,
            }
        )
    return rows


def worst_bin_with_sample(rows: list[dict[str, Any]], min_count: int = 1) -> dict[str, Any] | None:
    eligible = [row for row in rows if int(row.get("count", 0) or 0) >= min_count]
    if not eligible:
        return None
    return max(eligible, key=lambda row: abs(float(row.get("gap") or 0.0)))


def worst_market_calibration(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [row for row in rows if row.get("worst_bin")]
    if not eligible:
        return None
    return max(eligible, key=lambda row: abs(float((row.get("worst_bin") or {}).get("gap") or 0.0)))


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


def clamp_probability(value: object) -> float:
    try:
        probability = float(value)
    except (TypeError, ValueError):
        probability = 0.0
    return max(0.0, min(probability, 1.0))
