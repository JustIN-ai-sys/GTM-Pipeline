"""Load CRM CSV exports (Pipedrive native headers or plain headers) into SQLite."""

from __future__ import annotations

import csv
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

DEFAULT_STAGES = ["Lead", "Contacted", "Meeting", "Proposal", "Negotiation"]

# Each field accepts the Pipedrive export header first, then a plain fallback.
ORG_ALIASES = {
    "name": ["Organization - Name", "Name", "Organization"],
    "domain": ["Organization - Website", "Website domain", "Domain", "Website"],
    "industry": ["Organization - Industry", "Industry"],
    "employees": ["Organization - Employees", "Employees (approx.)", "Employees"],
    "country": ["Organization - Country", "Country", "HQ / location"],
}
PEOPLE_ALIASES = {
    "first": ["Person - First name", "First name"],
    "last": ["Person - Last name", "Last name"],
    "full": ["Person - Name", "Name"],
    "org": ["Person - Organization", "Organization"],
    "title": ["Person - Job title", "Job title"],
    "email": ["Person - Email - Work", "Person - Email", "Email", "Work Email"],
    "linkedin": ["Person - LinkedIn", "LinkedIn URL", "LinkedIn"],
}
DEAL_ALIASES = {
    "title": ["Deal - Title", "Title"],
    "org": ["Deal - Organization", "Organization"],
    "value": ["Deal - Value", "Value"],
    "stage": ["Deal - Stage", "Stage"],
    "status": ["Deal - Status", "Status"],
    "last_activity": ["Deal - Last activity date", "Last activity date"],
    "created": ["Deal - Deal created", "Created"],
}


