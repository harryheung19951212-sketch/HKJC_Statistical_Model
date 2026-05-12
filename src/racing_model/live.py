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
from .scrapers.hkjc import HKJCSource, merge_declaration_runners
from .storage import fetch_all, freeze_final_place_snapshots, insert_rows, race_status, upsert_race_status


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
    cached = completed_result_cache(conn, race_id)
    if cached and not (is_future_hkjc_race_date(ref.race_date) or before_hkjc_result_window(ref)):
        return {
            "race_id": race_id,
            "results": cached["results"],
            "odds_ticks": cached["odds_ticks"],
            "status": "resulted",
            "source": "database_cache",
        }
    if is_future_hkjc_race_date(ref.race_date) or before_hkjc_result_window(ref):
        now = datetime.now(timezone.utc).isoformat()
        upsert_race_status(
            conn,
            race_id,
            "scheduled",
            last_result_refresh_at=now,
            notes="race_not_due_for_official_results",
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
    voided = bool(parsed.get("voided"))
    now = datetime.now(timezone.utc).isoformat()
    if not results:
        if voided:
            insert_rows(conn, "races", usable_races(parsed.get("races", [])))
            insert_rows(conn, "runners", runners)
            upsert_race_status(
                conn,
                race_id,
                "resulted",
                last_result_refresh_at=now,
                notes="official_void_race",
            )
            conn.commit()
            return {"race_id": race_id, "results": 0, "odds_ticks": 0, "status": "resulted", "voided": 1}
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
    profile_cache: dict[str, str] = {}
    imported_races = 0
    imported_runners = 0
    imported_results = 0
    imported_odds = 0
    skipped_completed = 0
    errors = []
    first_race_id = None

    for race_no in range(1, race_count + 1):
        if progress:
            progress(race_no, race_count, f"loading_race_{race_no}")
        race_id = f"HK{race_date.replace('/', '')}-{venue.upper()}-{race_no:02d}"
        if first_race_id is None:
            first_race_id = race_id
        try:
            ref = HKJCRaceRef(race_date, venue.upper(), race_no)
            if completed_result_cache(conn, race_id):
                skipped_completed += 1
                if progress:
                    progress(race_no, race_count, f"skipped_completed_race_{race_no}")
                continue
            future_race = is_future_hkjc_race_date(race_date) or before_hkjc_result_window(ref)
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
                    try:
                        declaration = source.fetch_declaration_page(race_date, venue, race_no)
                        declaration_rows = source.parse_declaration(declaration.body, race_date, venue, race_no).get("runners", [])
                        runners = merge_declaration_runners(runners, declaration_rows) if declaration_rows else runners
                    except Exception:
                        pass
                if runners:
                    if progress:
                        progress(race_no, race_count, f"loading_horse_profiles_{race_no}")
                    runners = enrich_runners_with_horse_profiles(source, runners, conn=conn, profile_cache=profile_cache)
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
            voided = bool(parsed_result.get("voided"))
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
            elif voided:
                upsert_race_status(conn, race_id, "resulted", notes="official_void_race")
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
        "skipped_completed": skipped_completed,
        "errors": len(errors),
        "error_details": errors,
        "first_race_id": first_race_id,
    }


def completed_result_cache(conn: sqlite3.Connection, race_id: str) -> dict[str, int] | None:
    status = race_status(conn, race_id)
    if not status or status["status"] != "resulted":
        return None
    result_count = int(fetch_all(conn, "SELECT count(*) AS n FROM results WHERE race_id = ?", (race_id,))[0]["n"] or 0)
    odds_count = int(fetch_all(conn, "SELECT count(*) AS n FROM odds_ticks WHERE race_id = ?", (race_id,))[0]["n"] or 0)
    notes = str(status["notes"] or "")
    if result_count <= 0 and "official_void_race" not in notes:
        return None
    return {"results": result_count, "odds_ticks": odds_count}


