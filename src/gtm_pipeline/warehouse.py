"""Copy the local SQLite database to a Postgres warehouse (Supabase or any Postgres).

SQLite stays the working copy: loads, enrichment and exports run there. `push` refreshes the
warehouse from it in ONE transaction (all tables or nothing), then (re)creates the
`account_scores` view, so dashboards, SQL clients or other tools read the same score.

    pip install -e ".[warehouse]"
    gtm warehouse push                 # dry run: row counts, target host
    gtm warehouse push --yes           # writes; reads DATABASE_URL

The driver (psycopg) is an optional dependency; nothing else in the package needs it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from gtm_pipeline.db import read_sql

# Parents before children, so foreign keys hold during the load.
TABLES: list[tuple[str, list[str]]] = [
    ("organizations", ["id", "name", "domain", "industry", "employees", "country"]),
    ("stages", ["name", "position"]),
    ("icp_rules", ["industry", "weight"]),
    ("signal_rules", ["name", "points"]),
    (
        "people",
        [
            "id",
            "org_id",
            "first_name",
            "last_name",
            "title",
            "email",
            "linkedin_url",
            "email_status",
            "email_source",
            "email_checked_at",
        ],
    ),
    (
        "deals",
        ["id", "org_id", "title", "value", "stage", "status", "last_activity_date", "created_date"],
    ),
    ("signals", ["id", "org_id", "name", "status", "source", "observed_at"]),
    ("suppressions", ["id", "kind", "value", "reason"]),
    ("enrichment_attempts", ["person_id", "status", "source", "checked_at"]),
    (
        "enrichment_runs",
        ["enrichment_id", "created_at", "contacts_sent", "status", "credits_spent"],
    ),
]
# Columns that are dates in Postgres: SQLite stores them as text, sometimes empty.
DATE_COLUMNS = {
    "email_checked_at",
    "last_activity_date",
    "created_date",
    "observed_at",
    "checked_at",
    "created_at",
}

Connect = Callable[[str], Any]


class WarehouseError(RuntimeError):
    """Raised for warehouse errors. Never includes the connection password."""


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        for table, _ in TABLES
    }


def describe_target(url: str) -> str:
    """Host and database only: 'postgresql://user:<password>@db.x.co:5432/postgres' gives
    'db.x.co/postgres'."""
    parts = urlsplit(url)
    return f"{parts.hostname or '?'}{parts.path or ''}"


def _rows(conn: sqlite3.Connection, table: str, columns: list[str]) -> list[tuple]:
    rows = conn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()  # noqa: S608
    dates = [i for i, column in enumerate(columns) if column in DATE_COLUMNS]
    out = []
    for row in rows:
        values = list(row)
        for i in dates:
            if values[i] == "":
                values[i] = None
        out.append(tuple(values))
    return out


def push(conn: sqlite3.Connection, pg: Any) -> dict[str, int]:
    """Refresh every warehouse table from SQLite, then the score view. All or nothing."""
    written: dict[str, int] = {}
    try:
        with pg.transaction():
            pg.execute("SET LOCAL TIME ZONE 'UTC'")
            pg.execute(read_sql("postgres/schema.sql"))
            names = ", ".join(table for table, _ in TABLES)
            pg.execute(f"TRUNCATE {names}")
            for table, columns in TABLES:
                rows = _rows(conn, table, columns)
                if rows:
                    placeholders = ", ".join(["%s"] * len(columns))
                    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
                    with pg.cursor() as cursor:
                        cursor.executemany(sql, rows)
                written[table] = len(rows)
            pg.execute(read_sql("postgres/account_scores.sql"))
    except Exception as exc:  # psycopg errors; keep only the first line, never the URL
        message = str(exc).splitlines()[0] if str(exc) else ""
        raise WarehouseError(f"{type(exc).__name__}: {message}") from None
    return written


def connect(url: str) -> Any:
    try:
        import psycopg
    except ImportError:
        raise WarehouseError('psycopg is not installed: pip install -e ".[warehouse]"') from None
    try:
        return psycopg.connect(url, autocommit=True)
    except psycopg.Error as exc:
        raise WarehouseError(
            f"Could not connect to {describe_target(url)} ({type(exc).__name__})"
        ) from None