def _read(path: str | Path) -> Iterator[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def _pick(row: dict[str, str], names: list[str]) -> str | None:
    for name in names:
        value = row.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def parse_employees(raw: str | None) -> int | None:
    """'51-200' -> 125, '~120' -> 120, '1,500' -> 1500, '' -> None."""
    if not raw:
        return None
    numbers = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", raw)]
    if not numbers:
        return None
    if len(numbers) >= 2:
        return (numbers[0] + numbers[1]) // 2
    return numbers[0]


def normalize_domain(raw: str | None) -> str | None:
    if not raw:
        return None
    domain = re.sub(r"^https?://", "", raw.strip().lower())
    domain = re.sub(r"^www\.", "", domain)
    return domain.split("/")[0] or None


def parse_date(raw: str | None) -> str | None:
    """Keep the YYYY-MM-DD part; anything else becomes NULL."""
    if not raw:
        return None
    match = re.match(r"\d{4}-\d{2}-\d{2}", raw.strip())
    return match.group(0) if match else None


def parse_value(raw: str | None) -> float:
    if not raw:
        return 0.0
    cleaned = re.sub(r"[^\d.\-]", "", raw.replace(",", ""))
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def ensure_stages(conn: sqlite3.Connection, names: list[str]) -> None:
    conn.execute("DELETE FROM stages")
    conn.executemany(
        "INSERT INTO stages (name, position) VALUES (?, ?)",
        [(name, index) for index, name in enumerate(names, start=1)],
    )


def _org_id(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO organizations (name) VALUES (?)", (name,))
    row = conn.execute("SELECT id FROM organizations WHERE name = ?", (name,)).fetchone()
    return int(row["id"])


def load_organizations(conn: sqlite3.Connection, path: str | Path) -> int:
    count = 0
    for row in _read(path):
        name = _pick(row, ORG_ALIASES["name"])
        if not name:
            continue
        conn.execute(
            """
            INSERT INTO organizations (name, domain, industry, employees, country)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (name) DO UPDATE SET
                domain = COALESCE(excluded.domain, organizations.domain),
                industry = COALESCE(excluded.industry, organizations.industry),
                employees = COALESCE(excluded.employees, organizations.employees),
                country = COALESCE(excluded.country, organizations.country)
            """,
            (
                name,
                normalize_domain(_pick(row, ORG_ALIASES["domain"])),
                _pick(row, ORG_ALIASES["industry"]),
                parse_employees(_pick(row, ORG_ALIASES["employees"])),
                _pick(row, ORG_ALIASES["country"]),
            ),
        )
        count += 1
    return count


def load_people(conn: sqlite3.Connection, path: str | Path) -> int:
    count = 0
    for row in _read(path):
        first = _pick(row, PEOPLE_ALIASES["first"])
        last = _pick(row, PEOPLE_ALIASES["last"]) or ""
        if not first:
            full = _pick(row, PEOPLE_ALIASES["full"])
            if not full:
                continue
            first, _, last = full.partition(" ")
        org_name = _pick(row, PEOPLE_ALIASES["org"])
        if not org_name:
            continue  # a contact without an account cannot be scored
        email = _pick(row, PEOPLE_ALIASES["email"])
        conn.execute(
            """
            INSERT INTO people
                (org_id, first_name, last_name, title, email, linkedin_url, email_status,
                 email_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (org_id, first_name, last_name) DO UPDATE SET
                title = COALESCE(excluded.title, people.title),
                linkedin_url = COALESCE(excluded.linkedin_url, people.linkedin_url),
                email_status = CASE
                    WHEN people.email IS NULL AND excluded.email IS NOT NULL THEN 'unverified'
                    ELSE people.email_status
                END,
                email_source = CASE
                    WHEN people.email IS NULL AND excluded.email IS NOT NULL
                        THEN excluded.email_source
                    ELSE people.email_source
                END,
                email = COALESCE(people.email, excluded.email)
            """,
            (
                _org_id(conn, org_name),
                first,
                last.strip(),
                _pick(row, PEOPLE_ALIASES["title"]),
                email,
                _pick(row, PEOPLE_ALIASES["linkedin"]),
                "unverified" if email else "missing",
                "crm_import" if email else None,
            ),
        )
        count += 1
    return count


def load_deals(conn: sqlite3.Connection, path: str | Path) -> int:
    count = 0
    for row in _read(path):
        title = _pick(row, DEAL_ALIASES["title"])
        org_name = _pick(row, DEAL_ALIASES["org"])
        if not title or not org_name:
            continue
        status = (_pick(row, DEAL_ALIASES["status"]) or "open").lower()
        if status not in {"open", "won", "lost"}:
            status = "open"
        conn.execute(
            """
            INSERT INTO deals
                (org_id, title, value, stage, status, last_activity_date, created_date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (org_id, title) DO UPDATE SET
                value = excluded.value,
                stage = excluded.stage,
                status = excluded.status,
                last_activity_date = excluded.last_activity_date,
                created_date = COALESCE(excluded.created_date, deals.created_date)
            """,
            (
                _org_id(conn, org_name),
                title,
                parse_value(_pick(row, DEAL_ALIASES["value"])),
                _pick(row, DEAL_ALIASES["stage"]),
                status,
                parse_date(_pick(row, DEAL_ALIASES["last_activity"])),
                parse_date(_pick(row, DEAL_ALIASES["created"])),
            ),
        )
        count += 1
    return count


SIGNAL_ALIASES = {
    "org": ["Organization", "Organization - Name", "Name", "Company"],
    "name": ["Signal", "Name"],
    "status": ["Status"],
    "source": ["Source"],
    "observed": ["Observed at", "Observed", "Date"],
}
# A confirmed signal is kept when a later load says "not found" or "unknown".
KEEP_CONFIRMED = "(signals.status = 'confirmed' AND excluded.status <> 'confirmed')"
SIGNAL_STATUSES = {"confirmed", "not_found", "unknown"}


def load_signals(conn: sqlite3.Connection, path: str | Path) -> dict[str, int]:
    """Load observed facts (CSV: Organization, Signal, Status, Source, Observed at).

    A signal is a status with a source and a date, never a bare yes/no. A later
    "not_found" or "unknown" never replaces an earlier "confirmed": not finding a
    technology is not proof that the company stopped using it.
    """
    loaded = skipped = 0
    for row in _read(path):
        org_name = _pick(row, SIGNAL_ALIASES["org"])
        name = _pick(row, SIGNAL_ALIASES["name"])
        source = _pick(row, SIGNAL_ALIASES["source"])
        org = (
            conn.execute("SELECT id FROM organizations WHERE name = ?", (org_name,)).fetchone()
            if org_name
            else None
        )
        if not (org and name and source):
            skipped += 1  # unknown account, or no source: refuse to store an unsourced claim
            continue
        status = (_pick(row, SIGNAL_ALIASES["status"]) or "unknown").lower()
        upsert_signal(
            conn,
            org["id"],
            name,
            status,
            source,
            parse_date(_pick(row, SIGNAL_ALIASES["observed"])),
        )
        loaded += 1
    return {"signals": loaded, "signals_skipped": skipped}


def upsert_signal(
    conn: sqlite3.Connection,
    org_id: int,
    name: str,
    status: str,
    source: str,
    observed_at: str | None,
) -> None:
    """Store one observed fact. A confirmed signal is never replaced by a weaker one."""
    status = status if status in SIGNAL_STATUSES else "unknown"
    conn.execute(
        f"""
        INSERT INTO signals (org_id, name, status, source, observed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (org_id, name) DO UPDATE SET
            status = CASE WHEN {KEEP_CONFIRMED} THEN signals.status ELSE excluded.status END,
            source = CASE WHEN {KEEP_CONFIRMED} THEN signals.source ELSE excluded.source END,
            observed_at = CASE WHEN {KEEP_CONFIRMED}
                               THEN signals.observed_at ELSE excluded.observed_at END
        """,
        (org_id, name.lower(), status, source, observed_at),
    )


def load_suppressions(conn: sqlite3.Connection, path: str | Path) -> int:
    """Load do-not-contact entries (CSV: Kind, Value, Reason). Kind is email, domain or org."""
    count = 0
    for row in _read(path):
        kind = (_pick(row, ["Kind", "Type"]) or "").lower()
        value = _pick(row, ["Value"])
        if kind not in {"email", "domain", "org"} or not value:
            continue
        value = normalize_domain(value) if kind == "domain" else value.lower().strip()
        conn.execute(
            "INSERT OR REPLACE INTO suppressions (kind, value, reason) VALUES (?, ?, ?)",
            (kind, value, _pick(row, ["Reason"])),
        )
        count += 1
    return count


def load_all(
    conn: sqlite3.Connection,
    orgs: str | Path,
    people: str | Path | None = None,
    deals: str | Path | None = None,
    stages: list[str] | None = None,
    signals: str | Path | None = None,
    suppressions: str | Path | None = None,
) -> dict[str, int]:
    """Load the exports in dependency order. Safe to re-run (upserts).

    Only `orgs` is required, so a list of companies with no CRM history can be scored too.
    """
    ensure_stages(conn, stages or DEFAULT_STAGES)
    counts = {"organizations": load_organizations(conn, orgs)}
    if people:
        counts["people"] = load_people(conn, people)
    if deals:
        counts["deals"] = load_deals(conn, deals)
    if signals:
        counts.update(load_signals(conn, signals))
    if suppressions:
        counts["suppressions"] = load_suppressions(conn, suppressions)
    conn.commit()
    return counts
