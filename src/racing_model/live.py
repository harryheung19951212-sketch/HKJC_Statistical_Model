from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .backtest import run_backtest
from .exotic_dividends import upsert_exotic_dividends
from .model import RankingModel
from .scrapers.base import PoliteHttpClient
from .scrapers.hkjc import HKJCSource
from .storage import freeze_final_place_snapshots, insert_rows, upsert_race_status


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
    if is_future_hkjc_race_date(ref.race_date):
        now = datetime.now(timezone.utc).isoformat()
        upsert_race_status(
            conn,
            race_id,
            "scheduled",
            last_result_refresh_at=now,
            notes="future_race_results_not_available",
        )
        conn.commit()
        return {"race_id": race_id, "results": 0, "odds_ticks": 0, "status": "scheduled"}

    source = HKJCSource(PoliteHttpClient(user_agent, delay_seconds))
    fetched = source.fetch_results_page(ref.race_date, ref.venue, ref.race_no)
    try:
        chinese = source.fetch_chinese_results_page(ref.race_date, ref.venue, ref.race_no).body
    except Exception:
        chinese = None
    parsed = source.parse_results(fetched.body, ref.race_date, ref.venue, ref.race_no, chinese)
    results = parsed.get("results", [])
    odds = parsed.get("odds_ticks", [])
    runners = parsed.get("runners", [])
    exotic_dividends = parsed.get("exotic_dividends", [])
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

    update_runner_localization(conn, runners)
    result_rows = insert_rows(conn, "results", results)
    odds_rows = insert_rows(conn, "odds_ticks", odds)
    exotic_rows = upsert_exotic_dividends(
        conn,
        race_id,
        exotic_dividends,
        source="hkjc_results_final",
        dividend_status="final",
    )["imported"] if exotic_dividends else 0
    place_snapshot_rows = freeze_final_place_snapshots(conn, race_id, now)
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
        "odds_ticks": odds_rows + place_snapshot_rows,
        "exotic_dividends": exotic_rows,
        "place_snapshots": place_snapshot_rows,
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
            future_race = is_future_hkjc_race_date(race_date)
            parsed_result = {"results": [], "odds_ticks": [], "races": [], "runners": []}
            if not future_race:
                result = source.fetch_results_page(race_date, venue, race_no)
                try:
                    chinese_result = source.fetch_chinese_results_page(race_date, venue, race_no).body
                except Exception:
                    chinese_result = None
                parsed_result = source.parse_results(result.body, race_date, venue, race_no, chinese_result)
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
                if runners:
                    if progress:
                        progress(race_no, race_count, f"loading_horse_profiles_{race_no}")
                    runners = enrich_runners_with_horse_profiles(source, runners)
            except Exception:
                races = usable_races(result_races)
                runners = result_runners

            imported_races += insert_rows(conn, "races", races)
            imported_runners += insert_rows(conn, "runners", runners)
            if races:
                upsert_race_status(conn, race_id, "scheduled")

            results = parsed_result.get("results", [])
            odds = parsed_result.get("odds_ticks", [])
            exotic_dividends = parsed_result.get("exotic_dividends", [])
            if results:
                imported_results += insert_rows(conn, "results", results)
                imported_odds += insert_rows(conn, "odds_ticks", odds)
                if exotic_dividends:
                    upsert_exotic_dividends(
                        conn,
                        race_id,
                        exotic_dividends,
                        source="hkjc_results_final",
                        dividend_status="final",
                    )
                imported_odds += freeze_final_place_snapshots(conn, race_id, datetime.now(timezone.utc).isoformat())
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


def enrich_runners_with_horse_profiles(
    source: HKJCSource,
    runners: object,
) -> list[dict[str, object]]:
    if not isinstance(runners, list):
        return []
    enriched: list[dict[str, object]] = []
    history_by_horse_id: dict[str, str] = {}
    for runner in runners:
        if not isinstance(runner, dict):
            continue
        updated = dict(runner)
        horse_id = str(runner.get("horse_id") or "").strip()
        if horse_id:
            if horse_id not in history_by_horse_id:
                history_by_horse_id[horse_id] = fetch_profile_last_six_runs(source, horse_id)
            if history_by_horse_id[horse_id]:
                updated["last_six_runs"] = history_by_horse_id[horse_id]
        enriched.append(updated)
    return enriched


def fetch_profile_last_six_runs(source: HKJCSource, horse_id: str) -> str:
    try:
        profile = source.fetch_horse_profile_page(horse_id)
        parsed = source.parse_horse_profile(profile.body)
    except Exception:
        return ""
    return str(parsed.get("last_six_runs") or "")


def update_runner_localization(conn: sqlite3.Connection, runners: object) -> int:
    if not isinstance(runners, list):
        return 0
    updated = 0
    for runner in runners:
        if not isinstance(runner, dict) or not runner.get("horse_id"):
            continue
        cursor = conn.execute(
            """
            UPDATE runners
            SET
              horse_name_zh = CASE WHEN COALESCE(:horse_name_zh, '') != '' THEN :horse_name_zh ELSE horse_name_zh END,
              jockey_zh = CASE WHEN COALESCE(:jockey_zh, '') != '' THEN :jockey_zh ELSE jockey_zh END,
              trainer_zh = CASE WHEN COALESCE(:trainer_zh, '') != '' THEN :trainer_zh ELSE trainer_zh END
            WHERE race_id = :race_id
              AND horse_id = :horse_id
              AND (
                COALESCE(horse_name_zh, '') = ''
                OR COALESCE(jockey_zh, '') = ''
                OR COALESCE(trainer_zh, '') = ''
              )
            """,
            {
                "race_id": runner.get("race_id"),
                "horse_id": runner.get("horse_id"),
                "horse_name_zh": runner.get("horse_name_zh") or "",
                "jockey_zh": runner.get("jockey_zh") or "",
                "trainer_zh": runner.get("trainer_zh") or "",
            },
        )
        updated += cursor.rowcount
    return updated


def usable_races(races: object) -> list[dict[str, object]]:
    rows = [dict(row) for row in races] if isinstance(races, list) else []
    return [
        row
        for row in rows
        if row.get("course") not in {None, ""}
        and row.get("going") not in {None, ""}
        and int(row.get("distance_m") or 0) > 0
    ]


def is_future_hkjc_race_date(race_date: str) -> bool:
    normalized = str(race_date or "").replace("-", "/")
    try:
        target = datetime.strptime(normalized, "%Y/%m/%d").date()
    except ValueError:
        return False
    today_hk = datetime.now(hong_kong_tz()).date()
    return target > today_hk


def hong_kong_tz():
    try:
        return ZoneInfo("Asia/Hong_Kong")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8))
