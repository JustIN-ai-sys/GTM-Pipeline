"""SQLite connection helpers."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# The SQL files live next to the package source, in the repo root.
SQL_DIR = Path(os.environ.get("GTM_SQL_DIR", Path(__file__).resolve().parents[2] / "sql"))


def connect(path: str | Path = "gtm.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def read_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(read_sql("schema.sql"))
    conn.commit()
