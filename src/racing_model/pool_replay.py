from __future__ import annotations

import sqlite3
import re
from typing import Any

from .betting import RISK_PROFILES
from .betting_ledger import dedupe_logical_recommendations
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
    bankroll_replay = bankroll_replay_report(rows)
    return {
        "race_id": race_id,
        "summary": summary,
        "markets": markets,
        "ranking": ranked,
        "bankroll_replay": bankroll_replay,
        "insights": replay_insights(markets, best),
    }


def load_recommendations(conn: sqlite3.Connection, race_id: str | None) -> list[dict[str, Any]]:
    where = ""
    params: tuple[Any, ...] = ()
    if race_id:
        where = "WHERE race_id = ?"
        params = (race_id,)
    rows = [
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
    return dedupe_logical_recommendations(rows)


def market_replay(market: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
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


def format_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"
