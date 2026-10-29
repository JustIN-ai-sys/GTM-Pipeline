"""Bring a Clay table back into the pipeline as dated, sourced signals.

Clay is where a GTM team enriches accounts (tech stack, hiring, funding). Export the table as
CSV and `gtm import-clay` turns its tech stack column into CRM signals the score already uses:

    gtm import-clay clay-export.csv --column "Tech Stack"

- Rows are matched to accounts by domain, else by exact company name; others are counted and
  skipped, never created.
- A CRM named in the cell is `confirmed`; a filled cell without it is `not_found`; an empty
  cell is no data and stores nothing. A `not_found` never replaces an earlier `confirmed`.
- What Clay reports is technology seen on the company's website, not proof the sales team
  works in that CRM; the source says so (`clay_tech_stack`) and so does the date.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path

from gtm_pipeline.loader import _pick, _read, normalize_domain, upsert_signal

# Signal name -> words that identify it in a tech stack cell (case-insensitive).
CRM_KEYWORDS = {
    "crm_hubspot": ("hubspot",),
    "crm_salesforce": ("salesforce",),
    "crm_pipedrive": ("pipedrive",),
}
DOMAIN_COLUMNS = ["Company Domain", "Domain", "Website", "Company Website"]
NAME_COLUMNS = ["Company Name", "Company", "Name"]


def find_org(conn: sqlite3.Connection, row: dict[str, str]) -> int | None:
    domain = normalize_domain(_pick(row, DOMAIN_COLUMNS))
    if domain:
        found = conn.execute(
            "SELECT id FROM organizations WHERE lower(domain) = ?", (domain,)
        ).fetchone()
        if found:
            return found["id"]
    name = _pick(row, NAME_COLUMNS)
    if name:
        found = conn.execute(
            "SELECT id FROM organizations WHERE lower(name) = lower(?)", (name,)
        ).fetchone()
        if found:
            return found["id"]
    return None


def crm_signals(cell: str | None) -> dict[str, str]:
    """{'crm_hubspot': 'confirmed', ...} for a filled cell; {} when Clay returned nothing."""
    text = (cell or "").strip().lower()
    if not text or text in ("[]", "null", "none", "n/a"):
        return {}
    return {
        name: "confirmed" if any(word in text for word in words) else "not_found"
        for name, words in CRM_KEYWORDS.items()
    }


def import_tech_stack(
    conn: sqlite3.Connection,
    path: str | Path,
    column: str = "Tech Stack",
    source: str = "clay_tech_stack",
    as_of: str | None = None,
) -> Counter:
    observed = as_of or date.today().isoformat()
    result: Counter = Counter()
    for row in _read(path):
        result["rows"] += 1
        org_id = find_org(conn, row)
        if org_id is None:
            result["unmatched"] += 1
            continue
        signals = crm_signals(row.get(column))
        if not signals:
            result["no_data"] += 1
            continue
        result["matched"] += 1
        for name, status in signals.items():
            upsert_signal(conn, org_id, name, status, source, observed)
            result[status] += 1
    conn.commit()
    return result
