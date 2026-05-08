from __future__ import annotations

import math
import re
import sqlite3
from typing import Any

from .storage import fetch_all


DEFAULT_SIGNALS = {
    "body_weight_change": 0.0,
    "body_weight_trend": 0.0,
    "gear_change_signal": 0.0,
    "health_signal": 0.0,
    "trip_luck_score": 0.0,
    "ability_issue_score": 0.0,
    "closing_gain_score": 0.0,
    "pace_fade_score": 0.0,
    "distance_stretch_signal": 0.0,
    "opponent_strength_score": 0.0,
}

LUCK_KEYWORDS = {
    "blocked",
    "checked",
    "crowded",
    "wide",
    "held up",
    "no clear",
    "hampered",
    "bumped",
    "slowly away",
    "badly away",
    "traffic",
    "unbalanced",
    "被困",
    "受阻",
    "碰撞",
    "外疊",
    "慢閘",
}

ABILITY_ISSUE_KEYWORDS = {
    "weakened",
    "faded",
    "tired",
    "no extra",
    "one paced",
    "failed to quicken",
    "could not quicken",
    "gave ground",
    "力弱",
    "轉弱",
    "未能加速",
    "乏力",
}

CLOSING_KEYWORDS = {
    "ran on",
    "stayed on",
    "finished strongly",
    "closed",
    "made ground",
    "late",
    "追上",
    "後上",
    "衝刺",
}


def horse_context_signals(
    conn: sqlite3.Connection,
    horse_id: str,
    before_date: str,
    target_distance_m: int,
    current_body_weight_lbs: float | None = None,
    current_gear: str = "",
    limit: int = 6,
) -> dict[str, float]:
    rows = past_performances(conn, horse_id, before_date, limit=limit)
    if not rows:
        return dict(DEFAULT_SIGNALS)

    analyses = [analyse_performance(row, target_distance_m) for row in rows]
    weights = recency_weights(len(analyses))
    weighted = lambda key: weighted_average([float(row[key]) for row in analyses], weights)
    previous_body_weights = [
        float(row["body_weight_lbs"])
        for row in rows
        if row_has_value(row, "body_weight_lbs") and float(row["body_weight_lbs"] or 0.0) > 0
    ]
    body_change = 0.0
    if current_body_weight_lbs and previous_body_weights:
        body_change = float(current_body_weight_lbs) - previous_body_weights[0]
    body_trend = body_weight_trend(previous_body_weights)
    health = health_signal(rows, previous_body_weights, body_change)
    previous_gear = str(rows[0]["gear"] or "") if row_has_value(rows[0], "gear") else ""
    gear_change = gear_change_signal(current_gear, previous_gear)
    opponent_strength = opponent_strength_score(rows, weights)
    return {
        "body_weight_change": round(body_change / 20.0, 4),
        "body_weight_trend": round(body_trend / 15.0, 4),
        "gear_change_signal": gear_change,
        "health_signal": round(health, 4),
        "trip_luck_score": round(weighted("trip_luck_score"), 4),
        "ability_issue_score": round(weighted("ability_issue_score"), 4),
        "closing_gain_score": round(weighted("closing_gain_score"), 4),
        "pace_fade_score": round(weighted("pace_fade_score"), 4),
        "distance_stretch_signal": round(weighted("distance_stretch_signal"), 4),
        "opponent_strength_score": round(opponent_strength, 4),
    }


def past_performances(
    conn: sqlite3.Connection,
    horse_id: str,
    before_date: str,
    limit: int = 6,
) -> list[sqlite3.Row]:
    return fetch_all(
        conn,
        """
        SELECT
          r.race_id,
          r.date,
          r.distance_m,
          r.class_rating,
          r.prize,
          x.finish_position,
          x.finish_time_sec,
          x.margin_lengths,
          x.sectional_400_sec,
          x.sectional_800_sec,
          x.comment,
          x.running_positions,
          ru.running_style,
          ru.gear,
          ru.weight_lbs,
          ru.body_weight_lbs,
          (
            SELECT avg(COALESCE(ru2.official_rating, 0))
            FROM runners ru2
            WHERE ru2.race_id = r.race_id
          ) AS avg_official_rating
        FROM results x
        JOIN races r ON r.race_id = x.race_id
        LEFT JOIN runners ru ON ru.race_id = x.race_id AND ru.horse_id = x.horse_id
        WHERE x.horse_id = ? AND r.date < ?
        ORDER BY r.date DESC
        LIMIT ?
        """,
        (horse_id, before_date, limit),
    )


def analyse_performance(row: sqlite3.Row, target_distance_m: int) -> dict[str, float]:
    positions = parse_running_positions(row["running_positions"] if row_has_value(row, "running_positions") else "")
    finish = int(row["finish_position"] or 99)
    margin = float(row["margin_lengths"] or 0.0)
    distance = int(row["distance_m"] or 0)
    comment = normalize_comment(str(row["comment"] or ""))
    early = positions[0] if positions else None
    late = positions[-1] if positions else finish
    closing_gain = closing_gain_score(early, late, finish)
    pace_fade = pace_fade_score(early, late, finish)
    luck = trip_luck_score(comment, positions, finish, margin, closing_gain)
    ability = ability_issue_score(comment, positions, finish, margin, pace_fade)
    return {
        "trip_luck_score": luck,
        "ability_issue_score": ability,
        "closing_gain_score": closing_gain,
        "pace_fade_score": pace_fade,
        "distance_stretch_signal": distance_stretch_signal(distance, target_distance_m, closing_gain, pace_fade),
    }


def parse_running_positions(value: object) -> list[int]:
    return [int(token) for token in re.findall(r"\d+", str(value or ""))]


def running_positions_text(positions: list[int]) -> str:
    return "/".join(str(position) for position in positions)


