from __future__ import annotations

import sqlite3
from typing import Any

from .pool_rules import POOL_RULES, cost_adjusted_expected_value, pool_rule_payload
from .storage import fetch_all


POOL_LABELS_ZH = {
    "WIN": "獨贏",
    "PLACE": "位置",
    "QIN": "連贏",
    "QPL": "位置Q",
    "FCT": "二重彩",
    "TRIO": "單T",
    "TCE": "三連彩",
    "FIRST4": "四連環",
    "QUARTET": "四重彩",
}


def pool_replay_report(conn: sqlite3.Connection, race_id: str | None = None) -> dict[str, Any]:
    rows = load_recommendations(conn, race_id)
    market_rows = {market: [] for market in POOL_RULES}
    for row in rows:
        market = str(row.get("market") or "").upper()
        if market in market_rows:
            market_rows[market].append(row)

    markets = [market_replay(market, market_rows[market]) for market in POOL_RULES]
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
    return {
        "race_id": race_id,
        "summary": summary,
        "markets": markets,
        "ranking": ranked,
        "insights": replay_insights(markets, best),
    }


def load_recommendations(conn: sqlite3.Connection, race_id: str | None) -> list[dict[str, Any]]:
    where = ""
    params: tuple[Any, ...] = ()
    if race_id:
        where = "WHERE race_id = ?"
        params = (race_id,)
    return [
        dict(row)
        for row in fetch_all(
            conn,
            f"""
            SELECT *
            FROM betting_recommendations
            {where}
            ORDER BY race_date, race_id, created_at, recommendation_id
            """,
            params,
        )
    ]


def market_replay(market: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    reconciled = [row for row in rows if str(row.get("reconciliation_status")) == "reconciled"]
    executed = [row for row in reconciled if str(row.get("execution_status")) == "confirmed"]
    hits = [row for row in reconciled if int(row.get("outcome_win") or 0) == 1]
    staked_all = sum(safe_float(row.get("recommended_stake")) or 0.0 for row in rows)
    staked = sum(safe_float(row.get("recommended_stake")) or 0.0 for row in reconciled)
    returned = sum(safe_float(row.get("returned")) or 0.0 for row in reconciled)
    profit = sum(safe_float(row.get("profit")) or 0.0 for row in reconciled)
    execution = execution_replay(executed)
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
        "low_return_hits": len(low_return_hits),
        "max_drawdown": max_drawdown(reconciled),
        "execution_max_drawdown": max_drawdown(executed, profit_key="execution_profit"),
        "settlement_rate": safe_divide(len(reconciled), len(rows)),
        "execution_rate": safe_divide(len(executed), len(reconciled)),
        "verdict": market_verdict(len(rows), len(reconciled), roi, hit_rate),
    }


def overall_summary(rows: list[dict[str, Any]], markets: list[dict[str, Any]], best: dict[str, Any] | None) -> dict[str, Any]:
    reconciled = [row for row in rows if str(row.get("reconciliation_status")) == "reconciled"]
    executed = [row for row in reconciled if str(row.get("execution_status")) == "confirmed"]
    staked = sum(safe_float(row.get("recommended_stake")) or 0.0 for row in reconciled)
    returned = sum(safe_float(row.get("returned")) or 0.0 for row in reconciled)
    profit = sum(safe_float(row.get("profit")) or 0.0 for row in reconciled)
    execution = execution_replay(executed)
    hits = sum(1 for row in reconciled if int(row.get("outcome_win") or 0) == 1)
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
        "hit_rate": safe_divide(hits, len(reconciled)),
        "best_market": best["market"] if best else None,
        "best_market_label": best["market_label"] if best else None,
        "best_market_roi": best["roi"] if best else None,
        "settlement_rate": safe_divide(len(reconciled), len(rows)),
        "execution_rate": safe_divide(len(executed), len(reconciled)),
    }


def replay_insights(markets: list[dict[str, Any]], best: dict[str, Any] | None) -> list[dict[str, str]]:
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
    if not insights:
        insights.append(
            {
                "level": "data",
                "title": "等待更多結算樣本",
                "body": "目前投注留痕不足，先累積每個 pool 的建議、派彩與賽果，先可以可靠比較玩法。",
            }
        )
    return insights


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


def format_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"
