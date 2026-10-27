"""FullEnrich bulk enrichment: choose who to look up, build payloads, store outcomes.

Endpoints (https://docs.fullenrich.com):
    POST /api/v2/contact/enrich/bulk        start a job (max 100 contacts per call)
    GET  /api/v1/contact/enrich/bulk/{id}   fetch results (webhook is the preferred route)

Nothing here calls the network unless you pass an API key and use `start_bulk` /
`fetch_results` yourself, or run the CLI with --live. Tests inject a fake opener.

Spending credits is gated three times, before any request is made:
    1. `classify` removes contacts that must not be paid for (already have an email,
       suppressed, outside the ICP, looked up recently and not found).
    2. `render_report` shows what is left and the maximum cost, as markdown.
    3. The CLI refuses to go live without --live and --yes.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

BASE_URL = "https://app.fullenrich.com/api"
BATCH_SIZE = 100
GOOD_EMAIL_STATUSES = {"DELIVERABLE", "HIGH_PROBABILITY"}
DEFAULT_COOLDOWN_DAYS = 90

Opener = Callable[..., Any]
# (person_id, email or None, status or None). email None means "looked up, nothing found".
Outcome = tuple[int, str | None, str | None]


class EnrichError(RuntimeError):
    """Raised for provider errors. Never includes the API key."""


# --------------------------------------------------------------------------- selection


@dataclass(frozen=True)
class Filters:
    """Who is worth paying to enrich. Every field is optional."""

    min_employees: int | None = None
    max_employees: int | None = None
    require_icp_industry: bool = False
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS


REASONS = {
    "ok": "Eligible",
    "has_email": "Already has an email",
    "no_lookup_key": "Nothing to look up (no LinkedIn URL, or no name + company domain)",
    "suppressed": "On the suppression list (domain or organization)",
    "size_unknown": "Company size unknown (a size filter is on)",
    "too_small": "Below the minimum company size",
    "too_large": "Above the maximum company size",
    "industry_not_in_icp": "Industry not in the ICP table",
    "cooldown": "Looked up recently and not found (cooldown)",
}


def _today() -> str:
    return date.today().isoformat()


def suppressed_sets(conn: sqlite3.Connection) -> tuple[set[str], set[str], set[str]]:
    emails: set[str] = set()
    domains: set[str] = set()
    orgs: set[str] = set()
    for row in conn.execute("SELECT kind, value FROM suppressions"):
        {"email": emails, "domain": domains, "org": orgs}[row["kind"]].add(row["value"].lower())
    return emails, domains, orgs


def classify(
    conn: sqlite3.Connection, filters: Filters | None = None, as_of: str | None = None
) -> list[tuple[sqlite3.Row, str]]:
    """Return (row, reason) for every person. Reason 'ok' means worth enriching."""
    filters = filters or Filters()
    as_of = as_of or _today()
    _, domains, orgs = suppressed_sets(conn)
    cutoff = (date.fromisoformat(as_of) - timedelta(days=filters.cooldown_days)).isoformat()
    rows = conn.execute(
        """
        SELECT p.id, p.first_name, p.last_name, p.title, p.email, p.linkedin_url,
               o.domain, o.name AS company, o.industry, o.employees,
               (SELECT 1 FROM icp_rules AS r WHERE lower(r.industry) = lower(o.industry)
                                                  AND r.weight > 0) AS in_icp,
               a.status AS attempt_status, a.checked_at AS attempt_at
        FROM people AS p
        JOIN organizations AS o ON o.id = p.org_id
        LEFT JOIN enrichment_attempts AS a ON a.person_id = p.id
        ORDER BY p.id
        """
    ).fetchall()
    result = []
    for row in rows:
        result.append((row, _reason(row, filters, cutoff, domains, orgs)))
    return result


def _reason(
    row: sqlite3.Row, filters: Filters, cutoff: str, domains: set[str], orgs: set[str]
) -> str:
    if row["email"]:
        return "has_email"
    has_key = bool(row["linkedin_url"]) or bool(row["domain"] and row["last_name"])
    if not has_key:
        return "no_lookup_key"
    if (row["domain"] and row["domain"].lower() in domains) or row["company"].lower() in orgs:
        return "suppressed"
    employees = row["employees"]
    if (filters.min_employees or filters.max_employees) and employees is None:
        return "size_unknown"
    if filters.min_employees and employees < filters.min_employees:
        return "too_small"
    if filters.max_employees and employees > filters.max_employees:
        return "too_large"
    if filters.require_icp_industry and not row["in_icp"]:
        return "industry_not_in_icp"
    if row["attempt_status"] == "not_found" and row["attempt_at"] and row["attempt_at"] >= cutoff:
        return "cooldown"
    return "ok"


def candidates(
    conn: sqlite3.Connection,
    limit: int | None = None,
    filters: Filters | None = None,
    as_of: str | None = None,
) -> list[sqlite3.Row]:
    """People worth enriching, in a stable order, capped at `limit`."""
    rows = [row for row, reason in classify(conn, filters, as_of) if reason == "ok"]
    return rows[: int(limit)] if limit else rows


def render_report(
    conn: sqlite3.Connection,
    filters: Filters | None = None,
    as_of: str | None = None,
    limit: int | None = None,
) -> str:
    """Markdown summary to read BEFORE spending credits. No personal names or emails."""
    classified = classify(conn, filters, as_of)
    counts = Counter(reason for _, reason in classified)
    eligible = [row for row, reason in classified if reason == "ok"]
    selected = eligible[: int(limit)] if limit else eligible
    lines = [
        "# Enrichment report",
        "",
        f"Date: {as_of or _today()}  ",
        f"People in the database: {len(classified)}",
        "",
        "| Outcome | People |",
        "|---|---|",
    ]
    for reason, label in REASONS.items():
        if counts.get(reason):
            lines.append(f"| {label} | {counts[reason]} |")
    lines += [
        "",
        f"**To enrich now: {len(selected)}** "
        f"({len(eligible)} eligible, limit {limit or 'none'}), "
        f"in {-(-len(selected) // BATCH_SIZE)} batch(es).",
        f"Maximum cost: {len(selected)} credits (one per work email found; "
        "nothing is charged when no email is found).",
        "",
        "Companies in this batch (first 10):",
        "",
    ]
    seen: list[str] = []
    for row in selected:
        if row["company"] not in seen:
            seen.append(row["company"])
    lines += [f"- {name}" for name in seen[:10]] or ["- none"]
    lines += ["", "Run with `--live --yes` to spend the credits. Start with `--limit 10`.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- provider calls


def _contact_payload(row: sqlite3.Row) -> dict[str, Any]:
    item: dict[str, Any] = {
        "enrich_fields": ["contact.work_emails"],
        "custom": {"person_id": str(row["id"])},
    }
    if row["linkedin_url"]:
        item["linkedin_url"] = row["linkedin_url"]
    else:
        item.update(
            first_name=row["first_name"],
            last_name=row["last_name"],
            domain=row["domain"],
            company_name=row["company"],
        )
    return item


def build_payloads(
    rows: Iterable[sqlite3.Row],
    name: str,
    webhook_url: str | None = None,
    batch_size: int = BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Split contacts into provider-sized batches."""
    items = [_contact_payload(row) for row in rows]
    payloads = []
    for start in range(0, len(items), batch_size):
        payload: dict[str, Any] = {
            "name": f"{name} #{start // batch_size + 1}",
            "data": items[start : start + batch_size],
        }
        if webhook_url:
            payload["webhook_url"] = webhook_url
        payloads.append(payload)
    return payloads


