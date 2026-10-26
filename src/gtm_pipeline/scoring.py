"""Run the SQL account score and the pipeline health report."""

from __future__ import annotations

import csv
import sqlite3
from datetime import date
from pathlib import Path

from gtm_pipeline.db import read_sql


def _as_of(as_of: str | None) -> str:
    return as_of or date.today().isoformat()


def score_accounts(conn: sqlite3.Connection, as_of: str | None = None) -> list[sqlite3.Row]:
    return conn.execute(read_sql("score_accounts.sql"), {"as_of": _as_of(as_of)}).fetchall()


def pipeline_health(conn: sqlite3.Connection, as_of: str | None = None) -> list[sqlite3.Row]:
    return conn.execute(read_sql("pipeline_health.sql"), {"as_of": _as_of(as_of)}).fetchall()


def write_scores_csv(rows: list[sqlite3.Row], path: str | Path) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(rows[0].keys())
        writer.writerows([tuple(row) for row in rows])
