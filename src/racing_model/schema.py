from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Race:
    race_id: str
    date: str
    track: str
    course: str
    distance_m: int
    going: str
    class_rating: str
    prize: float


@dataclass(frozen=True)
class Runner:
    race_id: str
    horse_id: str
    horse_name: str
    jockey: str
    trainer: str
    draw: int
    weight_lbs: float
    body_weight_lbs: float | None
    official_rating: float
    age: int
    sex: str
    running_style: str
    gear: str


@dataclass(frozen=True)
class Result:
    race_id: str
    horse_id: str
    finish_position: int
    finish_time_sec: float
    margin_lengths: float
    sectional_400_sec: float | None
    sectional_800_sec: float | None
    running_positions: str
    comment: str


@dataclass(frozen=True)
class Workout:
    horse_id: str
    date: str
    track: str
    work_type: str
    distance_m: int
    time_sec: float
    rank: int | None
    notes: str


@dataclass(frozen=True)
class OddsTick:
    race_id: str
    horse_id: str
    timestamp: str
    win_odds: float
    place_odds: float | None
    source: str