def infer_running_style_from_positions(positions: list[int]) -> str:
    if not positions:
        return "unknown"
    early = positions[0]
    if early <= 2:
        return "leader"
    if early <= 5:
        return "pace"
    if early >= 9:
        return "closer"
    return "stalker"


def closing_gain_score(early: int | None, late: int, finish: int) -> float:
    if early is None:
        return 0.0
    gain_to_finish = max(0, early - finish)
    gain_late = max(0, late - finish)
    return min(1.0, (gain_to_finish * 0.18) + (gain_late * 0.12))


def pace_fade_score(early: int | None, late: int, finish: int) -> float:
    if early is None:
        return 0.0
    if early > 3:
        return 0.0
    fade = max(0, finish - early)
    late_fade = max(0, finish - late)
    return min(1.0, (fade * 0.16) + (late_fade * 0.10))


def trip_luck_score(
    comment: str,
    positions: list[int],
    finish: int,
    margin: float,
    closing_gain: float,
) -> float:
    score = 0.0
    if contains_keyword(comment, LUCK_KEYWORDS):
        score += 0.55
    if closing_gain >= 0.35 and finish > 1:
        score += 0.25
    if finish <= 5 and margin <= 2.5 and closing_gain >= 0.20:
        score += 0.15
    if positions and positions[0] >= 8 and finish <= 5:
        score += 0.10
    return min(1.0, score)


def ability_issue_score(
    comment: str,
    positions: list[int],
    finish: int,
    margin: float,
    pace_fade: float,
) -> float:
    score = 0.0
    if contains_keyword(comment, ABILITY_ISSUE_KEYWORDS):
        score += 0.45
    if margin >= 5.0:
        score += min(0.35, margin / 25.0)
    if pace_fade >= 0.35:
        score += 0.25
    if finish >= 8:
        score += 0.12
    if contains_keyword(comment, CLOSING_KEYWORDS):
        score -= 0.18
    return max(0.0, min(1.0, score))


def distance_stretch_signal(
    past_distance_m: int,
    target_distance_m: int,
    closing_gain: float,
    pace_fade: float,
) -> float:
    if past_distance_m <= 0 or target_distance_m <= 0:
        return 0.0
    distance_delta = target_distance_m - past_distance_m
    if distance_delta >= 200 and closing_gain > pace_fade:
        return min(1.0, (distance_delta / 600.0) * closing_gain)
    if distance_delta <= -200 and pace_fade > closing_gain:
        return min(1.0, (abs(distance_delta) / 600.0) * pace_fade)
    if distance_delta >= 200 and pace_fade > 0.35:
        return -min(1.0, (distance_delta / 600.0) * pace_fade)
    return 0.0


def body_weight_trend(weights: list[float]) -> float:
    if len(weights) < 2:
        return 0.0
    recent_first = list(reversed(weights[:4]))
    if len(recent_first) < 2:
        return 0.0
    x_values = list(range(len(recent_first)))
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(recent_first) / len(recent_first)
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    if denominator <= 0:
        return 0.0
    return sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, recent_first)) / denominator


def health_signal(rows: list[sqlite3.Row], body_weights: list[float], current_change: float) -> float:
    signal = 0.0
    if abs(current_change) >= 30:
        signal -= 0.35
    elif 0 < abs(current_change) <= 15:
        signal += 0.10
    if len(body_weights) >= 3:
        mean = sum(body_weights[:4]) / min(len(body_weights), 4)
        variance = sum((weight - mean) ** 2 for weight in body_weights[:4]) / min(len(body_weights), 4)
        if math.sqrt(variance) <= 12:
            signal += 0.12
        elif math.sqrt(variance) >= 28:
            signal -= 0.20
    latest = rows[0]
    if row_has_value(latest, "margin_lengths") and float(latest["margin_lengths"] or 0.0) >= 8:
        signal -= 0.10
    return max(-1.0, min(1.0, signal))


def gear_change_signal(current_gear: str, previous_gear: str) -> float:
    current = normalize_gear(current_gear)
    previous = normalize_gear(previous_gear)
    if not current and not previous:
        return 0.0
    if current == previous:
        return 0.0
    if current and not previous:
        return 0.25
    if previous and not current:
        return -0.10
    return 0.12


def opponent_strength_score(rows: list[sqlite3.Row], weights: list[float]) -> float:
    scores = []
    for row in rows:
        avg_rating = float(row["avg_official_rating"] or 0.0)
        class_bonus = class_strength(str(row["class_rating"] or ""))
        prize_bonus = math.log10(max(float(row["prize"] or 0.0), 1.0)) / 10.0
        scores.append((avg_rating / 100.0) + class_bonus + prize_bonus)
    return weighted_average(scores, weights)


def class_strength(class_rating: str) -> float:
    text = class_rating.lower()
    if "group" in text or "g1" in text:
        return 0.30
    for token in re.findall(r"\d+", text):
        value = int(token)
        return {1: 0.20, 2: 0.14, 3: 0.08, 4: 0.03, 5: -0.03}.get(value, 0.0)
    return 0.0


def recency_weights(count: int) -> list[float]:
    return [0.72**index for index in range(count)]


def weighted_average(values: list[float], weights: list[float]) -> float:
    if not values:
        return 0.0
    usable = list(zip(values, weights))
    total = sum(weight for _, weight in usable)
    if total <= 0:
        return 0.0
    return sum(value * weight for value, weight in usable) / total


def normalize_comment(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def normalize_gear(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").upper().replace("-", ""))


def contains_keyword(comment: str, keywords: set[str]) -> bool:
    return any(keyword in comment for keyword in keywords)


def row_has_value(row: sqlite3.Row, key: str) -> bool:
    return key in row.keys() and row[key] not in {None, ""}
