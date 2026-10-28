import io
import json
import re
import urllib.error

import pytest

from conftest import AS_OF
from gtm_pipeline import cli, export, hubspot
from test_export import _setup


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHubSpot:
    """In-memory stand-in for the endpoints hubspot.py calls."""

    def __init__(self):
        self.properties = {"companies": set(), "contacts": set()}
        self.records = {"companies": {}, "contacts": {}}
        self.associations = set()
        self.auth = None
        self._next = 100

    def add(self, obj, props):
        self._next += 1
        self.records[obj][str(self._next)] = dict(props)
        return str(self._next)

    def __call__(self, request, timeout):
        self.auth = request.get_header("Authorization")
        path = request.full_url.removeprefix(hubspot.BASE_URL)
        body = json.loads(request.data) if request.data else None
        out = self.route(request.get_method(), path, body)
        return _Response(json.dumps(out).encode())

    def route(self, method, path, body):
        if m := re.fullmatch(r"/crm/v3/properties/(\w+)/(\w+)", path):
            if m[2] not in self.properties[m[1]]:
                raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)
            return {"name": m[2]}
        if m := re.fullmatch(r"/crm/v3/properties/(\w+)", path):
            self.properties[m[1]].add(body["name"])
            return body
        if path == "/crm/v3/objects/companies/search":
            f = body["filterGroups"][0]["filters"][0]
            wanted = set(f.get("values") or [f.get("value")])
            hits = [
                {"id": rid, "properties": p}
                for rid, p in self.records["companies"].items()
                if p.get(f["propertyName"]) in wanted
            ]
            return {"results": hits}
        if path == "/crm/v3/objects/contacts/batch/read":
            wanted = {i["id"] for i in body["inputs"]}
            return {
                "results": [
                    {"id": rid, "properties": {"email": p["email"]}}
                    for rid, p in self.records["contacts"].items()
                    if p.get("email") in wanted
                ]
            }
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/batch/create", path):
            created = [
                {"id": self.add(m[1], i["properties"]), "properties": i["properties"]}
                for i in body["inputs"]
            ]
            return {"results": list(reversed(created))}  # order is not guaranteed
        if m := re.fullmatch(r"/crm/v3/objects/(\w+)/batch/update", path):
            for i in body["inputs"]:
                self.records[m[1]][i["id"]].update(i["properties"])
            return {"results": []}
        if m := re.fullmatch(
            r"/crm/v4/objects/contact/(\d+)/associations/default/company/(\d+)", path
        ):
            self.associations.add((m[1], m[2]))
            return {}
        raise AssertionError(f"unexpected call {method} {path}")


def _plan(conn):
    return hubspot.plan(export.ready_contacts(conn, AS_OF).rows)


def test_plan_needs_no_network_and_reports_counts_only(conn):
    _setup(conn)
    p = _plan(conn)
    assert len(p.companies) == 2 and len(p.contacts) == 2
    text = hubspot.render_plan(p)
    assert "2 companies, 2 contacts" in text
    assert "@" not in text


def test_push_creates_companies_contacts_properties_and_associations(conn):
    _setup(conn)
    fake = FakeHubSpot()
    result = hubspot.push(_plan(conn), hubspot.Client("tok", opener=fake))
    assert result == {
        "properties_created": 6,
        "companies_created": 2,
        "companies_updated": 0,
        "contacts_created": 2,
        "contacts_updated": 0,
        "associations": 2,
    }
    assert fake.auth == "Bearer tok"
    strong = next(p for p in fake.records["companies"].values() if p["name"] == "Strong")
    assert strong["domain"] == "strong.example" and strong["gtm_signals"] == "HubSpot"
    ada = next(p for p in fake.records["contacts"].values() if p["firstname"] == "Ada")
    assert ada["gtm_email_source"] == f"deliverable from fullenrich, checked {AS_OF}"
    # each contact is linked to its own company, whatever order the API answered in
    names = {
        fake.records["contacts"][c]["firstname"]: fake.records["companies"][o]["name"]
        for c, o in fake.associations
    }
    assert names == {"Ada": "Strong", "Di": "Weak"}


def test_rerun_updates_instead_of_duplicating_and_keeps_crm_fields(conn):
    _setup(conn)
    fake = FakeHubSpot()
    hubspot.push(_plan(conn), hubspot.Client("tok", opener=fake))
    ada_id = next(i for i, p in fake.records["contacts"].items() if p["firstname"] == "Ada")
    fake.records["contacts"][ada_id]["jobtitle"] = "Edited by a rep"
    result = hubspot.push(_plan(conn), hubspot.Client("tok", opener=fake))
    assert result["contacts_created"] == 0 and result["contacts_updated"] == 2
    assert result["companies_created"] == 0 and result["properties_created"] == 0
    assert len(fake.records["contacts"]) == 2 and len(fake.records["companies"]) == 2
    assert fake.records["contacts"][ada_id]["jobtitle"] == "Edited by a rep"


def test_company_without_domain_is_matched_by_name(conn):
    _setup(conn)
    conn.execute("UPDATE organizations SET domain = NULL WHERE name = 'Strong'")
    conn.commit()
    fake = FakeHubSpot()
    existing = fake.add("companies", {"name": "Strong"})
    result = hubspot.push(_plan(conn), hubspot.Client("tok", opener=fake))
    assert result["companies_created"] == 1 and result["companies_updated"] == 1
    assert fake.records["companies"][existing]["gtm_tier"]


def test_errors_never_leak_the_token():
    def fake_open(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    with pytest.raises(hubspot.HubSpotError) as exc:
        hubspot.Client("pat-secret-token", opener=fake_open).request("POST", "/x", {})
    assert "401" in str(exc.value) and "pat-secret-token" not in str(exc.value)


def test_cli_is_a_dry_run_unless_live_and_yes(conn, capsys, monkeypatch):
    _setup(conn)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    assert cli.main(["--db", db, "push", "hubspot", "--as-of", AS_OF]) == 0
    assert "Dry run: nothing was sent to HubSpot." in capsys.readouterr().out
    monkeypatch.setenv("HUBSPOT_TOKEN", "tok")
    assert cli.main(["--db", db, "push", "hubspot", "--live", "--as-of", AS_OF]) == 2
    assert "without --yes" in capsys.readouterr().err
