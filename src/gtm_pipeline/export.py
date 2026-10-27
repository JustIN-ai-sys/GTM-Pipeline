"""Hand-off to a sequencer or a CRM: a CSV of contacts that are ready to work.

This module never calls a sending tool or a CRM API. It writes a file the user imports into
lemlist (or another sequencer) or Pipedrive, after reading it. Three gates, in this order:

    1. Only contacts with a usable email: verified statuses by default (`deliverable`,
       `high_probability`); `unverified` emails (typed into the CRM) only on request.
    2. Never a suppressed email, domain or organization (checked again here, not only before
       enrichment: the list may have grown since).
    3. Accounts below `min_score` are left out. Contacts are ordered by account score.

Each row carries the account score, the reason for reaching out (the confirmed signals) and the
next action, so the sequence can be personalised and the CRM keeps why the contact was chosen.
"""

from __future__ import annotations

import csv
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gtm_pipeline import scoring
from gtm_pipeline.enrich import suppressed_sets

VERIFIED_STATUSES = ("deliverable", "high_probability")

# Human labels for signal names, used in personalisation columns.
SIGNAL_LABELS = {
    "crm_hubspot": "HubSpot",
    "crm_salesforce": "Salesforce",
    "crm_pipedrive": "Pipedrive",
}

# Column order per destination. lemlist maps unknown columns to custom variables, which can be
# used in templates as {{accountScore}}, {{crmUsed}}, ... Pipedrive headers match the loader's
# aliases, so an export can be loaded back with `gtm load --people`.
FORMATS: dict[str, list[tuple[str, str]]] = {
    "lemlist": [
        ("email", "email"),
        ("firstName", "first_name"),
        ("lastName", "last_name"),
        ("companyName", "company"),
        ("companyDomain", "domain"),
        ("jobTitle", "title"),
        ("linkedinUrl", "linkedin_url"),
        ("accountScore", "score"),
        ("crmUsed", "crm_used"),
        ("nextAction", "next_action"),
    ],
    "pipedrive": [
        ("Person - First name", "first_name"),
        ("Person - Last name", "last_name"),
        ("Person - Email - Work", "email"),
        ("Person - Job title", "title"),
        ("Person - LinkedIn", "linkedin_url"),
        ("Person - Organization", "company"),
        ("Organization - Website", "domain"),
        ("Note", "note"),
    ],
}

SKIP_REASONS = {
    "no_email": "No email",
    "unverified": "Email not verified (use --include-unverified to keep)",
    "suppressed": "On the suppression list (email, domain or organization)",
    "low_score": "Account below --min-score",
}


@dataclass(frozen=True)
class ExportResult:
    rows: list[dict[str, Any]]
    skipped: Counter


def signal_label(name: str) -> str:
    return SIGNAL_LABELS.get(name, name.removeprefix("crm_").replace("_", " ").title())


def ready_contacts(
    conn: sqlite3.Connection,
    as_of: str | None = None,
    min_score: int = 0,
    include_unverified: bool = False,
) -> ExportResult:
    """Contacts worth handing to a sequencer, best accounts first, with why they were chosen."""
    scores = {row["org_id"]: row for row in scoring.score_accounts(conn, as_of)}
    sup_emails, sup_domains, sup_orgs = suppressed_sets(conn)
    allowed = VERIFIED_STATUSES + (("unverified",) if include_unverified else ())
    people = conn.execute(
        """
        SELECT p.id, p.org_id, p.first_name, p.last_name, p.title, p.email, p.email_status,
               p.email_source, p.email_checked_at, p.linkedin_url,
               o.name AS company, o.domain
        FROM people AS p
        JOIN organizations AS o ON o.id = p.org_id
        ORDER BY p.id
        """
    ).fetchall()
    rows: list[dict[str, Any]] = []
    skipped: Counter = Counter()
    for person in people:
        email = (person["email"] or "").strip()
        account = scores.get(person["org_id"])
        if not email:
            skipped["no_email"] += 1
        elif person["email_status"] not in allowed:
            skipped["unverified"] += 1
        elif (
            email.lower() in sup_emails
            or email.rsplit("@", 1)[-1].lower() in sup_domains
            or (person["domain"] or "").lower() in sup_domains
            or person["company"].lower() in sup_orgs
        ):
            skipped["suppressed"] += 1
        elif account is None or account["score"] < min_score:
            skipped["low_score"] += 1
        else:
            rows.append(_row(person, account))
    rows.sort(key=lambda r: (-r["score"], r["company"], r["last_name"], r["first_name"]))
    return ExportResult(rows, skipped)


def _row(person: sqlite3.Row, account: sqlite3.Row) -> dict[str, Any]:
    signals = [s.strip() for s in (account["signals"] or "").split(",") if s.strip()]
    crm_used = ", ".join(signal_label(s) for s in signals)
    why = f"score {account['score']} (tier {account['tier']})"
    if crm_used:
        why += f", uses {crm_used}"
    source = person["email_source"] or "crm"
    checked = person["email_checked_at"] or "unknown date"
    return {
        "first_name": person["first_name"],
        "last_name": person["last_name"],
        "email": person["email"].strip(),
        "title": person["title"] or "",
        "linkedin_url": person["linkedin_url"] or "",
        "company": person["company"],
        "domain": person["domain"] or "",
        "score": account["score"],
        "tier": account["tier"],
        "crm_used": crm_used,
        "next_action": account["next_action"],
        "note": (
            f"gtm-pipeline: {why}. Email {person['email_status']} from {source}, checked {checked}."
        ),
    }


def write_csv(rows: list[dict[str, Any]], path: str | Path, fmt: str) -> None:
    columns = FORMATS[fmt]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([header for header, _ in columns])
        for row in rows:
            writer.writerow([row[key] for _, key in columns])


def render_summary(result: ExportResult, fmt: str, path: str | Path) -> str:
    """Counts only: no names or emails, so it can be pasted anywhere."""
    lines = [f"Exported {len(result.rows)} contacts for {fmt} to {path}"]
    accounts = Counter(row["company"] for row in result.rows)
    if accounts:
        lines.append(f"Accounts: {len(accounts)}, best score {result.rows[0]['score']}")
    for reason, label in SKIP_REASONS.items():
        if result.skipped.get(reason):
            lines.append(f"Skipped - {label}: {result.skipped[reason]}")
    lines.append("Nothing was sent. Read the file, then import it yourself.")
    return "\n".join(lines)
