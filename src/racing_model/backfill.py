from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .features import build_training_races
from .live import load_hkjc_race_day, parse_hkjc_race_id
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
        "errors": 0,
    }

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
        "training": training,
        "quality": quality,
        "model_versions": versions,
    }


def repair_orphan_result_runners(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = fetch_all(
        conn,
        """
        SELECT x.race_id, x.horse_id, min(x.finish_position) AS finish_position, x.comment
        FROM results x
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


def complete_repaired_runners(
    conn: sqlite3.Connection,
    user_agent: str,
    delay_seconds: float,
    raw_dir: Path | str = "data/raw",
) -> dict[str, Any]:
    race_ids = [
        row["race_id"]
        for row in fetch_all(
            conn,
            """
            SELECT DISTINCT race_id
            FROM runners
            WHERE gear = 'repaired_from_result'
               OR COALESCE(last_six_runs, '') = ''
               OR COALESCE(horse_name_zh, '') = ''
               OR COALESCE(jockey_zh, '') = ''
               OR COALESCE(trainer_zh, '') = ''
               OR body_weight_lbs IS NULL
            ORDER BY race_id
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
          )
        """,
        runner,
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
    if not model_path or not races:
        return {
            "trained": False,
            "training_races": len(races),
            "model_path": str(model_path) if model_path else None,
        }
    model = RankingModel.new()
    model.fit(races, epochs=epochs)
    model.save(model_path)
    return {
        "trained": True,
        "training_races": len(races),
        "model_path": str(model_path),
        "epochs": epochs,
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
            scalar(conn, "SELECT count(*) FROM races r WHERE NOT EXISTS (SELECT 1 FROM results x WHERE x.race_id = r.race_id)"),
        ),
        issue_row(
            "missing_odds",
            "有賽事但冇賠率記錄",
            scalar(conn, "SELECT count(*) FROM races r WHERE NOT EXISTS (SELECT 1 FROM odds_ticks o WHERE o.race_id = r.race_id)"),
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
