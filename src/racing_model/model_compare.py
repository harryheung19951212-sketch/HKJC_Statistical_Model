from __future__ import annotations

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
