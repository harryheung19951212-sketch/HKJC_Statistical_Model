from __future__ import annotations

import math
import re
import sqlite3
from typing import Any

from .storage import fetch_all
from .trip_diagnostics import (
    ability_issue_score,
    closing_gain_score,
    parse_running_positions,
    pace_fade_score,
    recency_weights,
    trip_luck_score,
    weighted_average,
)


DEFAULT_PACE_PROFILE = {
    "early_speed_profile": 0.0,
    "midrace_move_score": 0.0,
    "turn_position_score": 0.0,
    "late_gain_profile": 0.0,
    "front_fade_risk": 0.0,
    "distance_pace_fit": 0.0,
    "hidden_ability_signal": 0.0,
    "class_change_signal": 0.0,
    "rating_change_signal": 0.0,
    "traffic_history_risk": 0.0,
}

DEFAULT_RACE_CONTEXT = {
    "projected_position_score": 0.0,
    "traffic_risk_score": 0.0,
    "pace_advantage_score": 0.0,
    "pace_shape_pressure": 0.0,
}

FRONT_STYLES = {"leader", "front", "front_runner", "pace", "on_pace"}
STALKER_STYLES = {"prominent", "stalker", "handy", "pace_presser"}
MIDFIELD_STYLES = {"midfield", "settle_midfield", "average"}
CLOSER_STYLES = {"closer", "backmarker", "hold_up", "hold-up", "rear", "deep_closer"}


def horse_pace_profile(
    conn: sqlite3.Connection,
    horse_id: str,
    before_date: str,
    target_distance_m: int,
    target_class_rating: str = "",
    current_official_rating: float | None = None,
    limit: int = 6,
) -> dict[str, float]:
    rows = past_pace_rows(conn, horse_id, before_date, limit=limit)
    if not rows:
        return dict(DEFAULT_PACE_PROFILE)

    analyses = [analyse_pace_row(row, target_distance_m) for row in rows]
    weights = recency_weights(len(analyses))
    profile = {
        key: round(weighted_average([float(row[key]) for row in analyses], weights), 4)
        for key in [
            "early_speed_profile",
            "midrace_move_score",
            "turn_position_score",
            "late_gain_profile",
            "front_fade_risk",
            "distance_pace_fit",
            "hidden_ability_signal",
            "traffic_history_risk",
        ]
    }
    profile["class_change_signal"] = round(class_change_signal(target_class_rating, rows[0]), 4)
    profile["rating_change_signal"] = round(rating_change_signal(current_official_rating, rows[0]), 4)
    return profile


