from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from .storage import fetch_all, latest_odds_by_race


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
]


@dataclass(frozen=True)
class RunnerFeatures:
    race_id: str
    horse_id: str
    horse_no: int | None
    horse_name: str
    horse_name_zh: str
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

    output: list[RunnerFeatures] = []
    for runner in runners:
        horse_id = runner["horse_id"]
        latest = odds.get(horse_id)
        latest_win_odds = float(latest["win_odds"]) if latest else None
        latest_win_odds_source = str(latest["source"]) if latest and "source" in latest.keys() and latest["source"] else None
        latest_place_odds = float(latest["place_odds"]) if latest and latest["place_odds"] else None
        latest_place_odds_source = str(latest["place_source"]) if latest and "place_source" in latest.keys() and latest["place_source"] else None
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
        }
        result = results.get(horse_id)
        output.append(
            RunnerFeatures(
                race_id=race_id,
                horse_id=horse_id,
                horse_no=int(runner["horse_no"]) if "horse_no" in runner.keys() and runner["horse_no"] else None,
                horse_name=runner["horse_name"],
                horse_name_zh=runner["horse_name_zh"] if "horse_name_zh" in runner.keys() else "",
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
