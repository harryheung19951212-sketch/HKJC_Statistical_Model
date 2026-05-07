from __future__ import annotations

import math
import sqlite3
from typing import Any

from .features import FEATURE_NAMES, LATE_MARKET_FLOW_FEATURES, build_race_features
from .model import RankingModel
from .storage import fetch_all


MARKET_FEATURES = {"market_implied", *LATE_MARKET_FLOW_FEATURES}
ABILITY_FEATURES = [name for name in FEATURE_NAMES if name not in MARKET_FEATURES]


def dual_model_comparison(conn: sqlite3.Connection, model: RankingModel, race_id: str) -> dict[str, Any]:
    race_rows = fetch_all(conn, "SELECT * FROM races WHERE race_id = ?", (race_id,))
    if not race_rows:
        return {"race": None, "summary": {}, "runners": []}
    runners = build_race_features(conn, race_id)
    market_rows = model.predict_race(runners)
    ability_model = masked_model(model, ABILITY_FEATURES)
    ability_rows = ability_model.predict_race(runners)
    market_by_horse = {str(row["horse_id"]): dict(row) for row in market_rows}
    ability_by_horse = {str(row["horse_id"]): dict(row) for row in ability_rows}
    result_by_horse = {
        str(row["horse_id"]): int(row["finish_position"])
        for row in fetch_all(conn, "SELECT horse_id, finish_position FROM results WHERE race_id = ?", (race_id,))
    }
    rows = []
    for horse_id, market in market_by_horse.items():
        ability = ability_by_horse.get(horse_id, {})
        market_rank = rank_of(market_rows, horse_id)
        ability_rank = rank_of(ability_rows, horse_id)
        rows.append(
            {
                "horse_id": horse_id,
                "horse_no": market.get("horse_no"),
                "display_name": market.get("display_name") or market.get("horse_name") or horse_id,
                "draw": market.get("draw"),
                "jockey": market.get("display_jockey") or market.get("jockey"),
                "trainer": market.get("display_trainer") or market.get("trainer"),
                "ability_rank": ability_rank,
                "market_rank": market_rank,
                "rank_delta": (market_rank or 0) - (ability_rank or 0),
                "ability_win_probability": ability.get("win_probability"),
                "market_win_probability": market.get("win_probability"),
                "ability_top3_probability": ability.get("top3_probability"),
                "market_top3_probability": market.get("top3_probability"),
                "latest_win_odds": market.get("latest_win_odds"),
                "market_probability": market.get("market_probability"),
                "expected_value": market.get("expected_value"),
                "finish_position": result_by_horse.get(horse_id),
            }
        )
    rows.sort(key=lambda row: (int(row["ability_rank"] or 999), int(row["market_rank"] or 999)))
    top_ability = ability_rows[0] if ability_rows else None
    top_market = market_rows[0] if market_rows else None
    summary = {
        "ability_features": len(ABILITY_FEATURES),
        "market_features_removed": sorted(MARKET_FEATURES),
        "runner_count": len(rows),
        "top_ability_horse_id": top_ability.get("horse_id") if top_ability else None,
        "top_ability_name": top_ability.get("display_name") if top_ability else None,
        "top_market_horse_id": top_market.get("horse_id") if top_market else None,
        "top_market_name": top_market.get("display_name") if top_market else None,
        "top_pick_same": bool(top_ability and top_market and top_ability.get("horse_id") == top_market.get("horse_id")),
        "rank_disagreement": sum(abs(int(row["rank_delta"] or 0)) for row in rows),
        "max_rank_swing": max((abs(int(row["rank_delta"] or 0)) for row in rows), default=0),
    }
    return {
        "race": dict(race_rows[0]),
        "summary": summary,
        "runners": rows,
        "tracks": {
            "ability": [compact_prediction(row, index + 1) for index, row in enumerate(ability_rows)],
            "market": [compact_prediction(row, index + 1) for index, row in enumerate(market_rows)],
        },
    }


