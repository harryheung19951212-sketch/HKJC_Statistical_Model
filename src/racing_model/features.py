from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .storage import fetch_all, latest_odds_by_race
from .track_bias import same_day_track_bias, track_bias_features


FEATURE_NAMES = [
    "official_rating",
    "weight_lbs",
    "draw_inside",
    "draw_outside",
    "age",
    "recent_speed",
    "recent_form",
    "distance_fit",
    "going_fit",
    "jockey_win_rate",
    "trainer_win_rate",
    "workout_score",
    "pace_pressure",
    "market_implied",
    "odds_delta_5m",
    "odds_delta_2m",
    "odds_delta_30s",
    "late_steam",
    "late_drift",
    "same_day_inside_bias",
    "same_day_outside_bias",
    "same_day_pace_bias",
]

LATE_MARKET_FLOW_FEATURES = [
    "odds_delta_5m",
    "odds_delta_2m",
    "odds_delta_30s",
    "late_steam",
    "late_drift",
]

SAME_DAY_TRACK_BIAS_FEATURES = [
    "same_day_inside_bias",
    "same_day_outside_bias",
    "same_day_pace_bias",
]


@dataclass(frozen=True)
class RunnerFeatures:
    race_id: str
    horse_id: str
    horse_no: int | None
    horse_name: str
    last_six_runs: str
    horse_name_zh: str
    running_style: str
    jockey: str
    jockey_zh: str
    trainer: str
    trainer_zh: str
    draw: int
    features: dict[str, float]
    latest_win_odds: float | None
    latest_win_odds_source: str | None
    latest_place_odds: float | None
    latest_place_odds_source: str | None
    finish_position: int | None


