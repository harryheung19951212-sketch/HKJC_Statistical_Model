from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from .storage import fetch_all


PACE_FRONT = {"leader", "pace"}
PACE_CLOSER = {"closer"}


@dataclass(frozen=True)
class TrackBiasSnapshot:
    race_id: str
    source_race_ids: list[str]
    inside_bias: float
    outside_bias: float
    front_bias: float
    closer_bias: float
    favorite_underperformance: float
    longshot_uplift: float


def same_day_track_bias(conn: sqlite3.Connection, race_id: str) -> TrackBiasSnapshot:
    target_rows = fetch_all(conn, "SELECT * FROM races WHERE race_id = ?", (race_id,))
    if not target_rows:
        return empty_bias(race_id)
    target = dict(target_rows[0])
    target_sequence = race_sequence(str(target["race_id"]))
    candidate_rows = fetch_all(
        conn,
        """
        SELECT r.*
        FROM races r
        WHERE r.date = ?
          AND r.track = ?
          AND r.course = ?
          AND EXISTS (SELECT 1 FROM results x WHERE x.race_id = r.race_id)
        ORDER BY r.date, r.race_id
        """,
        (target["date"], target["track"], target["course"]),
    )
    prior_races = [
        dict(row)
        for row in candidate_rows
        if is_prior_race(str(row["race_id"]), str(target["race_id"]), target_sequence)
    ]
    if not prior_races:
        return empty_bias(race_id)

    source_race_ids = [str(row["race_id"]) for row in prior_races]
    runner_scores: list[dict[str, Any]] = []
    for prior in prior_races:
        runner_scores.extend(result_scores_for_race(conn, str(prior["race_id"])))
    if not runner_scores:
        return empty_bias(race_id)

    all_scores = [float(row["score"]) for row in runner_scores]
    base = mean(all_scores)
    inside_scores = [float(row["score"]) for row in runner_scores if row["draw_bucket"] == "inside"]
    outside_scores = [float(row["score"]) for row in runner_scores if row["draw_bucket"] == "outside"]
    front_scores = [float(row["score"]) for row in runner_scores if row["pace_bucket"] == "front"]
    closer_scores = [float(row["score"]) for row in runner_scores if row["pace_bucket"] == "closer"]
    favorite_scores = [float(row["score"]) for row in runner_scores if row["market_bucket"] == "favorite"]
    longshot_scores = [float(row["score"]) for row in runner_scores if row["market_bucket"] == "longshot"]

    return TrackBiasSnapshot(
        race_id=race_id,
        source_race_ids=source_race_ids,
        inside_bias=clamp(mean_or_base(inside_scores, base) - base),
        outside_bias=clamp(mean_or_base(outside_scores, base) - base),
        front_bias=clamp(mean_or_base(front_scores, base) - base),
        closer_bias=clamp(mean_or_base(closer_scores, base) - base),
        favorite_underperformance=clamp(base - mean_or_base(favorite_scores, base)),
        longshot_uplift=clamp(mean_or_base(longshot_scores, base) - base),
    )


def result_scores_for_race(conn: sqlite3.Connection, race_id: str) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT
          ru.horse_id,
          ru.draw,
          ru.running_style,
          re.finish_position,
          latest.win_odds
        FROM runners ru
        JOIN results re ON re.race_id = ru.race_id AND re.horse_id = ru.horse_id
        LEFT JOIN (
          SELECT o.race_id, o.horse_id, o.win_odds
          FROM odds_ticks o
          JOIN (
            SELECT race_id, horse_id, max(timestamp) AS max_ts
            FROM odds_ticks
            WHERE race_id = ? AND source != 'hkjc_results_final'
            GROUP BY race_id, horse_id
          ) x
            ON x.race_id = o.race_id
           AND x.horse_id = o.horse_id
           AND x.max_ts = o.timestamp
          WHERE o.race_id = ? AND o.source != 'hkjc_results_final'
        ) latest ON latest.race_id = ru.race_id AND latest.horse_id = ru.horse_id
        WHERE ru.race_id = ?
        ORDER BY ru.draw
        """,
        (race_id, race_id, race_id),
    )
    field_size = max(len(rows), 1)
    latest_odds = [float(row["win_odds"]) for row in rows if row["win_odds"]]
    favorite_odds = min(latest_odds) if latest_odds else None
    longshot_threshold = sorted(latest_odds)[max(int(len(latest_odds) * 0.66) - 1, 0)] if latest_odds else None
    output = []
    for row in rows:
        finish_position = max(int(row["finish_position"] or field_size), 1)
        score = (field_size + 1 - finish_position) / field_size
        win_odds = float(row["win_odds"]) if row["win_odds"] else None
        output.append(
            {
                "horse_id": row["horse_id"],
                "score": score,
                "draw_bucket": draw_bucket(int(row["draw"] or 0), field_size),
                "pace_bucket": pace_bucket(str(row["running_style"] or "")),
                "market_bucket": market_bucket(win_odds, favorite_odds, longshot_threshold),
            }
        )
    return output


def track_bias_features(
    snapshot: TrackBiasSnapshot,
    draw: int,
    field_size: int,
    running_style: str,
) -> dict[str, float]:
    draw_group = draw_bucket(draw, field_size)
    pace_group = pace_bucket(running_style)
    return {
        "same_day_inside_bias": snapshot.inside_bias if draw_group == "inside" else 0.0,
        "same_day_outside_bias": snapshot.outside_bias if draw_group == "outside" else 0.0,
        "same_day_pace_bias": pace_bias_for_group(snapshot, pace_group),
    }


def pace_bias_for_group(snapshot: TrackBiasSnapshot, pace_group: str) -> float:
    if pace_group == "front":
        return snapshot.front_bias
    if pace_group == "closer":
        return snapshot.closer_bias
    return 0.0


def draw_bucket(draw: int, field_size: int) -> str:
    if draw <= max(1, field_size // 3):
        return "inside"
    if draw > max(1, field_size * 2 // 3):
        return "outside"
    return "middle"


def pace_bucket(running_style: str) -> str:
    style = running_style.lower().strip()
    if style in PACE_FRONT:
        return "front"
    if style in PACE_CLOSER:
        return "closer"
    return "middle"


def market_bucket(
    win_odds: float | None,
    favorite_odds: float | None,
    longshot_threshold: float | None,
) -> str:
    if win_odds is None:
        return "unknown"
    if favorite_odds is not None and win_odds == favorite_odds:
        return "favorite"
    if longshot_threshold is not None and win_odds >= longshot_threshold:
        return "longshot"
    return "middle"


def is_prior_race(candidate_id: str, target_id: str, target_sequence: int | None) -> bool:
    if candidate_id == target_id:
        return False
    candidate_sequence = race_sequence(candidate_id)
    if target_sequence is not None and candidate_sequence is not None:
        return candidate_sequence < target_sequence
    return candidate_id < target_id


def race_sequence(race_id: str) -> int | None:
    matches = re.findall(r"(\d+)", race_id)
    return int(matches[-1]) if matches else None


def empty_bias(race_id: str) -> TrackBiasSnapshot:
    return TrackBiasSnapshot(
        race_id=race_id,
        source_race_ids=[],
        inside_bias=0.0,
        outside_bias=0.0,
        front_bias=0.0,
        closer_bias=0.0,
        favorite_underperformance=0.0,
        longshot_uplift=0.0,
    )


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def mean_or_base(values: list[float], base: float) -> float:
    return mean(values) if values else base


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
