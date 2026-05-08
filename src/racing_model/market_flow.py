from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from .features import late_market_flow, parse_timestamp
from .storage import FINAL_PLACE_SOURCES, fetch_all


def market_flow_report(conn: sqlite3.Connection, race_id: str) -> dict[str, Any]:
    runners = load_runners(conn, race_id)
    ticks = load_live_ticks(conn, race_id)
    flow = late_market_flow(conn, race_id)
    by_horse: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sources: set[str] = set()
    timestamps: list[str] = []
    for row in ticks:
        by_horse[str(row["horse_id"])].append(row)
        if row.get("source"):
            sources.add(str(row["source"]))
        if row.get("timestamp"):
            timestamps.append(str(row["timestamp"]))

    runner_rows = []
    for runner in runners:
        horse_id = str(runner["horse_id"])
        horse_ticks = sorted(by_horse.get(horse_id, []), key=lambda row: safe_timestamp(row.get("timestamp")))
        flow_values = flow.get(horse_id, {})
        first = horse_ticks[0] if horse_ticks else None
        latest = horse_ticks[-1] if horse_ticks else None
        latest_odds = safe_float(latest.get("win_odds")) if latest else None
        first_odds = safe_float(first.get("win_odds")) if first else None
        signal_strength = max(
            abs(float(flow_values.get("odds_delta_5m", 0.0))),
            abs(float(flow_values.get("odds_delta_2m", 0.0))),
            abs(float(flow_values.get("odds_delta_30s", 0.0))),
        )
        label = flow_label(flow_values, len(horse_ticks))
        runner_rows.append(
            {
                "race_id": race_id,
                "horse_id": horse_id,
                "horse_no": runner.get("horse_no"),
                "horse_name": runner.get("horse_name"),
                "horse_name_zh": runner.get("horse_name_zh"),
                "display_name": runner.get("display_name") or horse_id,
                "tick_count": len(horse_ticks),
                "first_win_odds": first_odds,
                "latest_win_odds": latest_odds,
                "latest_timestamp": latest.get("timestamp") if latest else None,
                "latest_source": latest.get("source") if latest else None,
                "odds_delta_5m": round(float(flow_values.get("odds_delta_5m", 0.0)), 6),
                "odds_delta_2m": round(float(flow_values.get("odds_delta_2m", 0.0)), 6),
                "odds_delta_30s": round(float(flow_values.get("odds_delta_30s", 0.0)), 6),
                "late_steam": round(float(flow_values.get("late_steam", 0.0)), 6),
                "late_drift": round(float(flow_values.get("late_drift", 0.0)), 6),
                "signal_strength": round(signal_strength, 6),
                "flow_label": label,
                "data_quality": runner_data_quality(len(horse_ticks), signal_strength),
            }
        )

    runner_rows.sort(
        key=lambda row: (
            float(row["signal_strength"]),
            int(row["tick_count"]),
            -int(row["horse_no"] or 999),
        ),
        reverse=True,
    )
    summary = flow_summary(race_id, runners, ticks, runner_rows, sources, timestamps)
    return {
        "race_id": race_id,
        "summary": summary,
        "runners": runner_rows,
        "insights": flow_insights(summary, runner_rows),
    }


def load_runners(conn: sqlite3.Connection, race_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in fetch_all(
            conn,
            """
            SELECT
              horse_id,
              horse_no,
              horse_name,
              horse_name_zh,
              COALESCE(NULLIF(horse_name_zh, ''), horse_name, horse_id) AS display_name
            FROM runners
            WHERE race_id = ?
            ORDER BY COALESCE(horse_no, draw), horse_id
            """,
            (race_id,),
        )
    ]


def load_live_ticks(conn: sqlite3.Connection, race_id: str) -> list[dict[str, Any]]:
    final_sources = tuple(FINAL_PLACE_SOURCES)
    placeholders = ",".join("?" for _ in final_sources)
    params: tuple[Any, ...] = (race_id, *final_sources)
    return [
        dict(row)
        for row in fetch_all(
            conn,
            f"""
            SELECT race_id, horse_id, timestamp, win_odds, place_odds, source
            FROM odds_ticks
            WHERE race_id = ?
              AND source NOT IN ({placeholders})
              AND win_odds > 1
            ORDER BY timestamp, horse_id
            """,
            params,
        )
    ]


