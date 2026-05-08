from __future__ import annotations

import csv
import re
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LIVE_ODDS_SOURCES = {"hkjc_graphql", "hkjc_mqtt"}
FINAL_PLACE_SNAPSHOT_SOURCE = "hkjc_final_place_snapshot"
FINAL_PLACE_BACKFILL_SOURCE = "hkjc_final_place_backfill"
FINAL_PLACE_SOURCES = {"hkjc_results_final", FINAL_PLACE_SNAPSHOT_SOURCE, FINAL_PLACE_BACKFILL_SOURCE}
OFFICIAL_WIN_ODDS_SOURCES = LIVE_ODDS_SOURCES | FINAL_PLACE_SOURCES


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

CREATE TABLE IF NOT EXISTS race_error_reviews (
  race_id TEXT NOT NULL,
  review_key TEXT NOT NULL,
  category TEXT NOT NULL,
  severity TEXT NOT NULL,
  horse_id TEXT,
  horse_no INTEGER,
  horse_name TEXT NOT NULL DEFAULT '',
  model_rank INTEGER,
  finish_position INTEGER,
  probability REAL,
  expected_value REAL,
  odds REAL,
  evidence TEXT NOT NULL DEFAULT '',
  recommendation TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (race_id, review_key)
);

CREATE TABLE IF NOT EXISTS model_registry_runs (
  run_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  trigger TEXT NOT NULL DEFAULT 'manual',
  model_path TEXT NOT NULL DEFAULT '',
  min_train_races INTEGER NOT NULL,
  epochs INTEGER NOT NULL,
  min_expected_value REAL NOT NULL,
  stake REAL NOT NULL,
  race_count INTEGER NOT NULL,
  folds INTEGER NOT NULL,
  best_variant_id TEXT NOT NULL DEFAULT '',
  best_label TEXT NOT NULL DEFAULT '',
  baseline_log_loss REAL,
  best_log_loss REAL,
  baseline_value_roi REAL,
  best_value_roi REAL,
  best_top_pick_hit_rate REAL,
  best_max_drawdown REAL,
  execution_confirmed INTEGER NOT NULL DEFAULT 0,
  execution_roi REAL,
  execution_max_drawdown REAL,
  execution_gate TEXT NOT NULL DEFAULT 'unverified',
  promotion_gate TEXT NOT NULL DEFAULT 'sample_insufficient',
  recommendation TEXT NOT NULL DEFAULT '',
  report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS betting_recommendations (
  recommendation_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'api_betting',
  model_path TEXT NOT NULL DEFAULT '',
  race_id TEXT NOT NULL,
  race_date TEXT NOT NULL DEFAULT '',
  market TEXT NOT NULL,
  market_label TEXT NOT NULL DEFAULT '',
  horse_id TEXT NOT NULL,
  horse_no INTEGER,
  horse_name TEXT NOT NULL DEFAULT '',
  model_rank INTEGER NOT NULL DEFAULT 0,
  risk_profile TEXT NOT NULL DEFAULT '',
  bankroll REAL NOT NULL DEFAULT 0,
  probability REAL,
  recommended_odds REAL,
  odds_source TEXT NOT NULL DEFAULT '',
  fair_odds REAL,
  market_probability REAL,
  edge REAL,
  expected_value REAL,
  cost_adjusted_expected_value REAL,
  required_dividend REAL,
  minimum_ticket_cost REAL,
  pool_choice_score REAL,
  pool_choice_rank INTEGER,
  pool_choice_verdict TEXT NOT NULL DEFAULT '',
  slip_strategy TEXT NOT NULL DEFAULT '',
  slip_rank INTEGER,
  slip_priority_score REAL,
  risk_tier TEXT NOT NULL DEFAULT '',
  portfolio_role TEXT NOT NULL DEFAULT '',
  expected_profit REAL,
  hit_probability REAL,
  recommended_stake REAL NOT NULL DEFAULT 0,
  race_status_at_recommendation TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  execution_status TEXT NOT NULL DEFAULT 'suggested',
  executed_at TEXT,
  execution_odds REAL,
  execution_stake REAL,
  execution_source TEXT NOT NULL DEFAULT '',
  execution_slippage REAL,
  execution_clv REAL,
  execution_value_status TEXT NOT NULL DEFAULT '',
  execution_value_message TEXT NOT NULL DEFAULT '',
  execution_edge_at_bet REAL,
  execution_expected_value_at_bet REAL,
  final_odds REAL,
  finish_position INTEGER,
  outcome_win INTEGER,
  returned REAL,
  profit REAL,
  clv REAL,
  slippage REAL,
  reconciled_at TEXT,
  reconciliation_status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS exotic_dividends (
  race_id TEXT NOT NULL,
  market TEXT NOT NULL,
  combination_key TEXT NOT NULL,
  combination TEXT NOT NULL DEFAULT '',
  dividend REAL NOT NULL,
  dividend_status TEXT NOT NULL DEFAULT 'probable',
  source TEXT NOT NULL DEFAULT 'manual',
  fetched_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (race_id, market, combination_key, dividend_status, source)
);
"""


TABLE_PRIMARY_KEYS = {
    "races": ["race_id"],
    "runners": ["race_id", "horse_id"],
    "results": ["race_id", "horse_id"],
    "workouts": ["horse_id", "date", "work_type", "distance_m"],
    "odds_ticks": ["race_id", "horse_id", "timestamp", "source"],
    "raw_snapshots": ["source", "url", "fetched_at"],
    "race_status": ["race_id"],
    "race_error_reviews": ["race_id", "review_key"],
    "model_registry_runs": ["run_id"],
    "betting_recommendations": ["recommendation_id"],
    "exotic_dividends": ["race_id", "market", "combination_key", "dividend_status", "source"],
}


class PostgresConnection:
    def __init__(self, url: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - optional production dependency
            raise RuntimeError("Install psycopg[binary] to use PostgreSQL storage.") from exc
        self.raw = psycopg.connect(url, row_factory=dict_row)

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type:
            self.raw.rollback()
        else:
            self.raw.commit()
        self.raw.close()

    def execute(self, sql: str, params: Any = ()) -> Any:
        statement, values = translate_postgres_sql(sql, params)
        if not statement:
            return EmptyCursor()
        return self.raw.execute(statement, values)

    def executemany(self, sql: str, params_seq: Iterable[Any]) -> Any:
        statement, _ = translate_postgres_sql(sql, ())
        with self.raw.cursor() as cursor:
            cursor.executemany(statement, list(params_seq))
            return cursor

    def executescript(self, sql: str) -> None:
        for statement in sql.split(";"):
            translated, params = translate_postgres_sql(statement, ())
            if translated:
                self.raw.execute(translated, params)

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()


class EmptyCursor:
    rowcount = 0

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []


def connect(db_path: Path | str) -> sqlite3.Connection | PostgresConnection:
    target = str(db_path)
    if is_postgres_url(target):
        return PostgresConnection(target)
    path = Path(target)
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
    ensure_column(conn, "betting_recommendations", "execution_status", "TEXT NOT NULL DEFAULT 'suggested'")
    ensure_column(conn, "betting_recommendations", "executed_at", "TEXT")
    ensure_column(conn, "betting_recommendations", "execution_odds", "REAL")
    ensure_column(conn, "betting_recommendations", "execution_stake", "REAL")
    ensure_column(conn, "betting_recommendations", "execution_source", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "betting_recommendations", "execution_slippage", "REAL")
    ensure_column(conn, "betting_recommendations", "execution_clv", "REAL")
    ensure_column(conn, "betting_recommendations", "cost_adjusted_expected_value", "REAL")
    ensure_column(conn, "betting_recommendations", "required_dividend", "REAL")
    ensure_column(conn, "betting_recommendations", "minimum_ticket_cost", "REAL")
    ensure_column(conn, "betting_recommendations", "pool_choice_score", "REAL")
    ensure_column(conn, "betting_recommendations", "pool_choice_rank", "INTEGER")
    ensure_column(conn, "betting_recommendations", "pool_choice_verdict", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "betting_recommendations", "slip_strategy", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "betting_recommendations", "slip_rank", "INTEGER")
    ensure_column(conn, "betting_recommendations", "slip_priority_score", "REAL")
    ensure_column(conn, "betting_recommendations", "risk_tier", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "betting_recommendations", "portfolio_role", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "betting_recommendations", "expected_profit", "REAL")
    ensure_column(conn, "betting_recommendations", "hit_probability", "REAL")
    ensure_column(conn, "betting_recommendations", "execution_value_status", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "betting_recommendations", "execution_value_message", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "betting_recommendations", "execution_edge_at_bet", "REAL")
    ensure_column(conn, "betting_recommendations", "execution_expected_value_at_bet", "REAL")
    ensure_column(conn, "model_registry_runs", "execution_confirmed", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "model_registry_runs", "execution_roi", "REAL")
    ensure_column(conn, "model_registry_runs", "execution_max_drawdown", "REAL")
    ensure_column(conn, "model_registry_runs", "execution_gate", "TEXT NOT NULL DEFAULT 'unverified'")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS race_error_reviews (
          race_id TEXT NOT NULL,
          review_key TEXT NOT NULL,
          category TEXT NOT NULL,
          severity TEXT NOT NULL,
          horse_id TEXT,
          horse_no INTEGER,
          horse_name TEXT NOT NULL DEFAULT '',
          model_rank INTEGER,
          finish_position INTEGER,
          probability REAL,
          expected_value REAL,
          odds REAL,
          evidence TEXT NOT NULL DEFAULT '',
          recommendation TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (race_id, review_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_registry_runs (
          run_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          trigger TEXT NOT NULL DEFAULT 'manual',
          model_path TEXT NOT NULL DEFAULT '',
          min_train_races INTEGER NOT NULL,
          epochs INTEGER NOT NULL,
          min_expected_value REAL NOT NULL,
          stake REAL NOT NULL,
          race_count INTEGER NOT NULL,
          folds INTEGER NOT NULL,
          best_variant_id TEXT NOT NULL DEFAULT '',
          best_label TEXT NOT NULL DEFAULT '',
          baseline_log_loss REAL,
          best_log_loss REAL,
          baseline_value_roi REAL,
          best_value_roi REAL,
          best_top_pick_hit_rate REAL,
          best_max_drawdown REAL,
          execution_confirmed INTEGER NOT NULL DEFAULT 0,
          execution_roi REAL,
          execution_max_drawdown REAL,
          execution_gate TEXT NOT NULL DEFAULT 'unverified',
          promotion_gate TEXT NOT NULL DEFAULT 'sample_insufficient',
          recommendation TEXT NOT NULL DEFAULT '',
          report_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS betting_recommendations (
          recommendation_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'api_betting',
          model_path TEXT NOT NULL DEFAULT '',
          race_id TEXT NOT NULL,
          race_date TEXT NOT NULL DEFAULT '',
          market TEXT NOT NULL,
          market_label TEXT NOT NULL DEFAULT '',
          horse_id TEXT NOT NULL,
          horse_no INTEGER,
          horse_name TEXT NOT NULL DEFAULT '',
          model_rank INTEGER NOT NULL DEFAULT 0,
          risk_profile TEXT NOT NULL DEFAULT '',
          bankroll REAL NOT NULL DEFAULT 0,
          probability REAL,
          recommended_odds REAL,
          odds_source TEXT NOT NULL DEFAULT '',
          fair_odds REAL,
          market_probability REAL,
          edge REAL,
          expected_value REAL,
          cost_adjusted_expected_value REAL,
          required_dividend REAL,
          minimum_ticket_cost REAL,
          pool_choice_score REAL,
          pool_choice_rank INTEGER,
          pool_choice_verdict TEXT NOT NULL DEFAULT '',
          slip_strategy TEXT NOT NULL DEFAULT '',
          slip_rank INTEGER,
          slip_priority_score REAL,
          risk_tier TEXT NOT NULL DEFAULT '',
          portfolio_role TEXT NOT NULL DEFAULT '',
          expected_profit REAL,
          hit_probability REAL,
          recommended_stake REAL NOT NULL DEFAULT 0,
          race_status_at_recommendation TEXT NOT NULL DEFAULT '',
          action TEXT NOT NULL DEFAULT '',
          reason TEXT NOT NULL DEFAULT '',
          execution_status TEXT NOT NULL DEFAULT 'suggested',
          executed_at TEXT,
          execution_odds REAL,
          execution_stake REAL,
          execution_source TEXT NOT NULL DEFAULT '',
          execution_slippage REAL,
          execution_clv REAL,
          execution_value_status TEXT NOT NULL DEFAULT '',
          execution_value_message TEXT NOT NULL DEFAULT '',
          execution_edge_at_bet REAL,
          execution_expected_value_at_bet REAL,
          final_odds REAL,
          finish_position INTEGER,
          outcome_win INTEGER,
          returned REAL,
          profit REAL,
          clv REAL,
          slippage REAL,
          reconciled_at TEXT,
          reconciliation_status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exotic_dividends (
          race_id TEXT NOT NULL,
          market TEXT NOT NULL,
          combination_key TEXT NOT NULL,
          combination TEXT NOT NULL DEFAULT '',
          dividend REAL NOT NULL,
          dividend_status TEXT NOT NULL DEFAULT 'probable',
          source TEXT NOT NULL DEFAULT 'manual',
          fetched_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          notes TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (race_id, market, combination_key, dividend_status, source)
        )
        """
    )


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
    col_sql = ", ".join(columns)
    values = [[row.get(column) for column in columns] for row in rows]
    if is_postgres_connection(conn):
        placeholders = ", ".join("%s" for _ in columns)
        conflict = TABLE_PRIMARY_KEYS.get(table, [])
        update_columns = [column for column in columns if column not in conflict]
        if conflict and update_columns:
            conflict_sql = ", ".join(conflict)
            update_sql = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)
            sql = (
                f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}"
            )
        elif conflict:
            conflict_sql = ", ".join(conflict)
            sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) ON CONFLICT ({conflict_sql}) DO NOTHING"
        else:
            sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
        conn.executemany(sql, values)
    else:
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT OR REPLACE INTO {table} ({col_sql}) VALUES ({placeholders})"
        conn.executemany(sql, values)
    return len(rows)


