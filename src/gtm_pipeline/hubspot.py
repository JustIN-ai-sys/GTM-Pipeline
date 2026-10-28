"""Push ready contacts and their account score to HubSpot (CRM API v3/v4).

Same contacts as `gtm export` (verified emails, suppressions re-checked, optional minimum
score), written to HubSpot instead of a CSV. Auth is a private app token
(`HUBSPOT_TOKEN`) with these scopes: crm.objects.companies.read/write,
crm.objects.contacts.read/write, crm.schemas.companies.write, crm.schemas.contacts.write.

Rules:
    - Dry run by default: `plan` needs no network. The CLI goes live only with --live --yes.
    - Companies are matched by domain (else by exact name), contacts by email. Nothing is
      duplicated, so a rerun is safe.
    - An existing record keeps its own fields (name, title, owner...): only the `gtm_*`
      properties are updated. New records get the standard fields too.
    - Errors carry the status code only, never the token.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

BASE_URL = "https://api.hubapi.com"
BATCH_SIZE = 100  # HubSpot batch endpoints take up to 100 inputs

Opener = Callable[..., Any]

# Properties this tool owns. Created on first live run if missing.
COMPANY_PROPERTIES = {
    "gtm_score": ("GTM account score", "number", "number"),
    "gtm_tier": ("GTM tier", "string", "text"),
    "gtm_next_action": ("GTM next action", "string", "text"),
    "gtm_signals": ("GTM confirmed signals", "string", "text"),
}
CONTACT_PROPERTIES = {
    "gtm_reason": ("GTM: why this contact", "string", "textarea"),
    "gtm_email_source": ("GTM: email source and check date", "string", "text"),
}
PROPERTY_GROUPS = {"companies": "companyinformation", "contacts": "contactinformation"}


class HubSpotError(RuntimeError):
    """Raised for API errors. Never includes the token."""


@dataclass
class Plan:
    """What a push would write, built from `export.ready_contacts` rows. No network needed."""

    companies: dict[str, dict[str, Any]] = field(default_factory=dict)  # key -> company
    contacts: list[dict[str, Any]] = field(default_factory=list)


def company_key(row: dict[str, Any]) -> str:
    return (row["domain"] or "").lower() or f"name:{row['company'].lower()}"


def plan(rows: Iterable[dict[str, Any]]) -> Plan:
    result = Plan()
    for row in rows:
        key = company_key(row)
        result.companies.setdefault(
            key,
            {
                "name": row["company"],
                "domain": row["domain"] or None,
                "gtm": {
                    "gtm_score": row["score"],
                    "gtm_tier": row["tier"],
                    "gtm_next_action": row["next_action"],
                    "gtm_signals": row["crm_used"],
                },
            },
        )
        result.contacts.append(
            {
                "email": row["email"].lower(),
                "company_key": key,
                "standard": {
                    "email": row["email"].lower(),
                    "firstname": row["first_name"],
                    "lastname": row["last_name"],
                    "jobtitle": row["title"],
                    "company": row["company"],
                },
                "gtm": {
                    "gtm_reason": row["note"],
                    "gtm_email_source": row["provenance"],
                },
            }
        )
    return result


def render_plan(p: Plan) -> str:
    """Counts only, no names or emails."""
    return "\n".join(
        [
            f"HubSpot push: {len(p.companies)} companies, {len(p.contacts)} contacts.",
            "Matched by domain (companies) and email (contacts); existing records keep their own "
            "fields, only gtm_* properties change.",
            f"Batches: {-(-len(p.contacts) // BATCH_SIZE)} for contacts.",
        ]
    )


# --------------------------------------------------------------------------- HTTP


class Client:
    def __init__(self, token: str, opener: Opener = urllib.request.urlopen) -> None:
        self._token = token
        self._opener = opener
        self.calls = 0

    def request(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - fixed https base URL
            BASE_URL + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        self.calls += 1
        try:
            with self._opener(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                return {}
            raise HubSpotError(f"HubSpot returned HTTP {exc.code} for {method} {path}") from None
        except urllib.error.URLError as exc:
            raise HubSpotError(f"Could not reach HubSpot: {exc.reason}") from None


def _chunks(items: list, size: int = BATCH_SIZE) -> Iterable[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def ensure_properties(client: Client) -> int:
    """Create the gtm_* properties that do not exist yet. Returns how many were created."""
    created = 0
    for obj, props in (("companies", COMPANY_PROPERTIES), ("contacts", CONTACT_PROPERTIES)):
        for name, (label, kind, field_type) in props.items():
            if client.request("GET", f"/crm/v3/properties/{obj}/{name}"):
                continue
            client.request(
                "POST",
                f"/crm/v3/properties/{obj}",
                {
                    "name": name,
                    "label": label,
                    "type": kind,
                    "fieldType": field_type,
                    "groupName": PROPERTY_GROUPS[obj],
                },
            )
            created += 1
    return created


def _find_companies(client: Client, companies: dict[str, dict]) -> dict[str, str]:
    """key -> HubSpot company id, for companies that already exist."""
    found: dict[str, str] = {}
    domains = [c["domain"].lower() for c in companies.values() if c["domain"]]
    for chunk in _chunks(domains, 50):
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "domain", "operator": "IN", "values": chunk}]}
            ],
            "properties": ["domain"],
            "limit": 100,
        }
        for result in client.request("POST", "/crm/v3/objects/companies/search", body).get(
            "results", []
        ):
            domain = (result.get("properties", {}).get("domain") or "").lower()
            if domain in chunk:
                found.setdefault(domain, str(result["id"]))
    for key, company in companies.items():
        if company["domain"]:
            continue
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "name", "operator": "EQ", "value": company["name"]}]}
            ],
            "properties": ["name"],
            "limit": 1,
        }
        results = client.request("POST", "/crm/v3/objects/companies/search", body).get("results")
        if results:
            found[key] = str(results[0]["id"])
    return found


def _find_contacts(client: Client, emails: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for chunk in _chunks(emails):
        body = {
            "idProperty": "email",
            "properties": ["email"],
            "inputs": [{"id": email} for email in chunk],
        }
        for result in client.request("POST", "/crm/v3/objects/contacts/batch/read", body).get(
            "results", []
        ):
            email = (result.get("properties", {}).get("email") or "").lower()
            if email:
                found[email] = str(result["id"])
    return found


def _write(client: Client, obj: str, existing: list[tuple[str, dict]], new: list[dict]) -> list:
    """Update existing records (gtm_* only) and create new ones. Returns created results."""
    for chunk in _chunks(existing):
        client.request(
            "POST",
            f"/crm/v3/objects/{obj}/batch/update",
            {"inputs": [{"id": rid, "properties": props} for rid, props in chunk]},
        )
    created: list = []
    for chunk in _chunks(new):
        response = client.request(
            "POST",
            f"/crm/v3/objects/{obj}/batch/create",
            {"inputs": [{"properties": props} for props in chunk]},
        )
        created.extend(response.get("results", []))
    return created


def push(p: Plan, client: Client) -> dict[str, int]:
    """Write the plan to HubSpot. Safe to rerun: matches before it creates."""
    props_created = ensure_properties(client)

    company_ids = _find_companies(client, p.companies)
    new_company_keys = [k for k in p.companies if k not in company_ids]
    created = _write(
        client,
        "companies",
        [(company_ids[k], _str(p.companies[k]["gtm"])) for k in p.companies if k in company_ids],
        [_company_props(p.companies[k]) for k in new_company_keys],
    )
    for result in created:  # batch results do not keep the input order
        props = result.get("properties", {})
        domain = (props.get("domain") or "").lower()
        key = domain if domain in p.companies else f"name:{(props.get('name') or '').lower()}"
        if key in p.companies:
            company_ids[key] = str(result["id"])

    contact_ids = _find_contacts(client, [c["email"] for c in p.contacts])
    new_contacts = [c for c in p.contacts if c["email"] not in contact_ids]
    created = _write(
        client,
        "contacts",
        [
            (contact_ids[c["email"]], _str(c["gtm"]))
            for c in p.contacts
            if c["email"] in contact_ids
        ],
        [_str({**c["standard"], **c["gtm"]}) for c in new_contacts],
    )
    by_email = {(r.get("properties", {}).get("email") or "").lower(): str(r["id"]) for r in created}
    contact_ids.update(by_email)

    associated = 0
    for contact in p.contacts:
        contact_id = contact_ids.get(contact["email"])
        company_id = company_ids.get(contact["company_key"])
        if contact_id and company_id:
            client.request(
                "PUT",
                f"/crm/v4/objects/contact/{contact_id}/associations/default/company/{company_id}",
            )
            associated += 1
    return {
        "properties_created": props_created,
        "companies_created": len(new_company_keys),
        "companies_updated": len(p.companies) - len(new_company_keys),
        "contacts_created": len(new_contacts),
        "contacts_updated": len(p.contacts) - len(new_contacts),
        "associations": associated,
    }


def _company_props(company: dict[str, Any]) -> dict[str, str]:
    props = {"name": company["name"], **company["gtm"]}
    if company["domain"]:
        props["domain"] = company["domain"]
    return _str(props)


def _str(props: dict[str, Any]) -> dict[str, str]:
    """HubSpot takes property values as strings; empty values are left out."""
    return {k: str(v) for k, v in props.items() if v not in (None, "")}
