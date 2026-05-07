from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from .storage import (
    FINAL_PLACE_BACKFILL_SOURCE,
    FINAL_PLACE_SNAPSHOT_SOURCE,
    LIVE_ODDS_SOURCES,
    fetch_all,
    final_place_odds_completeness,
    latest_odds_by_race,
    race_status,
)


FINAL_ODDS_SOURCES = {"hkjc_results_final", FINAL_PLACE_SNAPSHOT_SOURCE, FINAL_PLACE_BACKFILL_SOURCE}
OFFICIAL_ODDS_SOURCES = LIVE_ODDS_SOURCES | FINAL_ODDS_SOURCES


def odds_feed_health(
    conn: sqlite3.Connection,
    race_id: str,
    expected_interval_seconds: int = 30,
) -> dict[str, Any]:
    runners = fetch_all(
        conn,
        """
        SELECT horse_id, horse_no, horse_name, horse_name_zh
        FROM runners
        WHERE race_id = ?
        ORDER BY horse_no, draw, horse_id
        """,
        (race_id,),
    )
    rows = fetch_all(
        conn,
        """
        SELECT horse_id, timestamp, win_odds, place_odds, source
        FROM odds_ticks
        WHERE race_id = ?
        ORDER BY timestamp DESC
        """,
        (race_id,),
    )
    status_row = race_status(conn, race_id)
    lifecycle_status = status_row["status"] if status_row else "scheduled"
    official_rows = [dict(row) for row in rows if row["source"] in OFFICIAL_ODDS_SOURCES]
    live_rows = [row for row in official_rows if row["source"] in LIVE_ODDS_SOURCES]
    latest_official_timestamp = max((str(row["timestamp"]) for row in official_rows), default=None)
    latest_live_timestamp = max((str(row["timestamp"]) for row in live_rows), default=None)
    completeness = final_place_odds_completeness(conn, race_id)
    latest_odds = latest_odds_by_race(conn, race_id)
    runner_reports = []
    for runner in runners:
        horse_id = str(runner["horse_id"])
        horse_rows = [row for row in official_rows if str(row["horse_id"]) == horse_id]
        win_rows = [row for row in horse_rows if row.get("win_odds") is not None]
        place_rows = [row for row in horse_rows if row.get("place_odds") is not None]
        latest_win = max(win_rows, key=lambda row: str(row["timestamp"]), default=None)
        latest_place = max(place_rows, key=lambda row: str(row["timestamp"]), default=None)
        latest = latest_odds.get(horse_id, {})
        runner_reports.append(
            {
                "horse_id": horse_id,
                "horse_no": runner["horse_no"],
                "horse_name": runner["horse_name"],
                "horse_name_zh": runner["horse_name_zh"],
                "display_name": runner["horse_name_zh"] or runner["horse_name"] or horse_id,
                "win_tick_count": len(win_rows),
                "place_tick_count": len(place_rows),
                "latest_win_odds": latest_win.get("win_odds") if latest_win else None,
                "latest_win_source": latest_win.get("source") if latest_win else None,
                "latest_win_timestamp": latest_win.get("timestamp") if latest_win else None,
                "latest_place_odds": latest_place.get("place_odds") if latest_place else None,
                "latest_place_source": latest_place.get("source") if latest_place else None,
                "latest_place_timestamp": latest_place.get("timestamp") if latest_place else None,
                "final_place_odds": latest.get("place_odds"),
                "final_place_source": latest.get("place_source"),
                "missing_win_tick": not bool(win_rows),
                "missing_place_tick": not bool(place_rows),
            }
        )

    runner_count = len(runners)
    win_covered = sum(1 for item in runner_reports if not item["missing_win_tick"])
    place_covered = sum(1 for item in runner_reports if not item["missing_place_tick"])
    latest_age_seconds = timestamp_age_seconds(latest_live_timestamp or latest_official_timestamp)
    stale_after = max(expected_interval_seconds * 2, 60)
    is_stale = (
        lifecycle_status in {"scheduled", "live"}
        and latest_age_seconds is not None
        and latest_age_seconds > stale_after
    )
    status = feed_health_status(
        lifecycle_status,
        runner_count,
        win_covered,
        place_covered,
        completeness,
        official_rows,
        is_stale,
    )
    return {
        "race_id": race_id,
        "status": status,
        "lifecycle_status": lifecycle_status,
        "runner_count": runner_count,
        "expected_interval_seconds": expected_interval_seconds,
        "latest_official_timestamp": latest_official_timestamp,
        "latest_live_timestamp": latest_live_timestamp,
        "latest_age_seconds": latest_age_seconds,
        "is_stale": is_stale,
        "official_tick_count": len(official_rows),
        "live_tick_count": len(live_rows),
        "win_covered": win_covered,
        "place_covered": place_covered,
        "win_missing": max(runner_count - win_covered, 0),
        "place_missing": max(runner_count - place_covered, 0),
        "place_odds_completeness": completeness,
        "training_ready": lifecycle_status == "resulted" and bool(completeness.get("complete")),
        "runners": runner_reports,
        "source_counts": source_counts(official_rows),
    }


def feed_health_status(
    lifecycle_status: str,
    runner_count: int,
    win_covered: int,
    place_covered: int,
    completeness: dict[str, Any],
    official_rows: list[dict[str, Any]],
    is_stale: bool,
) -> str:
    if runner_count == 0:
        return "missing_runners"
    if lifecycle_status == "resulted":
        return "training_ready" if completeness.get("complete") else "incomplete_final_place"
    if is_stale:
        return "stale"
    if win_covered >= runner_count and place_covered >= runner_count:
        return "recording_complete"
    if official_rows:
        return "partial"
    return "no_official_ticks"


def source_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        source = str(row["source"])
        counts[source] = counts.get(source, 0) + 1
    return counts


def timestamp_age_seconds(value: str | None) -> float | None:
    if not value:
        return None
    parsed = parse_timestamp(value)
    if not parsed:
        return None
    return max((datetime.now(timezone.utc) - parsed).total_seconds(), 0.0)


def parse_timestamp(value: str) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