def import_csv(conn: sqlite3.Connection, table: str, csv_path: Path | str) -> int:
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    return insert_rows(conn, table, rows)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if is_postgres_connection(conn):
        rows = conn.execute(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table,),
        ).fetchall()
        return {row["name"] for row in rows}
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def fetch_all(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params))


def is_postgres_url(value: str) -> bool:
    return value.startswith(("postgres://", "postgresql://"))


def is_postgres_connection(conn: object) -> bool:
    return isinstance(conn, PostgresConnection)


def translate_postgres_sql(sql: str, params: Any) -> tuple[str, Any]:
    statement = sql.strip()
    if not statement or statement.upper().startswith("PRAGMA "):
        return "", params
    statement = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", statement, flags=re.I)
    if "ON CONFLICT" not in statement.upper() and re.match(r"INSERT\s+INTO\s+\w+", statement, re.I):
        table_match = re.match(r"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)", statement, re.I | re.S)
        if table_match and "OR IGNORE" in sql.upper():
            table = table_match.group(1)
            conflict = TABLE_PRIMARY_KEYS.get(table, [])
            if conflict:
                statement = f"{statement} ON CONFLICT ({', '.join(conflict)}) DO NOTHING"
    if isinstance(params, dict):
        statement = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", statement)
        return statement, params
    return statement.replace("?", "%s"), params