def dual_model_backtest(conn: sqlite3.Connection, model: RankingModel, max_races: int = 200) -> dict[str, Any]:
    race_rows = fetch_all(
        conn,
        """
        SELECT r.*
        FROM races r
        WHERE EXISTS (
          SELECT 1
          FROM results re
          WHERE re.race_id = r.race_id AND re.finish_position = 1
        )
        ORDER BY r.date, r.race_id
        LIMIT ?
        """,
        (max_races,),
    )
    records: list[dict[str, Any]] = []
    totals = {
        "runner_count": 0,
        "top_pick_same_count": 0,
        "ability_top_pick_wins": 0,
        "market_top_pick_wins": 0,
        "ability_top_pick_places": 0,
        "market_top_pick_places": 0,
        "ability_winner_rank_total": 0.0,
        "market_winner_rank_total": 0.0,
        "ability_brier_total": 0.0,
        "market_brier_total": 0.0,
        "ability_log_loss_total": 0.0,
        "market_log_loss_total": 0.0,
        "ability_disagreement_edge": 0,
        "market_disagreement_edge": 0,
        "tie_disagreements": 0,
    }
    for race_row in race_rows:
        comparison = dual_model_comparison(conn, model, str(race_row["race_id"]))
        rows = comparison.get("runners", [])
        if not rows:
            continue
        winner = next((row for row in rows if row.get("finish_position") == 1), None)
        ability_top = min(rows, key=lambda row: int(row.get("ability_rank") or 999))
        market_top = min(rows, key=lambda row: int(row.get("market_rank") or 999))
        if not winner:
            continue

        ability_top_finish = finish_position(ability_top)
        market_top_finish = finish_position(market_top)
        ability_winner_rank = int(winner.get("ability_rank") or 999)
        market_winner_rank = int(winner.get("market_rank") or 999)
        top_pick_same = bool((comparison.get("summary") or {}).get("top_pick_same"))
        brier = race_brier(rows)
        log_loss = winner_log_loss(winner)

        totals["runner_count"] += len(rows)
        totals["top_pick_same_count"] += int(top_pick_same)
        totals["ability_top_pick_wins"] += int(ability_top_finish == 1)
        totals["market_top_pick_wins"] += int(market_top_finish == 1)
        totals["ability_top_pick_places"] += int(ability_top_finish is not None and ability_top_finish <= 3)
        totals["market_top_pick_places"] += int(market_top_finish is not None and market_top_finish <= 3)
        totals["ability_winner_rank_total"] += ability_winner_rank
        totals["market_winner_rank_total"] += market_winner_rank
        totals["ability_brier_total"] += brier["ability"]
        totals["market_brier_total"] += brier["market"]
        totals["ability_log_loss_total"] += log_loss["ability"]
        totals["market_log_loss_total"] += log_loss["market"]
        if not top_pick_same:
            if ability_top_finish is not None and market_top_finish is not None and ability_top_finish < market_top_finish:
                totals["ability_disagreement_edge"] += 1
            elif market_top_finish is not None and ability_top_finish is not None and market_top_finish < ability_top_finish:
                totals["market_disagreement_edge"] += 1
            else:
                totals["tie_disagreements"] += 1

        records.append(
            {
                "race_id": race_row["race_id"],
                "date": race_row["date"],
                "track": race_row["track"],
                "course": race_row["course"],
                "distance_m": race_row["distance_m"],
                "ability_top": compact_backtest_runner(ability_top),
                "market_top": compact_backtest_runner(market_top),
                "winner": compact_backtest_runner(winner),
                "ability_winner_rank": ability_winner_rank,
                "market_winner_rank": market_winner_rank,
                "ability_top_finish": ability_top_finish,
                "market_top_finish": market_top_finish,
                "top_pick_same": top_pick_same,
                "rank_disagreement": (comparison.get("summary") or {}).get("rank_disagreement", 0),
                "max_rank_swing": (comparison.get("summary") or {}).get("max_rank_swing", 0),
                "verdict": race_verdict(top_pick_same, ability_top_finish, market_top_finish),
            }
        )

    race_count = len(records)
    summary = {
        "races": race_count,
        "runner_count": totals["runner_count"],
        "top_pick_same_count": totals["top_pick_same_count"],
        "top_pick_same_rate": safe_divide(totals["top_pick_same_count"], race_count),
        "disagreement_count": race_count - totals["top_pick_same_count"],
        "ability_top_pick_wins": totals["ability_top_pick_wins"],
        "market_top_pick_wins": totals["market_top_pick_wins"],
        "ability_win_rate": safe_divide(totals["ability_top_pick_wins"], race_count),
        "market_win_rate": safe_divide(totals["market_top_pick_wins"], race_count),
        "ability_top_pick_places": totals["ability_top_pick_places"],
        "market_top_pick_places": totals["market_top_pick_places"],
        "ability_place_rate": safe_divide(totals["ability_top_pick_places"], race_count),
        "market_place_rate": safe_divide(totals["market_top_pick_places"], race_count),
        "avg_ability_winner_rank": safe_divide(totals["ability_winner_rank_total"], race_count),
        "avg_market_winner_rank": safe_divide(totals["market_winner_rank_total"], race_count),
        "ability_brier": safe_divide(totals["ability_brier_total"], race_count),
        "market_brier": safe_divide(totals["market_brier_total"], race_count),
        "ability_log_loss": safe_divide(totals["ability_log_loss_total"], race_count),
        "market_log_loss": safe_divide(totals["market_log_loss_total"], race_count),
        "ability_disagreement_edge": totals["ability_disagreement_edge"],
        "market_disagreement_edge": totals["market_disagreement_edge"],
        "tie_disagreements": totals["tie_disagreements"],
        "market_features_removed": sorted(MARKET_FEATURES),
    }
    return {
        "summary": summary,
        "recommendation": backtest_recommendation(summary),
        "recent_races": records[-20:][::-1],
    }


