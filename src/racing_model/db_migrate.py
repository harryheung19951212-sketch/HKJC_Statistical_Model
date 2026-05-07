from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .storage import connect, fetch_all, init_db, insert_rows


MIGRATION_TABLES = [
    "races",
    "runners",
    "results",
    "workouts",
    "odds_ticks",
    "raw_snapshots",
    "race_status",
    "race_error_reviews",
]


def migrate_sqlite_to_database(
    sqlite_path: Path | str,
    target: Path | str,
    replace: bool = False,
) -> dict[str, Any]:
    source_path = Path(sqlite_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    init_db(target)
    imported: dict[str, int] = {}
    with sqlite3.connect(source_path) as source:
        source.row_factory = sqlite3.Row
        with connect(target) as dest:
            if replace:
                for table in reversed(MIGRATION_TABLES):
                    dest.execute(f"DELETE FROM {table}")
            for table in MIGRATION_TABLES:
                rows = [dict(row) for row in source.execute(f"SELECT * FROM {table}")]
                imported[table] = insert_rows(dest, table, rows)
            dest.commit()
    return {
        "source": str(source_path),
        "target": display_database_target(target),
        "replace": replace,
        "tables": imported,
        "total_rows": sum(imported.values()),
    }


def database_counts(target: Path | str) -> dict[str, int]:
    with connect(target) as conn:
        return {
            table: int(fetch_all(conn, f"SELECT count(*) AS n FROM {table}")[0]["n"])
            for table in MIGRATION_TABLES
        }


def display_database_target(target: Path | str) -> str:
    value = str(target)
    if not value.startswith(("postgres://", "postgresql://")):
        return value
    parts = urlsplit(value)
    if not parts.password:
        return value
    username = parts.username or ""
    hostname = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    auth = f"{username}:***@" if username else ""
    return urlunsplit((parts.scheme, f"{auth}{hostname}{port}", parts.path, parts.query, parts.fragment))
