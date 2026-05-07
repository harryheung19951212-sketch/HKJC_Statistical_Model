from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any


LIVE_ODDS_SOURCES = {"hkjc_graphql", "hkjc_mqtt"}
OFFICIAL_WIN_ODDS_SOURCES = LIVE_ODDS_SOURCES | {"hkjc_results_final"}


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS races (
  race_id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  track TEXT NOT NULL,
  course TEXT NOT NULL,
  distance_m INTEGER NOT NULL,
  going TEXT NOT NULL,
  class_rating TEXT NOT NULL,
  prize REAL NOT NULL,
  race_name TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS runners (
  race_id TEXT NOT NULL,
  horse_id TEXT NOT NULL,
  horse_no INTEGER,
  horse_name TEXT NOT NULL,
  horse_name_zh TEXT NOT NULL DEFAULT '',
  jockey TEXT NOT NULL,
  jockey_zh TEXT NOT NULL DEFAULT '',
  trainer TEXT NOT NULL,
  trainer_zh TEXT NOT NULL DEFAULT '',
  draw INTEGER NOT NULL,
  weight_lbs REAL NOT NULL,
  official_rating REAL NOT NULL,
  age INTEGER NOT NULL,
  sex TEXT NOT NULL,
  running_style TEXT NOT NULL,
  gear TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (race_id, horse_id)
);

CREATE TABLE IF NOT EXISTS results (
  race_id TEXT NOT NULL,
  horse_id TEXT NOT NULL,
  finish_position INTEGER NOT NULL,
  finish_time_sec REAL NOT NULL,
  margin_lengths REAL NOT NULL,
  sectional_400_sec REAL,
  sectional_800_sec REAL,
  comment TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (race_id, horse_id)
);

CREATE TABLE IF NOT EXISTS workouts (
  horse_id TEXT NOT NULL,
  date TEXT NOT NULL,
  track TEXT NOT NULL,
  work_type TEXT NOT NULL,
  distance_m INTEGER NOT NULL,
  time_sec REAL NOT NULL,
  rank INTEGER,
  notes TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (horse_id, date, work_type, distance_m)
);

CREATE TABLE IF NOT EXISTS odds_ticks (
  race_id TEXT NOT NULL,
  horse_id TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  win_odds REAL NOT NULL,
  place_odds REAL,
  source TEXT NOT NULL,
  PRIMARY KEY (race_id, horse_id, timestamp, source)
);

CREATE TABLE IF NOT EXISTS raw_snapshots (
  source TEXT NOT NULL,
  url TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  body TEXT NOT NULL,
  PRIMARY KEY (source, url, fetched_at)
);

CREATE TABLE IF NOT EXISTS race_status (
  race_id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'scheduled',
  last_odds_refresh_at TEXT,
  last_result_refresh_at TEXT,
  last_backtest_at TEXT,
  notes TEXT NOT NULL DEFAULT ''
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        migrate_schema(conn)
        conn.commit()


def migrate_schema(conn: sqlite3.Connection) -> None:
    ensure_column(conn, "runners", "horse_no", "INTEGER")
    ensure_column(conn, "runners", "horse_name_zh", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "runners", "jockey_zh", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "runners", "trainer_zh", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "races", "race_name", "TEXT NOT NULL DEFAULT ''")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def insert_rows(conn: sqlite3.Connection, table: str, rows: Iterable[dict[str, Any]]) -> int:
    rows = [clean_row(row) for row in rows]
    if not rows:
        return 0
    available_columns = table_columns(conn, table)
    rows = [
        {key: value for key, value in row.items() if key in available_columns}
        for row in rows
    ]
    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    col_sql = ", ".join(columns)
    sql = f"INSERT OR REPLACE INTO {table} ({col_sql}) VALUES ({placeholders})"
    values = [[row.get(column) for column in columns] for row in rows]
    conn.executemany(sql, values)
    return len(rows)


def import_csv(conn: sqlite3.Connection, table: str, csv_path: Path | str) -> int:
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    return insert_rows(conn, table, rows)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in row.items():
        if value == "":
            cleaned[key] = None
        else:
            cleaned[key] = value
    return cleaned


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params))