def masked_model(model: RankingModel, feature_names: list[str]) -> RankingModel:
    names = [name for name in feature_names if name in model.feature_names]
    return RankingModel(
        feature_names=names,
        weights={name: float(model.weights.get(name, 0.0)) for name in names},
        means={name: float(model.means.get(name, 0.0)) for name in names},
        scales={name: float(model.scales.get(name, 1.0)) for name in names},
        bias=model.bias,
        top3_weights={name: float((model.top3_weights or {}).get(name, 0.0)) for name in names},
        top3_bias=model.top3_bias,
        top3_model_trained=model.top3_model_trained,
    )


def rank_of(rows: list[dict[str, Any]], horse_id: str) -> int | None:
    for index, row in enumerate(rows, start=1):
        if str(row["horse_id"]) == horse_id:
            return index
    return None


def compact_prediction(row: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "horse_id": row.get("horse_id"),
        "horse_no": row.get("horse_no"),
        "display_name": row.get("display_name") or row.get("horse_name"),
        "win_probability": row.get("win_probability"),
        "top3_probability": row.get("top3_probability"),
    }


def compact_backtest_runner(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "horse_id": row.get("horse_id"),
        "horse_no": row.get("horse_no"),
        "display_name": row.get("display_name"),
        "finish_position": row.get("finish_position"),
        "ability_rank": row.get("ability_rank"),
        "market_rank": row.get("market_rank"),
    }


def finish_position(row: dict[str, Any]) -> int | None:
    value = row.get("finish_position")
    return int(value) if value is not None else None


def race_brier(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {"ability": 0.0, "market": 0.0}
    ability_total = 0.0
    market_total = 0.0
    for row in rows:
        target = 1.0 if row.get("finish_position") == 1 else 0.0
        ability_total += (float(row.get("ability_win_probability") or 0.0) - target) ** 2
        market_total += (float(row.get("market_win_probability") or 0.0) - target) ** 2
    return {"ability": ability_total / len(rows), "market": market_total / len(rows)}


def winner_log_loss(winner: dict[str, Any]) -> dict[str, float]:
    return {
        "ability": -math.log(clamp_probability(winner.get("ability_win_probability"))),
        "market": -math.log(clamp_probability(winner.get("market_win_probability"))),
    }


def clamp_probability(value: object) -> float:
    number = float(value or 0.0)
    return min(max(number, 1e-9), 1.0)


def safe_divide(numerator: float, denominator: int) -> float | None:
    return (numerator / denominator) if denominator else None


def race_verdict(top_pick_same: bool, ability_finish: int | None, market_finish: int | None) -> str:
    if top_pick_same:
        return "same_pick"
    if ability_finish is not None and market_finish is not None:
        if ability_finish < market_finish:
            return "ability_better"
        if market_finish < ability_finish:
            return "market_better"
    return "tie"


def backtest_recommendation(summary: dict[str, Any]) -> dict[str, str]:
    races = int(summary.get("races") or 0)
    if races < 5:
        return {
            "verdict": "insufficient",
            "message": "已完賽樣本仍然太少，暫時只可作技術驗證，未適合用來決定模型方向。",
            "action": "先累積更多已完賽場次，再用同一份報告判斷賠率訊號是否真正增加命中率。",
        }
    ability_win = float(summary.get("ability_win_rate") or 0.0)
    market_win = float(summary.get("market_win_rate") or 0.0)
    ability_brier = float(summary.get("ability_brier") or 0.0)
    market_brier = float(summary.get("market_brier") or 0.0)
    if ability_win > market_win + 0.03 and ability_brier <= market_brier:
        return {
            "verdict": "ability_leads",
            "message": "純能力軌暫時較穩，賠率訊號未有明顯提高勝出命中率。",
            "action": "下一輪迭代應降低市場權重，優先加強馬匹能力、場地、步速與狀態特徵。",
        }
    if market_win > ability_win + 0.03 and market_brier <= ability_brier:
        return {
            "verdict": "market_leads",
            "message": "市場融合軌暫時較準，賠率變化對結果有可量化貢獻。",
            "action": "下一輪迭代可以保留賠率訊號，但要繼續用回測防止過度追熱門。",
        }
    return {
        "verdict": "mixed",
        "message": "兩條軌道未拉開明顯差距，現階段應保持雙軌並行。",
        "action": "優先擴大樣本和建立分場景回測，例如泥地、短途、冷熱門分組後再決定權重。",
    }