def latest_odds_by_race(conn: sqlite3.Connection, race_id: str) -> dict[str, dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT race_id, horse_id, timestamp, win_odds, place_odds, source
        FROM odds_ticks
        WHERE race_id = ?
          AND source IN (
            'hkjc_graphql',
            'hkjc_mqtt',
            'hkjc_results_final',
            'hkjc_final_place_snapshot',
            'hkjc_final_place_backfill'
          )
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
        elif item["source"] in FINAL_PLACE_SOURCES:
            result_win.setdefault(horse_id, item)
            if item.get("place_odds") is not None:
                result_place.setdefault(horse_id, item)

    output: dict[str, dict[str, Any]] = {}
    for horse_id in set(live_win) | set(result_win) | set(live_place) | set(result_place):
        item = dict(live_win.get(horse_id) or result_win[horse_id])
        place = result_place.get(horse_id) or live_place.get(horse_id)
        item["place_odds"] = place.get("place_odds") if place else None
        item["place_source"] = place.get("source") if place else None
        output[horse_id] = item
    return output


def final_place_snapshot_rows(conn: sqlite3.Connection, race_id: str, timestamp: str) -> list[dict[str, Any]]:
    existing_rows = fetch_all(
        conn,
        """
        SELECT horse_id
        FROM odds_ticks
        WHERE race_id = ?
          AND source IN ('hkjc_results_final', 'hkjc_final_place_snapshot', 'hkjc_final_place_backfill')
          AND place_odds IS NOT NULL
        """,
        (race_id,),
    )
    existing_place_horses = {str(row["horse_id"]) for row in existing_rows}
    rows = fetch_all(
        conn,
        """
        SELECT o.race_id, o.horse_id, o.win_odds, o.place_odds
        FROM odds_ticks o
        JOIN (
          SELECT race_id, horse_id, max(timestamp) AS max_ts
          FROM odds_ticks
          WHERE race_id = ?
            AND source IN ('hkjc_graphql', 'hkjc_mqtt')
            AND place_odds IS NOT NULL
          GROUP BY race_id, horse_id
        ) latest
          ON o.race_id = latest.race_id
         AND o.horse_id = latest.horse_id
         AND o.timestamp = latest.max_ts
        WHERE o.place_odds IS NOT NULL
        """,
        (race_id,),
    )
    return [
        {
            "race_id": race_id,
            "horse_id": row["horse_id"],
            "timestamp": timestamp,
            "win_odds": row["win_odds"],
            "place_odds": row["place_odds"],
            "source": FINAL_PLACE_SNAPSHOT_SOURCE,
        }
        for row in rows
        if str(row["horse_id"]) not in existing_place_horses
    ]


def freeze_final_place_snapshots(conn: sqlite3.Connection, race_id: str, timestamp: str) -> int:
    rows = final_place_snapshot_rows(conn, race_id, timestamp)
    if not rows:
        return 0
    return insert_rows(conn, "odds_ticks", rows)


def final_place_odds_completeness(conn: sqlite3.Connection, race_id: str) -> dict[str, Any]:
    runner_rows = fetch_all(
        conn,
        """
        SELECT horse_id
        FROM runners
        WHERE race_id = ?
        """,
        (race_id,),
    )
    latest = latest_odds_by_race(conn, race_id)
    runner_ids = [str(row["horse_id"]) for row in runner_rows]
    missing = [horse_id for horse_id in runner_ids if not latest.get(horse_id, {}).get("place_odds")]
    return {
        "runner_count": len(runner_ids),
        "place_odds_count": len(runner_ids) - len(missing),
        "missing_count": len(missing),
        "missing_horse_ids": missing,
        "complete": bool(runner_ids) and not missing,
    }


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
    insert_rows(conn, "race_status", [values])


def refresh_race_statuses(conn: sqlite3.Connection) -> None:
    race_ids = [row["race_id"] for row in fetch_all(conn, "SELECT race_id FROM races")]
    for race_id in race_ids:
        race_rows = fetch_all(conn, "SELECT date FROM races WHERE race_id = ?", (race_id,))
        race_date = race_rows[0]["date"] if race_rows else ""
        runner_count = fetch_all(conn, "SELECT count(*) AS n FROM runners WHERE race_id = ?", (race_id,))[0]["n"]
        result_count = fetch_all(conn, "SELECT count(*) AS n FROM results WHERE race_id = ?", (race_id,))[0]["n"]
        if is_future_race_date(race_date):
            status = "scheduled"
        elif runner_count and result_count >= runner_count:
            status = "resulted"
        elif race_status(conn, race_id) and race_status(conn, race_id)["status"] == "live":
            status = "live"
        else:
            status = "scheduled"
        upsert_race_status(conn, race_id, status)
    conn.commit()


def is_future_race_date(value: object) -> bool:
    text = str(value or "").replace("-", "/")
    try:
        target = datetime.strptime(text, "%Y/%m/%d").date()
    except ValueError:
        return False
    return target > datetime.now(hong_kong_tz()).date()


def hong_kong_tz():
    try:
        return ZoneInfo("Asia/Hong_Kong")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8))