def _request(
    method: str, url: str, api_key: str, body: dict | None, opener: Opener, timeout: int = 30
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - fixed https base URL
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with opener(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise EnrichError(f"FullEnrich returned HTTP {exc.code} for {method} {url}") from None
    except urllib.error.URLError as exc:
        raise EnrichError(f"Could not reach FullEnrich: {exc.reason}") from None


def start_bulk(
    payload: dict[str, Any], api_key: str, opener: Opener = urllib.request.urlopen
) -> str:
    body = _request("POST", f"{BASE_URL}/v2/contact/enrich/bulk", api_key, payload, opener)
    try:
        return str(body["enrichment_id"])
    except KeyError:
        raise EnrichError("Response has no enrichment_id") from None


def fetch_results(
    enrichment_id: str, api_key: str, opener: Opener = urllib.request.urlopen
) -> dict[str, Any]:
    url = f"{BASE_URL}/v1/contact/enrich/bulk/{enrichment_id}"
    return _request("GET", url, api_key, None, opener)


# --------------------------------------------------------------------------- parsing


def records(body: Any) -> list[dict[str, Any]]:
    """Accept the shapes seen so far: {datas: [...]}, {data: [...]}, {results: [...]} (the
    FullEnrich MCP connector), a bare list, or a single record."""
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if not isinstance(body, dict):
        return []
    for key in ("datas", "data", "results"):
        value = body.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
        if isinstance(value, dict):
            return records(value)
    return [body] if "custom" in body or "input" in body else []


def _email_and_status(value: Any, status: Any = None) -> tuple[str | None, str]:
    """`most_probable_*email` is a string (API docs) or {email, status} (MCP connector)."""
    if isinstance(value, dict):
        return value.get("email"), str(value.get("status") or "").upper()
    return value, str(status or "").upper()


def _best_email(contact: dict[str, Any]) -> tuple[str | None, str | None]:
    for key in ("most_probable_email", "most_probable_work_email"):
        email, status = _email_and_status(
            contact.get(key), contact.get("most_probable_email_status")
        )
        if email and status != "INVALID":
            return str(email), (status.lower() if status else "unverified")
    for entry in (contact.get("emails") or []) + (contact.get("work_emails") or []):
        if isinstance(entry, dict) and entry.get("email"):
            if str(entry.get("status", "")).upper() in GOOD_EMAIL_STATUSES:
                return str(entry["email"]), str(entry["status"]).lower()
    return None, None


def normalize_linkedin(url: str | None) -> str:
    """'https://www.linkedin.com/in/Jane-Doe/?x=1' -> 'linkedin.com/in/jane-doe'."""
    if not url:
        return ""
    url = url.strip().lower().split("?", 1)[0].split("#", 1)[0].rstrip("/")
    for prefix in ("https://", "http://", "www."):
        url = url.removeprefix(prefix)
    return url


def linkedin_index(conn: sqlite3.Connection) -> dict[str, int]:
    """Normalized LinkedIn URL -> person id, to map back results that carry no `custom` field."""
    return {
        normalize_linkedin(row["linkedin_url"]): row["id"]
        for row in conn.execute("SELECT id, linkedin_url FROM people WHERE linkedin_url <> ''")
    }


def _person_id(record: dict[str, Any], by_linkedin: dict[str, int] | None) -> int | None:
    custom = record.get("custom") or {}
    try:
        return int(custom["person_id"])
    except (KeyError, TypeError, ValueError):
        pass
    if not by_linkedin:
        return None
    sent = record.get("input") or {}
    url = sent.get("professional_network_url") or sent.get("linkedin_url")
    return by_linkedin.get(normalize_linkedin(url))


def parse_outcomes(body: Any, by_linkedin: dict[str, int] | None = None) -> list[Outcome]:
    """One outcome per record that maps to a person; email is None when nothing was found.

    Records are mapped by `custom.person_id` (set by `build_payloads`), or else by the LinkedIn
    URL that was sent, when `by_linkedin` is given (see `linkedin_index`). Records that map to
    nobody are skipped.
    """
    outcomes: list[Outcome] = []
    for record in records(body):
        person_id = _person_id(record, by_linkedin)
        if person_id is None:
            continue
        contact = record.get("contact") or record.get("contact_info") or {}
        email, status = _best_email(contact)
        outcomes.append((person_id, email, status))
    return outcomes


def parse_results(
    body: Any, by_linkedin: dict[str, int] | None = None
) -> list[tuple[int, str, str]]:
    """Return (person_id, email, email_status) for every record that found an email."""
    return [
        (person_id, email, status or "unverified")
        for person_id, email, status in parse_outcomes(body, by_linkedin)
        if email
    ]


def is_finished(body: Any) -> bool:
    """Only a finished job proves "not found". A body with no status is a single final record."""
    status = str(body.get("status") or "").upper() if isinstance(body, dict) else ""
    return status in ("", "FINISHED")


# --------------------------------------------------------------------------- storing


def apply_outcomes(
    conn: sqlite3.Connection,
    outcomes: Iterable[Outcome],
    source: str = "fullenrich",
    checked_at: str | None = None,
    record_not_found: bool = True,
) -> dict[str, int]:
    """Store what the provider returned.

    - An email already stored is never overwritten.
    - An email on the suppression list is not stored.
    - "Not found" is remembered (so a rerun does not pay again) but never replaces a "found".
    """
    checked_at = checked_at or _today()
    sup_emails, sup_domains, _ = suppressed_sets(conn)
    stored = not_found = suppressed = 0
    for person_id, email, status in outcomes:
        if email and (
            email.lower() in sup_emails or email.rsplit("@", 1)[-1].lower() in sup_domains
        ):
            suppressed += 1
            continue
        if email:
            cursor = conn.execute(
                """
                UPDATE people
                SET email = ?, email_status = ?, email_source = ?, email_checked_at = ?
                WHERE id = ? AND (email IS NULL OR email = '')
                """,
                (email, status or "unverified", source, checked_at, person_id),
            )
            stored += cursor.rowcount
            attempt = "found"
        elif record_not_found:
            not_found += 1
            attempt = "not_found"
        else:
            continue
        conn.execute(
            """
            INSERT INTO enrichment_attempts (person_id, status, source, checked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (person_id) DO UPDATE SET
                status = excluded.status, source = excluded.source, checked_at = excluded.checked_at
            WHERE enrichment_attempts.status <> 'found'
            """,
            (person_id, attempt, source, checked_at),
        )
    conn.commit()
    return {"stored": stored, "not_found": not_found, "suppressed": suppressed}


def apply_results(
    conn: sqlite3.Connection, results: Iterable[tuple[int, str, str]], source: str = "fullenrich"
) -> int:
    """Store found emails only (kept for callers that do not track "not found")."""
    return apply_outcomes(conn, results, source, record_not_found=False)["stored"]


def record_run(conn: sqlite3.Connection, enrichment_id: str, contacts_sent: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO enrichment_runs (enrichment_id, contacts_sent) VALUES (?, ?)",
        (enrichment_id, contacts_sent),
    )
    conn.commit()