def build_race_features(conn: sqlite3.Connection, race_id: str) -> list[RunnerFeatures]:
    race = fetch_all(conn, "SELECT * FROM races WHERE race_id = ?", (race_id,))
    if not race:
        raise ValueError(f"Race not found: {race_id}")
    race_row = race[0]

    runners = fetch_all(conn, "SELECT * FROM runners WHERE race_id = ? ORDER BY draw", (race_id,))
    odds = latest_odds_by_race(conn, race_id)
    results = {
        row["horse_id"]: row
        for row in fetch_all(conn, "SELECT * FROM results WHERE race_id = ?", (race_id,))
    }
    all_runner_count = max(len(runners), 1)
    late_flow = late_market_flow(conn, race_id)
    track_bias = same_day_track_bias(conn, race_id)

    output: list[RunnerFeatures] = []
    for runner in runners:
        horse_id = runner["horse_id"]
        latest = odds.get(horse_id)
        latest_win_odds = float(latest["win_odds"]) if latest else None
        latest_win_odds_source = str(latest["source"]) if latest and "source" in latest.keys() and latest["source"] else None
        latest_place_odds = float(latest["place_odds"]) if latest and latest["place_odds"] else None
        latest_place_odds_source = str(latest["place_source"]) if latest and "place_source" in latest.keys() and latest["place_source"] else None
        bias_features = track_bias_features(
            track_bias,
            int(runner["draw"]),
            all_runner_count,
            str(runner["running_style"] or ""),
        )
        feature_values = {
            "official_rating": float(runner["official_rating"] or 0),
            "weight_lbs": float(runner["weight_lbs"] or 0),
            "draw_inside": 1.0 if int(runner["draw"]) <= max(1, all_runner_count // 3) else 0.0,
            "draw_outside": 1.0 if int(runner["draw"]) > max(1, all_runner_count * 2 // 3) else 0.0,
            "age": float(runner["age"] or 0),
            "recent_speed": recent_speed(conn, horse_id, race_row["date"]),
            "recent_form": recent_form(conn, horse_id, race_row["date"]),
            "distance_fit": distance_fit(conn, horse_id, int(race_row["distance_m"]), race_row["date"]),
            "going_fit": going_fit(conn, horse_id, race_row["going"], race_row["date"]),
            "jockey_win_rate": participant_win_rate(conn, "jockey", runner["jockey"], race_row["date"]),
            "trainer_win_rate": participant_win_rate(conn, "trainer", runner["trainer"], race_row["date"]),
            "workout_score": workout_score(conn, horse_id, race_row["date"]),
            "pace_pressure": pace_pressure(runner["running_style"], runners),
            "market_implied": implied_probability(latest_win_odds),
            "odds_delta_5m": late_flow.get(horse_id, {}).get("odds_delta_5m", 0.0),
            "odds_delta_2m": late_flow.get(horse_id, {}).get("odds_delta_2m", 0.0),
            "odds_delta_30s": late_flow.get(horse_id, {}).get("odds_delta_30s", 0.0),
            "late_steam": late_flow.get(horse_id, {}).get("late_steam", 0.0),
            "late_drift": late_flow.get(horse_id, {}).get("late_drift", 0.0),
            "same_day_inside_bias": bias_features["same_day_inside_bias"],
            "same_day_outside_bias": bias_features["same_day_outside_bias"],
            "same_day_pace_bias": bias_features["same_day_pace_bias"],
        }
        result = results.get(horse_id)
        output.append(
            RunnerFeatures(
                race_id=race_id,
                horse_id=horse_id,
                horse_no=int(runner["horse_no"]) if "horse_no" in runner.keys() and runner["horse_no"] else None,
                horse_name=runner["horse_name"],
                last_six_runs=str(runner["last_six_runs"] or "") if "last_six_runs" in runner.keys() else "",
                horse_name_zh=runner["horse_name_zh"] if "horse_name_zh" in runner.keys() else "",
                running_style=str(runner["running_style"] or ""),
                jockey=runner["jockey"],
                jockey_zh=runner["jockey_zh"] if "jockey_zh" in runner.keys() else "",
                trainer=runner["trainer"],
                trainer_zh=runner["trainer_zh"] if "trainer_zh" in runner.keys() else "",
                draw=int(runner["draw"]),
                features=feature_values,
                latest_win_odds=latest_win_odds,
                latest_win_odds_source=latest_win_odds_source,
                latest_place_odds=latest_place_odds,
                latest_place_odds_source=latest_place_odds_source,
                finish_position=int(result["finish_position"]) if result else None,
            )
        )
    return output


def build_training_races(conn: sqlite3.Connection) -> list[list[RunnerFeatures]]:
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
    return [build_race_features(conn, race_id) for race_id in race_ids]


def recent_speed(conn: sqlite3.Connection, horse_id: str, before_date: str) -> float:
    rows = fetch_all(
        conn,
        """
        SELECT r.distance_m, x.finish_time_sec, x.margin_lengths
        FROM results x
        JOIN races r ON r.race_id = x.race_id
        WHERE x.horse_id = ? AND r.date < ?
        ORDER BY r.date DESC
        LIMIT 5
        """,
        (horse_id, before_date),
    )
    if not rows:
        return 0.0
    scores = []
    for row in rows:
        metres_per_second = float(row["distance_m"]) / max(float(row["finish_time_sec"]), 1.0)
        penalty = float(row["margin_lengths"] or 0) * 0.03
        scores.append(metres_per_second - penalty)
    return sum(scores) / len(scores)


def recent_form(conn: sqlite3.Connection, horse_id: str, before_date: str) -> float:
    rows = fetch_all(
        conn,
        """
        SELECT x.finish_position
        FROM results x
        JOIN races r ON r.race_id = x.race_id
        WHERE x.horse_id = ? AND r.date < ?
        ORDER BY r.date DESC
        LIMIT 5
        """,
        (horse_id, before_date),
    )
    if not rows:
        return 0.0
    return sum(1.0 / max(int(row["finish_position"]), 1) for row in rows) / len(rows)


def distance_fit(conn: sqlite3.Connection, horse_id: str, distance_m: int, before_date: str) -> float:
    rows = fetch_all(
        conn,
        """
        SELECT r.distance_m, x.finish_position
        FROM results x
        JOIN races r ON r.race_id = x.race_id
        WHERE x.horse_id = ? AND r.date < ?
        """,
        (horse_id, before_date),
    )
    if not rows:
        return 0.0
    weighted = []
    for row in rows:
        distance_delta = abs(int(row["distance_m"]) - distance_m)
        similarity = math.exp(-distance_delta / 600)
        weighted.append(similarity / max(int(row["finish_position"]), 1))
    return sum(weighted) / len(weighted)


def going_fit(conn: sqlite3.Connection, horse_id: str, going: str, before_date: str) -> float:
    target = normalize_going(going)
    rows = fetch_all(
        conn,
        """
        SELECT r.going, x.finish_position
        FROM results x
        JOIN races r ON r.race_id = x.race_id
        WHERE x.horse_id = ? AND r.date < ?
        """,
        (horse_id, before_date),
    )
    matching = [row for row in rows if normalize_going(row["going"]) == target]
    if not matching:
        return 0.0
    return sum(1.0 / max(int(row["finish_position"]), 1) for row in matching) / len(matching)


def participant_win_rate(conn: sqlite3.Connection, field: str, name: str, before_date: str) -> float:
    if field not in {"jockey", "trainer"}:
        raise ValueError("field must be jockey or trainer")
    rows = fetch_all(
        conn,
        f"""
        SELECT x.finish_position
        FROM runners ru
        JOIN races r ON r.race_id = ru.race_id
        JOIN results x ON x.race_id = ru.race_id AND x.horse_id = ru.horse_id
        WHERE ru.{field} = ? AND r.date < ?
        ORDER BY r.date DESC
        LIMIT 100
        """,
        (name, before_date),
    )
    if not rows:
        return 0.0
    wins = sum(1 for row in rows if int(row["finish_position"]) == 1)
    return wins / len(rows)


def workout_score(conn: sqlite3.Connection, horse_id: str, before_date: str) -> float:
    rows = fetch_all(
        conn,
        """
        SELECT *
        FROM workouts
        WHERE horse_id = ? AND date < ?
        ORDER BY date DESC
        LIMIT 5
        """,
        (horse_id, before_date),
    )
    if not rows:
        return 0.0
    score = 0.0
    for row in rows:
        speed = float(row["distance_m"]) / max(float(row["time_sec"]), 1.0)
        rank_bonus = 0.15 / max(int(row["rank"] or 10), 1)
        type_bonus = 0.08 if "trial" in str(row["work_type"]).lower() else 0.0
        score += speed + rank_bonus + type_bonus
    return score / len(rows)


def pace_pressure(style: str, runners: list[sqlite3.Row]) -> float:
    style = (style or "").lower()
    leaders = sum(1 for runner in runners if (runner["running_style"] or "").lower() in {"leader", "pace"})
    if style in {"leader", "pace"}:
        return -0.15 * max(leaders - 1, 0)
    if style == "closer":
        return 0.08 * max(leaders - 1, 0)
    return 0.0


def implied_probability(odds: float | None) -> float:
    if not odds or odds <= 1:
        return 0.0
    return 1.0 / odds


def late_market_flow(conn: sqlite3.Connection, race_id: str) -> dict[str, dict[str, float]]:
    rows = fetch_all(
        conn,
        """
        SELECT horse_id, timestamp, win_odds, source
        FROM odds_ticks
        WHERE race_id = ?
          AND source != 'hkjc_results_final'
          AND win_odds > 1
        ORDER BY timestamp
        """,
        (race_id,),
    )
    parsed_rows = []
    for row in rows:
        timestamp = parse_timestamp(row["timestamp"])
        if timestamp is None:
            continue
        parsed_rows.append(
            {
                "horse_id": str(row["horse_id"]),
                "timestamp": timestamp,
                "win_odds": float(row["win_odds"]),
            }
        )
    if not parsed_rows:
        return {}
    reference_time = max(row["timestamp"] for row in parsed_rows)
    by_horse: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in parsed_rows:
        if row["timestamp"] <= reference_time:
            by_horse[str(row["horse_id"])].append(row)

    output: dict[str, dict[str, float]] = {}
    for horse_id, horse_rows in by_horse.items():
        horse_rows.sort(key=lambda row: row["timestamp"])
        latest = latest_row_at_or_before(horse_rows, reference_time)
        if latest is None:
            continue
        deltas = {
            "odds_delta_5m": odds_delta_for_window(horse_rows, latest, 300.0),
            "odds_delta_2m": odds_delta_for_window(horse_rows, latest, 120.0),
            "odds_delta_30s": odds_delta_for_window(horse_rows, latest, 30.0),
        }
        positive = [max(value, 0.0) for value in deltas.values()]
        negative = [min(value, 0.0) for value in deltas.values()]
        output[horse_id] = {
            **deltas,
            "late_steam": sum(positive) / len(positive),
            "late_drift": sum(negative) / len(negative),
        }
    return output


def odds_delta_for_window(
    rows: list[dict[str, Any]],
    latest: dict[str, Any],
    seconds: float,
) -> float:
    cutoff = float(latest["timestamp"]) - seconds
    baseline = first_row_at_or_after(rows, cutoff)
    if baseline is None or baseline is latest:
        return 0.0
    baseline_odds = float(baseline["win_odds"])
    latest_odds = float(latest["win_odds"])
    if baseline_odds <= 1 or latest_odds <= 1:
        return 0.0
    return math.log(baseline_odds / latest_odds)


def latest_row_at_or_before(rows: list[dict[str, Any]], timestamp: float) -> dict[str, Any] | None:
    candidate = None
    for row in rows:
        if float(row["timestamp"]) <= timestamp:
            candidate = row
        else:
            break
    return candidate


def first_row_at_or_after(rows: list[dict[str, Any]], timestamp: float) -> dict[str, Any] | None:
    for row in rows:
        if float(row["timestamp"]) >= timestamp:
            return row
    return None


def parse_timestamp(value: object) -> float | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def normalize_going(going: str) -> str:
    text = (going or "").lower()
    if "firm" in text:
        return "firm"
    if "soft" in text or "yield" in text:
        return "soft"
    if "good" in text:
        return "good"
    if "wet" in text or "mud" in text:
        return "wet"
    return text.strip()


def feature_matrix(runners: list[RunnerFeatures]) -> list[list[float]]:
    return [[runner.features.get(name, 0.0) for name in FEATURE_NAMES] for runner in runners]


def feature_summary_by_name(runners: list[RunnerFeatures]) -> dict[str, dict[str, float]]:
    values: dict[str, list[float]] = defaultdict(list)
    for runner in runners:
        for name, value in runner.features.items():
            values[name].append(value)
    return {
        name: {"min": min(items), "max": max(items), "mean": sum(items) / len(items)}
        for name, items in values.items()
        if items
    }
