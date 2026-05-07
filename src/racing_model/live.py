from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .backtest import run_backtest
from .model import RankingModel
from .scrapers.base import PoliteHttpClient
from .scrapers.hkjc import HKJCSource
from .storage import insert_rows, upsert_race_status


@dataclass(frozen=True)
class HKJCRaceRef:
    race_date: str
    venue: str
    race_no: int


def parse_hkjc_race_id(race_id: str) -> HKJCRaceRef | None:
    match = re.fullmatch(r"HK(\d{4})(\d{2})(\d{2})-(ST|HV)-(\d{2})", race_id)
    if not match:
        return None
    year, month, day, venue, race_no = match.groups()
    return HKJCRaceRef(f"{year}/{month}/{day}", venue, int(race_no))


def refresh_hkjc_results_if_available(
    conn: sqlite3.Connection,
    race_id: str,
    model: RankingModel,
    user_agent: str,
    delay_seconds: float,
) -> dict[str, int | str] | None:
    ref = parse_hkjc_race_id(race_id)
    if not ref:
        return None

    source = HKJCSource(PoliteHttpClient(user_agent, delay_seconds))
    fetched = source.fetch_results_page(ref.race_date, ref.venue, ref.race_no)
    parsed = source.parse_results(fetched.body, ref.race_date, ref.venue, ref.race_no)
    results = parsed.get("results", [])
    odds = parsed.get("odds_ticks", [])
    now = datetime.now(timezone.utc).isoformat()
    if not results:
        upsert_race_status(
            conn,
            race_id,
            "scheduled",
            last_result_refresh_at=now,
            notes="results_not_available",
        )
        conn.commit()
        return {"race_id": race_id, "results": 0, "odds_ticks": 0, "status": "scheduled"}

    result_rows = insert_rows(conn, "results", results)
    odds_rows = insert_rows(conn, "odds_ticks", odds)
    backtest = run_backtest(conn, model)
    upsert_race_status(
        conn,
        race_id,
        "resulted",
        last_result_refresh_at=now,
        last_backtest_at=now,
        notes=f"auto_result_imported; backtest_roi={backtest.roi:.4f}",
    )
    conn.commit()
    return {
        "race_id": race_id,
        "results": result_rows,
        "odds_ticks": odds_rows,
        "status": "resulted",
    }


def load_hkjc_race_day(
    conn: sqlite3.Connection,
    race_date: str,
    venue: str,
    race_count: int,
    user_agent: str,
    delay_seconds: float,
    progress: object | None = None,
) -> dict[str, object]:
    source = HKJCSource(PoliteHttpClient(user_agent, delay_seconds))
    imported_races = 0
    imported_runners = 0
    imported_results = 0
    imported_odds = 0
    errors = []
    first_race_id = None

    for race_no in range(1, race_count + 1):
        if progress:
            progress(race_no, race_count, f"loading_race_{race_no}")
        race_id = f"HK{race_date.replace('/', '')}-{venue.upper()}-{race_no:02d}"
        if first_race_id is None:
            first_race_id = race_id
        try:
            result = source.fetch_results_page(race_date, venue, race_no)
            parsed_result = source.parse_results(result.body, race_date, venue, race_no)
            results = parsed_result.get("results", [])
            odds = parsed_result.get("odds_ticks", [])
            result_races = parsed_result.get("races", [])
            result_runners = parsed_result.get("runners", [])

            try:
                racecard = source.fetch_racecard_page(race_date, venue, race_no)
                chinese_card = source.fetch_chinese_racecard_page(race_date, venue, race_no)
                parsed_card = source.parse_racecard(
                    racecard.body,
                    race_date,
                    venue,
                    race_no,
                    chinese_card.body,
                )
                races = usable_races(parsed_card.get("races", [])) or usable_races(result_races)
                runners = parsed_card.get("runners", []) or result_runners
            except Exception:
                races = usable_races(result_races)
                runners = result_runners

            imported_races += insert_rows(conn, "races", races)
            imported_runners += insert_rows(conn, "runners", runners)
            if races:
                upsert_race_status(conn, race_id, "scheduled")

            results = parsed_result.get("results", [])
            odds = parsed_result.get("odds_ticks", [])
            if results:
                imported_results += insert_rows(conn, "results", results)
                imported_odds += insert_rows(conn, "odds_ticks", odds)
                upsert_race_status(conn, race_id, "resulted")
            elif runners:
                upsert_race_status(conn, race_id, "scheduled")
        except Exception as exc:
            errors.append({"race_no": race_no, "error": str(exc)})
        conn.commit()
        if progress:
            progress(race_no, race_count, f"finished_race_{race_no}")

    conn.commit()
    return {
        "date": race_date,
        "venue": venue,
        "requested_races": race_count,
        "imported_races": imported_races,
        "imported_runners": imported_runners,
        "imported_results": imported_results,
        "imported_odds": imported_odds,
        "errors": len(errors),
        "error_details": errors,
        "first_race_id": first_race_id,
    }


def usable_races(races: object) -> list[dict[str, object]]:
    rows = [dict(row) for row in races] if isinstance(races, list) else []
    return [
        row
        for row in rows
        if row.get("course") not in {None, ""}
        and row.get("going") not in {None, ""}
        and int(row.get("distance_m") or 0) > 0
    ]
