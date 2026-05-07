from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .features import build_race_features
from .model import RankingModel
from .storage import fetch_all


@dataclass(frozen=True)
class BacktestResult:
    bets: int
    wins: int
    staked: float
    returned: float
    roi: float
    hit_rate: float
    place_bets: int
    place_wins: int
    place_staked: float
    place_returned: float
    place_roi: float
    place_hit_rate: float


def run_backtest(
    conn: sqlite3.Connection,
    model: RankingModel,
    min_expected_value: float = 0.05,
    stake: float = 10.0,
) -> BacktestResult:
    race_ids = [
        row["race_id"]
        for row in fetch_all(
            conn,
            """
            SELECT r.race_id
            FROM races r
            WHERE EXISTS (SELECT 1 FROM results x WHERE x.race_id = r.race_id)
            ORDER BY r.date, r.race_id
            """,
        )
    ]
    bets = 0
    wins = 0
    returned = 0.0
    place_bets = 0
    place_wins = 0
    place_returned = 0.0
    for race_id in race_ids:
        runners = build_race_features(conn, race_id)
        predictions = model.predict_race(runners)
        result_by_horse = {runner.horse_id: runner.finish_position for runner in runners}
        for prediction in predictions:
            ev = prediction["expected_value"]
            if ev is not None and float(ev) >= min_expected_value and prediction["latest_win_odds"] is not None:
                bets += 1
                if result_by_horse[prediction["horse_id"]] == 1:
                    wins += 1
                    returned += stake * float(prediction["latest_win_odds"])
            place_ev = prediction.get("top3_expected_value")
            place_odds = prediction.get("place_odds")
            place_source = prediction.get("place_odds_source")
            if place_ev is None or float(place_ev) < min_expected_value:
                continue
            if place_odds is None:
                continue
            if place_source not in {"hkjc_graphql", "hkjc_mqtt"}:
                continue
            place_bets += 1
            finish_position = result_by_horse[prediction["horse_id"]]
            if finish_position is not None and int(finish_position) <= 3:
                place_wins += 1
                place_returned += stake * float(place_odds)
    staked = bets * stake
    profit = returned - staked
    place_staked = place_bets * stake
    place_profit = place_returned - place_staked
    return BacktestResult(
        bets=bets,
        wins=wins,
        staked=staked,
        returned=returned,
        roi=(profit / staked) if staked else 0.0,
        hit_rate=(wins / bets) if bets else 0.0,
        place_bets=place_bets,
        place_wins=place_wins,
        place_staked=place_staked,
        place_returned=place_returned,
        place_roi=(place_profit / place_staked) if place_staked else 0.0,
        place_hit_rate=(place_wins / place_bets) if place_bets else 0.0,
    )