def latest_odds_by_race(conn: sqlite3.Connection, race_id: str) -> dict[str, dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT race_id, horse_id, timestamp, win_odds, place_odds, source
        FROM odds_ticks
        WHERE race_id = ?
          AND source IN ('hkjc_graphql', 'hkjc_mqtt', 'hkjc_results_final')
        ORDER BY timestamp DESC
        """,
        (race_id,),
    )
    live_win: dict[str, dict[str, Any]] = {}
    result_win: dict[str, dict[str, Any]] = {}
    live_place: dict[str, dict[str, Any]] = {}
    result_place: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        horse_id = item["horse_id"]
        if item["source"] in LIVE_ODDS_SOURCES:
            live_win.setdefault(horse_id, item)
            if item.get("place_odds") is not None:
                live_place.setdefault(horse_id, item)
        elif item["source"] == "hkjc_results_final":
            result_win.setdefault(horse_id, item)
            if item.get("place_odds") is not None:
                result_place.setdefault(horse_id, item)

    output: dict[str, dict[str, Any]] = {}
    for horse_id in set(live_win) | set(result_win) | set(live_place) | set(result_place):
        item = dict(live_win.get(horse_id) or result_win[horse_id])
        place = live_place.get(horse_id) or result_place.get(horse_id)
        item["place_odds"] = place.get("place_odds") if place else None
        item["place_source"] = place.get("source") if place else None
        output[horse_id] = item
    return output


def latest_raw_odds_by_race(conn: sqlite3.Connection, race_id: str) -> dict[str, sqlite3.Row]:
    rows = fetch_all(
        conn,
        """
        SELECT o.*
        FROM odds_ticks o
        JOIN (
          SELECT race_id, horse_id, max(timestamp) AS max_ts
          FROM odds_ticks
          WHERE race_id = ?
          GROUP BY race_id, horse_id
        ) latest
        ON o.race_id = latest.race_id
          AND o.horse_id = latest.horse_id
          AND o.timestamp = latest.max_ts
        """,
        (race_id,),
    )
    return {row["horse_id"]: row for row in rows}


def race_status(conn: sqlite3.Connection, race_id: str) -> sqlite3.Row | None:
    rows = fetch_all(conn, "SELECT * FROM race_status WHERE race_id = ?", (race_id,))
    return rows[0] if rows else None


def upsert_race_status(
    conn: sqlite3.Connection,
    race_id: str,
    status: str,
    **fields: Any,
) -> None:
    current = race_status(conn, race_id)
    values = {
        "race_id": race_id,
        "status": status,
        "last_odds_refresh_at": fields.get("last_odds_refresh_at"),
        "last_result_refresh_at": fields.get("last_result_refresh_at"),
        "last_backtest_at": fields.get("last_backtest_at"),
        "notes": fields.get("notes", ""),
    }
    if current:
        merged = dict(current)
        merged.update({key: value for key, value in values.items() if value is not None or key in {"status", "notes"}})
        values = merged
    conn.execute(
        """
        INSERT OR REPLACE INTO race_status
          (race_id, status, last_odds_refresh_at, last_result_refresh_at, last_backtest_at, notes)
        VALUES
          (:race_id, :status, :last_odds_refresh_at, :last_result_refresh_at, :last_backtest_at, :notes)
        """,
        values,
    )


def refresh_race_statuses(conn: sqlite3.Connection) -> None:
    race_ids = [row["race_id"] for row in fetch_all(conn, "SELECT race_id FROM races")]
    for race_id in race_ids:
        runner_count = fetch_all(conn, "SELECT count(*) AS n FROM runners WHERE race_id = ?", (race_id,))[0]["n"]
        result_count = fetch_all(conn, "SELECT count(*) AS n FROM results WHERE race_id = ?", (race_id,))[0]["n"]
        odds_count = fetch_all(conn, "SELECT count(*) AS n FROM odds_ticks WHERE race_id = ?", (race_id,))[0]["n"]
        if runner_count and result_count >= runner_count:
            status = "resulted"
        elif race_status(conn, race_id) and race_status(conn, race_id)["status"] == "live":
            status = "live"
        else:
            status = "scheduled"
        upsert_race_status(conn, race_id, status)
    conn.commit()
