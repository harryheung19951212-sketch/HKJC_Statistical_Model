from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .features import build_training_races
from .live import load_hkjc_race_day, parse_hkjc_race_id, usable_races
from .model import RankingModel
from .scrapers.base import PoliteHttpClient
from .scrapers.hkjc import HKJCSource, merge_declaration_runners
from .storage import fetch_all, refresh_race_statuses
from .walk_forward import run_walk_forward_versions


ProgressCallback = Callable[[int, int, str], None]


def load_hkjc_date_range(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str,
    venue: str,
    race_count: int,
    user_agent: str,
    delay_seconds: float,
    model_path: Path | str | None = None,
    train_epochs: int = 120,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    dates = list(iter_dates(start_date, end_date))
    total_units = max(len(dates) * race_count, 1)
    current_units = 0
    day_results = []
    totals = {
        "imported_races": 0,
        "imported_runners": 0,
        "imported_results": 0,
        "imported_odds": 0,
        "skipped_completed": 0,
        "skipped_days": 0,
        "no_meeting_days": 0,
        "errors": 0,
    }
    source = HKJCSource(PoliteHttpClient(user_agent, delay_seconds))

    for day_index, race_date in enumerate(dates, start=1):
        def day_progress(done: int, total: int, message: str) -> None:
            if not progress:
                return
            progress(
                current_units + min(done, total),
                total_units,
                f"{race_date} {message}",
            )

        if progress:
            progress(current_units, total_units, f"開始回填 {race_date} ({day_index}/{len(dates)})")
        if completed_meeting_cached(conn, race_date, venue, race_count):
            result = skipped_day_result(race_date, venue, race_count, "database_completed")
        elif not hkjc_meeting_available(source, race_date, venue):
            result = skipped_day_result(race_date, venue, race_count, "no_matching_hkjc_meeting")
        else:
            result = load_hkjc_race_day(
                conn,
                race_date,
                venue,
                race_count,
                user_agent,
                delay_seconds,
                progress=day_progress,
            )
        day_results.append(result)
        for key in totals:
            totals[key] += int(result.get(key, 0) or 0)
        current_units += race_count
        if progress:
            progress(current_units, total_units, f"完成 {race_date}")

    refresh_race_statuses(conn)
    repair = repair_orphan_result_runners(conn)
    completion = complete_repaired_runners(conn, user_agent, delay_seconds)
    alignment = align_resulted_race_data(conn)
    quality = data_quality_report(conn)
    training = train_model_if_requested(conn, model_path, train_epochs)
    versions = run_walk_forward_versions(conn, epochs=60) if training["training_races"] else None
    return {
        "start_date": normalize_hkjc_date(start_date),
        "end_date": normalize_hkjc_date(end_date),
        "venue": venue.upper(),
        "days": len(dates),
        "requested_races": len(dates) * race_count,
        **totals,
        "day_results": day_results,
        "repair": repair,
        "completion": completion,
        "alignment": alignment,
        "training": training,
        "quality": quality,
        "model_versions": versions,
    }


def skipped_day_result(race_date: str, venue: str, race_count: int, reason: str) -> dict[str, Any]:
    return {
        "date": normalize_hkjc_date(race_date),
        "venue": venue.upper(),
        "requested_races": race_count,
        "imported_races": 0,
        "imported_runners": 0,
        "imported_results": 0,
        "imported_odds": 0,
        "skipped_completed": race_count if reason == "database_completed" else 0,
        "skipped_days": 1,
        "no_meeting_days": 1 if reason == "no_matching_hkjc_meeting" else 0,
        "errors": 0,
        "error_details": [],
        "first_race_id": f"HK{normalize_hkjc_date(race_date).replace('/', '')}-{venue.upper()}-01",
        "skip_reason": reason,
    }


def completed_meeting_cached(
    conn: sqlite3.Connection,
    race_date: str,
    venue: str,
    race_count: int,
) -> bool:
    rows = fetch_all(
        conn,
        """
        SELECT r.race_id, COALESCE(s.status, '') AS status, COALESCE(s.notes, '') AS notes
        FROM races r
        LEFT JOIN race_status s ON s.race_id = r.race_id
        WHERE r.date = ?
          AND r.track = ?
        ORDER BY r.race_id
        """,
        (normalize_hkjc_date(race_date), venue_track_name(venue)),
    )
    if len(rows) < race_count:
        return False
    for row in rows[:race_count]:
        runner_count = scalar_for(conn, "SELECT count(*) FROM runners WHERE race_id = ?", (row["race_id"],))
        result_count = scalar_for(conn, "SELECT count(*) FROM results WHERE race_id = ?", (row["race_id"],))
        voided = "official_void_race" in str(row["notes"] or "")
        if str(row["status"] or "") != "resulted":
            return False
        if runner_count <= 0:
            return False
        if result_count <= 0 and not voided:
            return False
    return True


def hkjc_meeting_available(source: HKJCSource, race_date: str, venue: str) -> bool:
    normalized_date = normalize_hkjc_date(race_date)
    normalized_venue = venue.upper()
    try:
        racecard = source.fetch_racecard_page(normalized_date, normalized_venue, 1)
        parsed_card = source.parse_racecard(racecard.body, normalized_date, normalized_venue, 1, None)
        if usable_races(parsed_card.get("races", [])) or parsed_card.get("runners"):
            return True
    except Exception:
        pass
    try:
        result = source.fetch_results_page(normalized_date, normalized_venue, 1)
        try:
            chinese_result = source.fetch_chinese_results_page(normalized_date, normalized_venue, 1).body
        except Exception:
            chinese_result = None
        parsed_result = source.parse_results(result.body, normalized_date, normalized_venue, 1, chinese_result)
        return bool(
            usable_races(parsed_result.get("races", []))
            or parsed_result.get("runners")
            or parsed_result.get("results")
            or parsed_result.get("voided")
        )
    except Exception:
        return False


def venue_track_name(venue: str) -> str:
    code = venue.upper()
    if code == "ST":
        return "Sha Tin"
    if code == "HV":
        return "Happy Valley"
    return venue


def repair_orphan_result_runners(conn: sqlite3.Connection) -> dict[str, Any]:
    refresh_race_statuses(conn)
    rows = fetch_all(
        conn,
        """
        SELECT x.race_id, x.horse_id, min(x.finish_position) AS finish_position, x.comment
        FROM results x
        JOIN race_status s ON s.race_id = x.race_id AND s.status = 'resulted'
        LEFT JOIN runners ru ON ru.race_id = x.race_id AND ru.horse_id = x.horse_id
        WHERE ru.horse_id IS NULL
        GROUP BY x.race_id, x.horse_id
        ORDER BY x.race_id, min(x.finish_position)
        """,
    )
    inserted = 0
    repaired = []
    for row in rows:
        defaults = race_runner_defaults(conn, row["race_id"])
        comment = parse_result_comment(row["comment"] or "")
        values = {
            "race_id": row["race_id"],
            "horse_id": row["horse_id"],
            "horse_no": None,
            "horse_name": comment["horse_name"] or row["horse_id"],
            "horse_name_zh": "",
            "jockey": comment["jockey"] or "unknown",
            "jockey_zh": "",
            "trainer": comment["trainer"] or "unknown",
            "trainer_zh": "",
            "draw": 0,
            "weight_lbs": defaults["weight_lbs"],
            "official_rating": defaults["official_rating"],
            "age": defaults["age"],
            "sex": "",
            "running_style": "unknown",
            "gear": "repaired_from_result",
        }
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO runners (
              race_id, horse_id, horse_no, horse_name, horse_name_zh,
              jockey, jockey_zh, trainer, trainer_zh, draw,
              weight_lbs, official_rating, age, sex, running_style, gear
            )
            VALUES (
              :race_id, :horse_id, :horse_no, :horse_name, :horse_name_zh,
              :jockey, :jockey_zh, :trainer, :trainer_zh, :draw,
              :weight_lbs, :official_rating, :age, :sex, :running_style, :gear
            )
            """,
            values,
        )
        if cursor.rowcount:
            inserted += 1
            repaired.append(
                {
                    "race_id": row["race_id"],
                    "horse_id": row["horse_id"],
                    "horse_name": values["horse_name"],
                    "finish_position": row["finish_position"],
                }
            )
    conn.commit()
    refresh_race_statuses(conn)
    return {
        "inserted_runners": inserted,
        "repaired": repaired,
    }


def align_resulted_race_data(conn: sqlite3.Connection, sample_limit: int = 20) -> dict[str, Any]:
    """Keep resulted races aligned to actual starters and reuse known Chinese names."""
    refresh_race_statuses(conn)
    stale_rows = fetch_all(
        conn,
        """
        SELECT ru.race_id, ru.horse_id, ru.horse_no, ru.horse_name
        FROM runners ru
        JOIN race_status s ON s.race_id = ru.race_id AND s.status = 'resulted'
        WHERE EXISTS (SELECT 1 FROM results x WHERE x.race_id = ru.race_id)
          AND NOT EXISTS (
            SELECT 1
            FROM results x
            WHERE x.race_id = ru.race_id AND x.horse_id = ru.horse_id
          )
        ORDER BY ru.race_id, COALESCE(ru.horse_no, 999), ru.horse_id
        """,
    )
    pruned = []
    deleted_odds = 0
    deleted_runners = 0
    for row in stale_rows:
        params = (row["race_id"], row["horse_id"])
        odds_cursor = conn.execute("DELETE FROM odds_ticks WHERE race_id = ? AND horse_id = ?", params)
        runner_cursor = conn.execute("DELETE FROM runners WHERE race_id = ? AND horse_id = ?", params)
        deleted_odds += int(getattr(odds_cursor, "rowcount", 0) or 0)
        deleted_runners += int(getattr(runner_cursor, "rowcount", 0) or 0)
        if len(pruned) < sample_limit:
            pruned.append(dict(row))

    horse_name_updates = fill_missing_chinese_by_key(
        conn,
        key_column="horse_id",
        zh_column="horse_name_zh",
    )
    jockey_updates = fill_missing_chinese_by_key(
        conn,
        key_column="jockey",
        zh_column="jockey_zh",
    )
    trainer_updates = fill_missing_chinese_by_key(
        conn,
        key_column="trainer",
        zh_column="trainer_zh",
    )
    inferred_horse_zh = infer_chinese_horse_names(conn)
    conn.commit()
    refresh_race_statuses(conn)
    return {
        "pruned_unmatched_resulted_runners": deleted_runners,
        "deleted_unmatched_odds_ticks": deleted_odds,
        "filled_horse_chinese_names": horse_name_updates + inferred_horse_zh,
        "filled_jockey_chinese_names": jockey_updates,
        "filled_trainer_chinese_names": trainer_updates,
        "sample_pruned": pruned,
    }


def fill_missing_chinese_by_key(conn: sqlite3.Connection, key_column: str, zh_column: str) -> int:
    rows = fetch_all(
        conn,
        f"""
        SELECT missing.race_id, missing.horse_id, known.{zh_column} AS zh_value
        FROM runners missing
        JOIN race_status ms ON ms.race_id = missing.race_id AND ms.status = 'resulted'
        JOIN (
          SELECT {key_column}, max({zh_column}) AS {zh_column}
          FROM runners
          WHERE COALESCE({key_column}, '') != ''
            AND COALESCE({zh_column}, '') != ''
          GROUP BY {key_column}
        ) known ON known.{key_column} = missing.{key_column}
        WHERE COALESCE(missing.{zh_column}, '') = ''
          AND COALESCE(known.{zh_column}, '') != ''
        """,
    )
    updated = 0
    for row in rows:
        cursor = conn.execute(
            f"UPDATE runners SET {zh_column} = ? WHERE race_id = ? AND horse_id = ? AND COALESCE({zh_column}, '') = ''",
            (row["zh_value"], row["race_id"], row["horse_id"]),
        )
        updated += int(getattr(cursor, "rowcount", 0) or 0)
    return updated


def infer_chinese_horse_names(conn: sqlite3.Connection) -> int:
    rows = fetch_all(
        conn,
        """
        SELECT race_id, horse_id, horse_name
        FROM runners
        JOIN race_status s USING (race_id)
        WHERE s.status = 'resulted'
          AND COALESCE(horse_name_zh, '') = ''
          AND COALESCE(horse_name, '') != ''
        """,
    )
    updated = 0
    for row in rows:
        name = str(row["horse_name"] or "")
        if not contains_cjk(name):
            continue
        cursor = conn.execute(
            "UPDATE runners SET horse_name_zh = ? WHERE race_id = ? AND horse_id = ? AND COALESCE(horse_name_zh, '') = ''",
            (name, row["race_id"], row["horse_id"]),
        )
        updated += int(getattr(cursor, "rowcount", 0) or 0)
    return updated


def contains_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in value)


def complete_repaired_runners(
    conn: sqlite3.Connection,
    user_agent: str,
    delay_seconds: float,
    raw_dir: Path | str = "data/raw",
) -> dict[str, Any]:
    refresh_race_statuses(conn)
    race_ids = [
        row["race_id"]
        for row in fetch_all(
            conn,
            """
            SELECT DISTINCT ru.race_id
            FROM runners ru
            JOIN race_status s ON s.race_id = ru.race_id AND s.status = 'resulted'
            WHERE ru.gear = 'repaired_from_result'
               OR COALESCE(ru.last_six_runs, '') = ''
               OR COALESCE(ru.horse_name_zh, '') = ''
               OR COALESCE(ru.jockey_zh, '') = ''
               OR COALESCE(ru.trainer_zh, '') = ''
               OR ru.body_weight_lbs IS NULL
               OR COALESCE(ru.sire, '') = ''
               OR COALESCE(ru.dam, '') = ''
            ORDER BY ru.race_id
            """,
        )
    ]
    source = HKJCSource(PoliteHttpClient(user_agent, delay_seconds))
    updated = 0
    races_checked = 0
    details = []
    for race_id in race_ids:
        ref = parse_hkjc_race_id(race_id)
        if not ref:
            continue
        races_checked += 1
        parsed = load_racecard_for_completion(source, ref.race_date, ref.venue, ref.race_no, raw_dir)
        runners = parsed.get("runners", [])
        for runner in runners:
            cursor = update_runner_from_racecard(conn, runner)
            if cursor.rowcount:
                updated += 1
                details.append(
                    {
                        "race_id": runner["race_id"],
                        "horse_id": runner["horse_id"],
                        "horse_name": runner.get("horse_name"),
                        "horse_name_zh": runner.get("horse_name_zh"),
                    }
                )
    conn.commit()
    refresh_race_statuses(conn)
    return {
        "races_checked": races_checked,
        "updated_runners": updated,
        "details": details,
    }


def load_racecard_for_completion(
    source: HKJCSource,
    race_date: str,
    venue: str,
    race_no: int,
    raw_dir: Path | str,
) -> dict[str, Any]:
    english = latest_raw_snapshot(raw_dir, "English_Racing_RaceCard", race_date, venue, race_no)
    chinese = latest_raw_snapshot(raw_dir, "Chinese_Racing_RaceCard", race_date, venue, race_no)
    if english:
        english_body = Path(english).read_text(encoding="utf-8", errors="ignore")
    else:
        english_body = source.fetch_racecard_page(race_date, venue, race_no).body
    if chinese:
        chinese_body = Path(chinese).read_text(encoding="utf-8", errors="ignore")
    else:
        chinese_body = source.fetch_chinese_racecard_page(race_date, venue, race_no).body
    parsed = source.parse_racecard(english_body, race_date, venue, race_no, chinese_body)
    if parsed.get("runners"):
        try:
            declaration = source.fetch_declaration_page(race_date, venue, race_no)
            declaration_rows = source.parse_declaration(declaration.body, race_date, venue, race_no).get("runners", [])
            if declaration_rows:
                parsed["runners"] = merge_declaration_runners(parsed["runners"], declaration_rows)  # type: ignore[index]
        except Exception:
            pass
    if parsed.get("runners"):
        return parsed
    result_body = source.fetch_results_page(race_date, venue, race_no).body
    try:
        chinese_result_body = source.fetch_chinese_results_page(race_date, venue, race_no).body
    except Exception:
        chinese_result_body = None
    return source.parse_results(result_body, race_date, venue, race_no, chinese_result_body)


def latest_raw_snapshot(
    raw_dir: Path | str,
    marker: str,
    race_date: str,
    venue: str,
    race_no: int,
) -> Path | None:
    compact_date = race_date.replace("/", "_")
    directory = Path(raw_dir)
    if not directory.exists():
        return None
    candidates = [
        path
        for path in directory.glob("*.html")
        if marker in path.name
        and f"RaceDate_{compact_date}" in path.name
        and f"Racecourse_{venue}" in path.name
        and f"RaceNo_{race_no}" in path.name
    ]
    return sorted(candidates)[-1] if candidates else None


def update_runner_from_racecard(conn: sqlite3.Connection, runner: dict[str, Any]) -> sqlite3.Cursor:
    params = dict(runner)
    params.setdefault("sire", "")
    params.setdefault("dam", "")
    return conn.execute(
        """
        UPDATE runners
        SET
          horse_no = COALESCE(:horse_no, horse_no),
          last_six_runs = CASE WHEN COALESCE(:last_six_runs, '') != '' THEN :last_six_runs ELSE last_six_runs END,
          horse_name = CASE
            WHEN COALESCE(:horse_name, '') != ''
             AND COALESCE(:horse_name, '') != COALESCE(:horse_name_zh, '')
            THEN :horse_name
            ELSE horse_name
          END,
          horse_name_zh = CASE WHEN COALESCE(:horse_name_zh, '') != '' THEN :horse_name_zh ELSE horse_name_zh END,
          jockey = CASE WHEN COALESCE(:jockey, '') != '' THEN :jockey ELSE jockey END,
          jockey_zh = CASE WHEN COALESCE(:jockey_zh, '') != '' THEN :jockey_zh ELSE jockey_zh END,
          trainer = CASE WHEN COALESCE(:trainer, '') != '' THEN :trainer ELSE trainer END,
          trainer_zh = CASE WHEN COALESCE(:trainer_zh, '') != '' THEN :trainer_zh ELSE trainer_zh END,
          draw = CASE WHEN COALESCE(:draw, 0) > 0 THEN :draw ELSE draw END,
          weight_lbs = CASE WHEN COALESCE(:weight_lbs, 0) > 0 THEN :weight_lbs ELSE weight_lbs END,
          body_weight_lbs = CASE WHEN COALESCE(:body_weight_lbs, 0) > 0 THEN :body_weight_lbs ELSE body_weight_lbs END,
          official_rating = CASE WHEN COALESCE(:official_rating, 0) > 0 THEN :official_rating ELSE official_rating END,
          age = CASE WHEN COALESCE(:age, 0) > 0 THEN :age ELSE age END,
          sex = CASE WHEN COALESCE(:sex, '') != '' THEN :sex ELSE sex END,
          sire = CASE WHEN COALESCE(:sire, '') != '' THEN :sire ELSE sire END,
          dam = CASE WHEN COALESCE(:dam, '') != '' THEN :dam ELSE dam END,
          gear = CASE
            WHEN gear = 'repaired_from_result' AND COALESCE(:gear, '') != '' THEN :gear
            WHEN gear = 'repaired_from_result' THEN ''
            ELSE gear
          END
        WHERE race_id = :race_id
          AND horse_id = :horse_id
          AND (
            gear = 'repaired_from_result'
            OR COALESCE(last_six_runs, '') = ''
            OR COALESCE(horse_name_zh, '') = ''
            OR COALESCE(jockey_zh, '') = ''
            OR COALESCE(trainer_zh, '') = ''
            OR body_weight_lbs IS NULL
            OR COALESCE(sire, '') = ''
            OR COALESCE(dam, '') = ''
          )
        """,
        params,
    )


def race_runner_defaults(conn: sqlite3.Connection, race_id: str) -> dict[str, Any]:
    rows = fetch_all(
        conn,
        """
        SELECT
          avg(weight_lbs) AS weight_lbs,
          avg(official_rating) AS official_rating,
          avg(age) AS age
        FROM runners
        WHERE race_id = ?
        """,
        (race_id,),
    )
    row = rows[0] if rows else {}
    return {
        "weight_lbs": round(float(row["weight_lbs"] or 0), 1) if row else 0.0,
        "official_rating": round(float(row["official_rating"] or 0), 1) if row else 0.0,
        "age": int(round(float(row["age"] or 0))) if row else 0,
    }


def parse_result_comment(comment: str) -> dict[str, str]:
    parts = [part.strip() for part in comment.split(";")]
    data = {
        "horse_name": parts[0] if parts else "",
        "jockey": "",
        "trainer": "",
    }
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower()
        if key in data:
            data[key] = value.strip()
    return data


def train_model_if_requested(
    conn: sqlite3.Connection,
    model_path: Path | str | None,
    epochs: int,
) -> dict[str, Any]:
    races = build_training_races(conn)
    replay = black_box_fake_ticket_replay(races, epochs=epochs)
    if not model_path or not races:
        return {
            "trained": False,
            "training_races": len(races),
            "model_path": str(model_path) if model_path else None,
            "black_box_replay": replay,
        }
    model = RankingModel.new()
    model.fit(races, epochs=epochs)
    model.save(model_path)
    return {
        "trained": True,
        "training_races": len(races),
        "model_path": str(model_path),
        "epochs": epochs,
        "black_box_method": "walk_forward_fake_ticket_replay_then_refit",
        "black_box_replay": replay,
    }


def black_box_fake_ticket_replay(
    races: list[list[Any]],
    epochs: int = 120,
    max_races: int = 30,
) -> dict[str, Any]:
    if len(races) < 2:
        return {
            "status": "insufficient_history",
            "replayed_races": 0,
            "fake_tickets": 0,
            "win_hit_rate": None,
            "place_hit_rate": None,
        }
    start_index = max(1, len(races) - max_races)
    replay_epochs = max(5, min(int(epochs // 4 or epochs), 20))
    win_tickets = 0
    win_hits = 0
    win_profit = 0.0
    place_tickets = 0
    place_hits = 0
    place_profit = 0.0
    replayed_races = 0
    for race_index in range(start_index, len(races)):
        history = races[:race_index]
        target = races[race_index]
        if not history or not target:
            continue
        model = RankingModel.new()
        model.fit(history, epochs=replay_epochs)
        predictions = model.predict_race(target)
        if not predictions:
            continue
        finish_by_horse = {runner.horse_id: runner.finish_position for runner in target}
        top_win = predictions[0]
        win_tickets += 1
        win_finish = finish_by_horse.get(str(top_win["horse_id"]))
        if win_finish == 1:
            win_hits += 1
            win_profit += float(top_win.get("latest_win_odds") or 0.0) - 1.0
        else:
            win_profit -= 1.0

        place_pick = max(predictions, key=lambda row: float(row.get("top3_probability") or 0.0))
        place_tickets += 1
        place_finish = finish_by_horse.get(str(place_pick["horse_id"]))
        if place_finish is not None and int(place_finish) <= 3:
            place_hits += 1
            place_profit += float(place_pick.get("latest_place_odds") or 0.0) - 1.0
        else:
            place_profit -= 1.0
        replayed_races += 1
    fake_tickets = win_tickets + place_tickets
    return {
        "status": "ok" if replayed_races else "insufficient_history",
        "method": "train_on_previous_races_emit_fake_win_and_place_tickets_compare_actual_results",
        "replayed_races": replayed_races,
        "fake_tickets": fake_tickets,
        "replay_epochs": replay_epochs,
        "win_tickets": win_tickets,
        "win_hits": win_hits,
        "win_hit_rate": win_hits / win_tickets if win_tickets else None,
        "win_profit_units": round(win_profit, 4),
        "place_tickets": place_tickets,
        "place_hits": place_hits,
        "place_hit_rate": place_hits / place_tickets if place_tickets else None,
        "place_profit_units": round(place_profit, 4),
    }


def data_quality_report(conn: sqlite3.Connection) -> dict[str, Any]:
    totals = {
        "races": scalar(conn, "SELECT count(*) FROM races"),
        "runners": scalar(conn, "SELECT count(*) FROM runners"),
        "results": scalar(conn, "SELECT count(*) FROM results"),
        "odds_ticks": scalar(conn, "SELECT count(*) FROM odds_ticks"),
        "workouts": scalar(conn, "SELECT count(*) FROM workouts"),
    }
    issues = [
        issue_row(
            "missing_runners",
            "有賽事但冇馬匹資料",
            scalar(conn, "SELECT count(*) FROM races r WHERE NOT EXISTS (SELECT 1 FROM runners ru WHERE ru.race_id = r.race_id)"),
        ),
        issue_row(
            "missing_results",
            "有賽事但未有賽果",
            scalar(
                conn,
                """
                SELECT count(*)
                FROM races r
                LEFT JOIN race_status s ON s.race_id = r.race_id
                WHERE COALESCE(s.status, '') != 'scheduled'
                  AND COALESCE(s.notes, '') != 'official_void_race'
                  AND NOT EXISTS (SELECT 1 FROM results x WHERE x.race_id = r.race_id)
                """,
            ),
        ),
        issue_row(
            "missing_odds",
            "有賽事但冇賠率記錄",
            scalar(
                conn,
                """
                SELECT count(*)
                FROM races r
                LEFT JOIN race_status s ON s.race_id = r.race_id
                WHERE COALESCE(s.status, '') != 'scheduled'
                  AND COALESCE(s.notes, '') != 'official_void_race'
                  AND NOT EXISTS (SELECT 1 FROM odds_ticks o WHERE o.race_id = r.race_id)
                """,
            ),
        ),
        issue_row(
            "incomplete_results",
            "賽果數少於馬匹數",
            scalar(
                conn,
                """
                SELECT count(*)
                FROM races r
                WHERE
                  (SELECT count(*) FROM results x WHERE x.race_id = r.race_id) > 0
                  AND (SELECT count(*) FROM results x WHERE x.race_id = r.race_id)
                    < (SELECT count(*) FROM runners ru WHERE ru.race_id = r.race_id)
                """,
            ),
        ),
        issue_row(
            "result_count_mismatch",
            "賽果數同馬匹數不一致",
            scalar(
                conn,
                """
                SELECT count(*)
                FROM races r
                WHERE
                  (SELECT count(*) FROM results x WHERE x.race_id = r.race_id) > 0
                  AND (SELECT count(*) FROM results x WHERE x.race_id = r.race_id)
                    != (SELECT count(*) FROM runners ru WHERE ru.race_id = r.race_id)
                """,
            ),
        ),
        issue_row(
            "orphan_results",
            "有賽果但搵唔到對應馬匹",
            scalar(
                conn,
                """
                SELECT count(*)
                FROM results x
                LEFT JOIN runners ru ON ru.race_id = x.race_id AND ru.horse_id = x.horse_id
                WHERE ru.horse_id IS NULL
                """,
            ),
        ),
        issue_row(
            "orphan_odds",
            "有賠率但搵唔到對應馬匹",
            scalar(
                conn,
                """
                SELECT count(*)
                FROM odds_ticks o
                LEFT JOIN runners ru ON ru.race_id = o.race_id AND ru.horse_id = o.horse_id
                WHERE ru.horse_id IS NULL
                """,
            ),
        ),
        issue_row(
            "missing_chinese_names",
            "馬匹中文名缺失",
            scalar(conn, "SELECT count(*) FROM runners WHERE COALESCE(horse_name_zh, '') = ''"),
        ),
        issue_row(
            "missing_jockey_trainer_zh",
            "騎師或練馬師中文名缺失",
            scalar(conn, "SELECT count(*) FROM runners WHERE COALESCE(jockey_zh, '') = '' OR COALESCE(trainer_zh, '') = ''"),
        ),
    ]
    resulted_races = scalar(
        conn,
        "SELECT count(DISTINCT race_id) FROM results",
    )
    quality_score = compute_quality_score(totals, issues)
    latest_dates = [
        dict(row)
        for row in fetch_all(
            conn,
            """
            SELECT date, count(*) AS races
            FROM races
            GROUP BY date
            ORDER BY date DESC
            LIMIT 8
            """,
        )
    ]
    return {
        "totals": totals,
        "resulted_races": resulted_races,
        "quality_score": quality_score,
        "issues": issues,
        "latest_dates": latest_dates,
    }


def iter_dates(start_date: str, end_date: str) -> list[str]:
    start = parse_date(start_date)
    end = parse_date(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y/%m/%d"))
        current += timedelta(days=1)
    return dates


def parse_date(value: str) -> datetime:
    text = value.strip().replace("-", "/")
    return datetime.strptime(text, "%Y/%m/%d")


def normalize_hkjc_date(value: str) -> str:
    return parse_date(value).strftime("%Y/%m/%d")


def scalar(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    if row is None:
        return 0
    if isinstance(row, dict):
        value = next(iter(row.values()), 0)
    else:
        value = row[0]
    return int(value or 0)


def scalar_for(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    if isinstance(row, dict):
        value = next(iter(row.values()), 0)
    else:
        value = row[0]
    return int(value or 0)


def issue_row(issue_id: str, label: str, count: int) -> dict[str, Any]:
    severity = "ok"
    if count:
        severity = "warning" if count < 10 else "high"
    return {
        "id": issue_id,
        "label": label,
        "count": count,
        "severity": severity,
    }


def compute_quality_score(totals: dict[str, int], issues: list[dict[str, Any]]) -> float:
    race_count = max(int(totals.get("races", 0)), 1)
    runner_count = max(int(totals.get("runners", 0)), 1)
    weighted = 0.0
    weights = {
        "missing_runners": 3.0,
        "missing_results": 1.5,
        "missing_odds": 1.0,
        "incomplete_results": 2.0,
        "result_count_mismatch": 2.0,
        "orphan_results": 1.5,
        "orphan_odds": 0.5,
        "missing_chinese_names": 0.25,
        "missing_jockey_trainer_zh": 0.15,
    }
    for issue in issues:
        weighted += weights.get(str(issue["id"]), 1.0) * int(issue["count"])
    penalty_base = max(race_count * 8.0, runner_count * 0.5, 20.0)
    penalty = min(weighted / penalty_base, 1.0)
    return max(0.0, 1.0 - penalty)