def past_pace_rows(
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
          x.margin_lengths,
          x.comment,
          x.running_positions,
          ru.running_style,
          ru.official_rating,
          (
            SELECT count(*)
            FROM runners ru_count
            WHERE ru_count.race_id = r.race_id
          ) AS runner_count,
          (
            SELECT avg(COALESCE(ru_rating.official_rating, 0))
            FROM runners ru_rating
            WHERE ru_rating.race_id = r.race_id
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


def analyse_pace_row(row: sqlite3.Row, target_distance_m: int) -> dict[str, float]:
    runner_count = max(int(row["runner_count"] or 12), 1)
    finish = max(int(row["finish_position"] or runner_count), 1)
    positions = parse_running_positions(row["running_positions"] if "running_positions" in row.keys() else "")
    early = positions[0] if positions else inferred_early_position(str(row["running_style"] or ""), finish, runner_count)
    mid = positions[len(positions) // 2] if positions else finish
    turn = positions[-2] if len(positions) >= 2 else mid
    comment = normalize_text(str(row["comment"] or ""))
    margin = float(row["margin_lengths"] or 0.0)
    closing_gain = closing_gain_score(early, turn, finish)
    fade = pace_fade_score(early, turn, finish)
    luck = trip_luck_score(comment, positions, finish, margin, closing_gain)
    ability = ability_issue_score(comment, positions, finish, margin, fade)
    return {
        "early_speed_profile": position_score(early, runner_count),
        "midrace_move_score": signed_position_move(early, mid, runner_count),
        "turn_position_score": position_score(turn, runner_count),
        "late_gain_profile": closing_gain,
        "front_fade_risk": fade,
        "distance_pace_fit": distance_pace_fit(
            int(row["distance_m"] or 0),
            target_distance_m,
            position_score(early, runner_count),
            closing_gain,
            fade,
        ),
        "hidden_ability_signal": hidden_ability_signal(row, finish, margin, luck, ability, closing_gain, fade),
        "traffic_history_risk": traffic_history_risk(luck, positions, finish, runner_count),
    }


def build_race_pace_context(
    runners: list[sqlite3.Row],
    profiles: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    if not runners:
        return {}
    runner_count = len(runners)
    max_draw = max([safe_int(row["draw"]) or 0 for row in runners] + [runner_count, 1])
    prepared = []
    for index, runner in enumerate(runners):
        horse_id = str(runner["horse_id"])
        draw = safe_int(runner["draw"]) or index + 1
        draw_pct = (draw - 1) / max(max_draw - 1, 1)
        profile = profiles.get(horse_id, DEFAULT_PACE_PROFILE)
        style_score = running_style_score(str(runner["running_style"] or ""))
        history_speed = float(profile.get("early_speed_profile") or 0.0)
        early_score = clamp((0.56 * (history_speed or style_score)) + (0.24 * style_score) + (0.20 * (1.0 - draw_pct)))
        prepared.append(
            {
                "horse_id": horse_id,
                "draw_pct": draw_pct,
                "early_score": early_score,
                "profile": profile,
            }
        )

    ordered = sorted(prepared, key=lambda row: (float(row["early_score"]), -float(row["draw_pct"])), reverse=True)
    speed_count = sum(1 for row in prepared if float(row["early_score"]) >= 0.68)
    leader_count = sum(1 for row in prepared if float(row["early_score"]) >= 0.78)
    pressure = clamp((leader_count / max(runner_count * 0.22, 1.0) * 0.45) + (speed_count / max(runner_count * 0.35, 1.0) * 0.55))
    shape = "fast" if pressure >= 0.68 or leader_count >= 3 else "slow" if pressure <= 0.34 and speed_count <= 1 else "moderate"

    output: dict[str, dict[str, float]] = {}
    for projected_position, row in enumerate(ordered, start=1):
        profile = row["profile"]
        projected_score = position_score(projected_position, runner_count)
        traffic = projected_traffic_risk(row, projected_position, runner_count, shape)
        advantage = projected_pace_advantage(row, projected_position, runner_count, shape, traffic)
        output[str(row["horse_id"])] = {
            "projected_position_score": round(projected_score, 4),
            "traffic_risk_score": round(traffic, 4),
            "pace_advantage_score": round(advantage, 4),
            "pace_shape_pressure": round(pressure, 4),
            "distance_pace_fit": round(float(profile.get("distance_pace_fit") or 0.0), 4),
        }
    return output


def projected_traffic_risk(row: dict[str, Any], projected_position: int, runner_count: int, shape: str) -> float:
    profile = row["profile"]
    draw_pct = float(row["draw_pct"])
    risk = 0.08 + float(profile.get("traffic_history_risk") or 0.0) * 0.45
    if projected_position > max(3, runner_count * 0.40):
        risk += 0.15
    if draw_pct <= 0.30 and projected_position > max(3, runner_count * 0.35):
        risk += 0.18
    if shape == "slow" and float(profile.get("late_gain_profile") or 0.0) > 0.25:
        risk += 0.12
    if runner_count >= 10 and projected_position > runner_count * 0.55:
        risk += 0.10
    return clamp(risk)


def projected_pace_advantage(
    row: dict[str, Any],
    projected_position: int,
    runner_count: int,
    shape: str,
    traffic: float,
) -> float:
    profile = row["profile"]
    early = float(profile.get("early_speed_profile") or 0.0)
    late = float(profile.get("late_gain_profile") or 0.0)
    fade = float(profile.get("front_fade_risk") or 0.0)
    distance_fit = float(profile.get("distance_pace_fit") or 0.0)
    front_rank = projected_position <= max(1, round(runner_count * 0.25))
    closer_rank = projected_position > max(2, round(runner_count * 0.55))
    advantage = 0.0
    if shape == "fast":
        advantage += late * 0.32
        if front_rank:
            advantage -= fade * 0.32
    elif shape == "slow":
        if front_rank:
            advantage += early * 0.18
        if closer_rank:
            advantage -= max(0.12, late * 0.18)
    else:
        advantage += float(profile.get("midrace_move_score") or 0.0) * 0.10
    advantage += distance_fit * 0.28
    advantage -= max(traffic - 0.42, 0.0) * 0.22
    return clamp(advantage, -0.5, 0.5)


def position_score(position: int | None, runner_count: int) -> float:
    if position is None or runner_count <= 1:
        return 0.0
    return clamp(1.0 - ((max(int(position), 1) - 1) / max(runner_count - 1, 1)))


def signed_position_move(start: int | None, end: int | None, runner_count: int) -> float:
    if start is None or end is None or runner_count <= 1:
        return 0.0
    return clamp((int(start) - int(end)) / max(runner_count - 1, 1), -1.0, 1.0)


def distance_pace_fit(
    past_distance_m: int,
    target_distance_m: int,
    early_speed: float,
    closing_gain: float,
    fade: float,
) -> float:
    if past_distance_m <= 0 or target_distance_m <= 0:
        return 0.0
    delta = target_distance_m - past_distance_m
    if abs(delta) < 150:
        return 0.0
    scale = min(abs(delta) / 600.0, 1.0)
    if delta > 0:
        return clamp((closing_gain - fade) * scale, -1.0, 1.0)
    return clamp((early_speed - closing_gain - (fade * 0.35)) * scale, -1.0, 1.0)


def hidden_ability_signal(
    row: sqlite3.Row,
    finish: int,
    margin: float,
    luck: float,
    ability: float,
    closing_gain: float,
    fade: float,
) -> float:
    signal = 0.0
    signal += luck * 0.30
    signal += closing_gain * 0.22
    if finish <= 5 and margin <= 2.5:
        signal += 0.16
    avg_rating = float(row["avg_official_rating"] or 0.0)
    rating = float(row["official_rating"] or 0.0)
    if avg_rating > 0 and rating > 0 and avg_rating - rating >= 4 and margin <= 3.0:
        signal += 0.12
    signal -= ability * 0.28
    signal -= fade * 0.12
    if margin >= 6:
        signal -= min(0.18, margin / 50.0)
    return clamp(signal, -1.0, 1.0)


def traffic_history_risk(luck: float, positions: list[int], finish: int, runner_count: int) -> float:
    risk = luck * 0.55
    if positions and positions[0] > runner_count * 0.55 and finish <= 5:
        risk += 0.12
    if len(positions) >= 3 and positions[-2] > runner_count * 0.55 and finish <= 5:
        risk += 0.10
    return clamp(risk)


def class_change_signal(target_class_rating: str, previous_row: sqlite3.Row) -> float:
    current = class_number(target_class_rating)
    previous = class_number(str(previous_row["class_rating"] or ""))
    if current is None or previous is None:
        return 0.0
    return clamp((previous - current) / 2.0, -1.0, 1.0)


def rating_change_signal(current_official_rating: float | None, previous_row: sqlite3.Row) -> float:
    if current_official_rating is None:
        return 0.0
    previous = float(previous_row["official_rating"] or 0.0)
    if previous <= 0:
        return 0.0
    return clamp((float(current_official_rating) - previous) / 10.0, -1.0, 1.0)


def class_number(value: str) -> int | None:
    text = str(value or "").lower()
    match = re.search(r"class\s*(\d+)|第\s*([一二三四五六七八九十\d]+)\s*班", text)
    if not match:
        return None
    number = match.group(1) or match.group(2)
    if not number:
        return None
    if number.isdigit():
        return int(number)
    return {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}.get(number)


def inferred_early_position(style: str, finish: int, runner_count: int) -> int:
    normalized = normalize_style(style)
    if normalized in FRONT_STYLES:
        return min(2, runner_count)
    if normalized in STALKER_STYLES:
        return max(2, round(runner_count * 0.32))
    if normalized in MIDFIELD_STYLES:
        return max(3, round(runner_count * 0.55))
    if normalized in CLOSER_STYLES:
        return max(4, round(runner_count * 0.78))
    return min(max(finish, 1), runner_count)


def running_style_score(style: str) -> float:
    normalized = normalize_style(style)
    if normalized in FRONT_STYLES:
        return 0.95
    if normalized in STALKER_STYLES:
        return 0.72
    if normalized in MIDFIELD_STYLES:
        return 0.48
    if normalized in CLOSER_STYLES:
        return 0.18
    return 0.40


def normalize_style(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def safe_int(value: object) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(float(value), upper))