def flow_summary(
    race_id: str,
    runners: list[dict[str, Any]],
    ticks: list[dict[str, Any]],
    runner_rows: list[dict[str, Any]],
    sources: set[str],
    timestamps: list[str],
) -> dict[str, Any]:
    runner_count = len(runners)
    runners_with_ticks = sum(1 for row in runner_rows if int(row["tick_count"]) > 0)
    coverage_rate = runners_with_ticks / runner_count if runner_count else None
    strong_rows = [row for row in runner_rows if float(row["signal_strength"]) >= 0.08]
    steam_rows = sorted(
        [row for row in runner_rows if row["flow_label"] == "落飛"],
        key=lambda row: float(row["late_steam"]),
        reverse=True,
    )
    drift_rows = sorted(
        [row for row in runner_rows if row["flow_label"] == "轉冷"],
        key=lambda row: float(row["late_drift"]),
    )
    return {
        "race_id": race_id,
        "runner_count": runner_count,
        "runners_with_ticks": runners_with_ticks,
        "coverage_rate": coverage_rate,
        "total_ticks": len(ticks),
        "active_sources": sorted(sources),
        "earliest_timestamp": min(timestamps) if timestamps else None,
        "latest_timestamp": max(timestamps) if timestamps else None,
        "strong_signal_count": len(strong_rows),
        "steam_count": len(steam_rows),
        "drift_count": len(drift_rows),
        "strongest_steam": steam_rows[0] if steam_rows else None,
        "strongest_drift": drift_rows[0] if drift_rows else None,
        "verdict": market_flow_verdict(runner_count, runners_with_ticks, len(ticks), len(strong_rows)),
    }


def market_flow_verdict(runner_count: int, runners_with_ticks: int, total_ticks: int, strong_count: int) -> str:
    if runner_count == 0:
        return "missing_race"
    if total_ticks == 0:
        return "no_live_ticks"
    coverage = runners_with_ticks / runner_count
    if coverage < 0.8 or total_ticks < runner_count * 2:
        return "thin_sample"
    if strong_count > 0:
        return "actionable_flow"
    return "stable_market"


def runner_data_quality(tick_count: int, signal_strength: float) -> str:
    if tick_count == 0:
        return "無 live tick"
    if tick_count == 1:
        return "只有一口價"
    if signal_strength >= 0.08:
        return "有明顯異動"
    return "可監控"


def flow_label(flow_values: dict[str, float], tick_count: int) -> str:
    if tick_count < 2:
        return "資料不足"
    strongest = max(
        [
            float(flow_values.get("odds_delta_30s", 0.0)),
            float(flow_values.get("odds_delta_2m", 0.0)),
            float(flow_values.get("odds_delta_5m", 0.0)),
        ],
        key=abs,
    )
    if strongest >= 0.025:
        return "落飛"
    if strongest <= -0.025:
        return "轉冷"
    return "平穩"


def flow_insights(summary: dict[str, Any], runner_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    verdict = summary.get("verdict")
    if verdict == "missing_race":
        return [{"level": "data", "title": "未有賽事", "body": "資料庫未找到此場賽事，無法建立資金流報告。"}]
    if verdict == "no_live_ticks":
        return [{"level": "data", "title": "未有 live 賠率", "body": "需要先啟動官方 GraphQL/MQTT 或手動刷新，否則不能判斷臨場資金流。"}]
    insights: list[dict[str, str]] = []
    coverage = summary.get("coverage_rate")
    if coverage is not None and coverage < 0.8:
        insights.append(
            {
                "level": "data",
                "title": "賠率覆蓋未足",
                "body": f"目前只有 {summary.get('runners_with_ticks', 0)}/{summary.get('runner_count', 0)} 匹馬有 live tick，先不要用資金流作主要下注理由。",
            }
        )
    steam = summary.get("strongest_steam")
    if steam:
        insights.append(
            {
                "level": "focus",
                "title": f"{steam['display_name']} 有落飛訊號",
                "body": "賠率下跌代表市場追捧，但仍要同模型勝率和 edge 比較；如果已被壓到無價值，應放棄。",
            }
        )
    drift = summary.get("strongest_drift")
    if drift:
        insights.append(
            {
                "level": "risk",
                "title": f"{drift['display_name']} 有轉冷訊號",
                "body": "賠率升高可能增加表面價值，但亦可能反映負面資訊；需要檢查狀態、臨場消息和模型盲點。",
            }
        )
    if not insights:
        insights.append(
            {
                "level": "stable",
                "title": "市場暫時平穩",
                "body": "未見明顯落飛或轉冷，投注判斷應以模型概率、期望值和風險控制為主。",
            }
        )
    return insights


def safe_timestamp(value: object) -> float:
    return parse_timestamp(value) or 0.0


def safe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