def enrich_runners_with_horse_profiles(
    source: HKJCSource,
    runners: object,
    conn: sqlite3.Connection | None = None,
    profile_cache: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    if not isinstance(runners, list):
        return []
    cache = profile_cache if profile_cache is not None else {}
    existing_last_six = cached_last_six_runs(conn, runners)
    enriched: list[dict[str, object]] = []
    for runner in runners:
        if not isinstance(runner, dict):
            continue
        updated = dict(runner)
        horse_id = str(runner.get("horse_id") or "").strip()
        if horse_id and not str(updated.get("last_six_runs") or "").strip():
            cached = cache.get(horse_id) or existing_last_six.get(horse_id, "")
            if not cached:
                cached = fetch_profile_last_six_runs(source, horse_id)
            if cached:
                cache[horse_id] = cached
                updated["last_six_runs"] = cached
        enriched.append(updated)
    return enriched


def cached_last_six_runs(conn: sqlite3.Connection | None, runners: list[dict[str, object]]) -> dict[str, str]:
    if conn is None:
        return {}
    horse_ids = sorted(
        {
            str(runner.get("horse_id") or "").strip()
            for runner in runners
            if str(runner.get("horse_id") or "").strip() and not str(runner.get("last_six_runs") or "").strip()
        }
    )
    if not horse_ids:
        return {}
    placeholders = ", ".join("?" for _ in horse_ids)
    rows = fetch_all(
        conn,
        f"""
        SELECT horse_id, last_six_runs
        FROM runners
        WHERE horse_id IN ({placeholders})
          AND COALESCE(last_six_runs, '') != ''
        ORDER BY horse_id, race_id DESC
        """,
        tuple(horse_ids),
    )
    cached: dict[str, str] = {}
    for row in rows:
        horse_id = str(row["horse_id"] or "").strip()
        last_six_runs = str(row["last_six_runs"] or "").strip()
        if horse_id and last_six_runs and horse_id not in cached:
            cached[horse_id] = last_six_runs
    return cached


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
              last_six_runs = CASE WHEN COALESCE(:last_six_runs, '') != '' THEN :last_six_runs ELSE last_six_runs END,
              horse_name_zh = CASE WHEN COALESCE(:horse_name_zh, '') != '' THEN :horse_name_zh ELSE horse_name_zh END,
              jockey_zh = CASE WHEN COALESCE(:jockey_zh, '') != '' THEN :jockey_zh ELSE jockey_zh END,
              trainer_zh = CASE WHEN COALESCE(:trainer_zh, '') != '' THEN :trainer_zh ELSE trainer_zh END,
              body_weight_lbs = CASE WHEN COALESCE(:body_weight_lbs, 0) > 0 THEN :body_weight_lbs ELSE body_weight_lbs END
            WHERE race_id = :race_id
              AND horse_id = :horse_id
              AND (
                COALESCE(last_six_runs, '') = ''
                OR COALESCE(horse_name_zh, '') = ''
                OR COALESCE(jockey_zh, '') = ''
                OR COALESCE(trainer_zh, '') = ''
                OR body_weight_lbs IS NULL
              )
            """,
            {
                "race_id": runner.get("race_id"),
                "horse_id": runner.get("horse_id"),
                "last_six_runs": runner.get("last_six_runs") or "",
                "horse_name_zh": runner.get("horse_name_zh") or "",
                "jockey_zh": runner.get("jockey_zh") or "",
                "trainer_zh": runner.get("trainer_zh") or "",
                "body_weight_lbs": runner.get("body_weight_lbs"),
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


def before_hkjc_result_window(ref: HKJCRaceRef, now: datetime | None = None) -> bool:
    post_time = estimated_hkjc_post_time(ref)
    if post_time is None:
        return False
    current = now.astimezone(hong_kong_tz()) if now else datetime.now(hong_kong_tz())
    return current < post_time + timedelta(minutes=5)


def estimated_hkjc_post_time(ref: HKJCRaceRef) -> datetime | None:
    normalized = str(ref.race_date or "").replace("-", "/")
    try:
        race_date = datetime.strptime(normalized, "%Y/%m/%d").date()
    except ValueError:
        return None
    first_post_hour = 18 if ref.venue.upper() == "HV" else 12
    first_post_minute = 40 if ref.venue.upper() == "HV" else 30
    first_post = datetime(
        race_date.year,
        race_date.month,
        race_date.day,
        first_post_hour,
        first_post_minute,
        tzinfo=hong_kong_tz(),
    )
    return first_post + timedelta(minutes=max(int(ref.race_no) - 1, 0) * 35)


def hong_kong_tz():
    try:
        return ZoneInfo("Asia/Hong_Kong")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8))
