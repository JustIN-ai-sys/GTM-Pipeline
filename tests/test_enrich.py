import io
import json
import urllib.error

import pytest

from conftest import AS_OF
from gtm_pipeline import enrich


def _people(conn, n, with_linkedin=True):
    conn.execute("INSERT INTO organizations (id, name, domain) VALUES (1, 'Acme', 'acme.example')")
    for i in range(n):
        conn.execute(
            "INSERT INTO people (org_id, first_name, last_name, linkedin_url) VALUES (1, ?, ?, ?)",
            (f"F{i}", f"L{i}", f"https://www.linkedin.com/in/p{i}" if with_linkedin else None),
        )
    conn.commit()


def test_only_contacts_without_email_are_candidates(conn):
    _people(conn, 3)
    conn.execute("UPDATE people SET email = 'x@acme.example' WHERE first_name = 'F0'")
    assert len(enrich.candidates(conn)) == 2


def test_contacts_that_cannot_be_looked_up_are_skipped(conn):
    conn.execute("INSERT INTO organizations (id, name) VALUES (1, 'NoDomain')")
    conn.execute("INSERT INTO people (org_id, first_name, last_name) VALUES (1, 'A', 'B')")
    assert enrich.candidates(conn) == []


def test_payloads_are_split_in_batches_of_100(conn):
    _people(conn, 250)
    payloads = enrich.build_payloads(enrich.candidates(conn), "run", "https://hook.example/x")
    assert [len(p["data"]) for p in payloads] == [100, 100, 50]
    assert all(p["webhook_url"] == "https://hook.example/x" for p in payloads)
    first = payloads[0]["data"][0]
    assert first["enrich_fields"] == ["contact.work_emails"]
    assert first["linkedin_url"].startswith("https://www.linkedin.com/in/")
    assert "person_id" in first["custom"]


def test_name_and_domain_are_used_when_there_is_no_linkedin(conn):
    _people(conn, 1, with_linkedin=False)
    item = enrich.build_payloads(enrich.candidates(conn), "run")[0]["data"][0]
    assert (item["first_name"], item["last_name"], item["domain"]) == ("F0", "L0", "acme.example")
    assert "linkedin_url" not in item


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_start_bulk_sends_bearer_token_and_returns_id():
    seen = {}

    def fake_open(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data)
        return _Response(json.dumps({"enrichment_id": "abc-123"}).encode())

    enrichment_id = enrich.start_bulk({"name": "n", "data": []}, "secret", opener=fake_open)
    assert enrichment_id == "abc-123"
    assert seen["url"] == "https://app.fullenrich.com/api/v2/contact/enrich/bulk"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"] == {"name": "n", "data": []}


def test_http_errors_never_leak_the_api_key():
    def fake_open(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 402, "Payment Required", {}, None)

    with pytest.raises(enrich.EnrichError) as exc:
        enrich.start_bulk({"name": "n", "data": []}, "super-secret-key", opener=fake_open)
    assert "402" in str(exc.value)
    assert "super-secret-key" not in str(exc.value)


V1_BODY = {
    "status": "FINISHED",
    "datas": [
        {
            "custom": {"person_id": "1"},
            "contact": {
                "most_probable_email": "a@acme.example",
                "most_probable_email_status": "DELIVERABLE",
            },
        },
        {
            "custom": {"person_id": "2"},
            "contact": {
                "emails": [
                    {"email": "b@acme.example", "status": "HIGH_PROBABILITY"},
                    {"email": "junk@acme.example", "status": "INVALID"},
                ]
            },
        },
        {"custom": {"person_id": "3"}, "contact": {"emails": []}},
        {"custom": {}, "contact": {"most_probable_email": "orphan@acme.example"}},
    ],
}


def test_parse_results_keeps_only_usable_emails():
    assert enrich.parse_results(V1_BODY) == [
        (1, "a@acme.example", "deliverable"),
        (2, "b@acme.example", "high_probability"),
    ]


def test_parse_results_accepts_wrapped_and_single_record_payloads():
    wrapped = {"event": "done", "data": V1_BODY["datas"]}
    single = V1_BODY["datas"][0]
    assert len(enrich.parse_results(wrapped)) == 2
    assert enrich.parse_results(single) == [(1, "a@acme.example", "deliverable")]
    assert enrich.parse_results("garbage") == []


def test_apply_results_does_not_overwrite_existing_email(conn):
    _people(conn, 2)
    ids = [r["id"] for r in conn.execute("SELECT id FROM people ORDER BY id")]
    conn.execute("UPDATE people SET email = 'keep@acme.example' WHERE id = ?", (ids[0],))
    updated = enrich.apply_results(
        conn,
        [(ids[0], "new@acme.example", "deliverable"), (ids[1], "b@acme.example", "deliverable")],
    )
    assert updated == 1
    emails = [r["email"] for r in conn.execute("SELECT email FROM people ORDER BY id")]
    assert emails == ["keep@acme.example", "b@acme.example"]


# ---- v2: filters, suppressions, cooldown, outcomes, report


def _org(conn, org_id, name, domain, industry, employees):
    conn.execute(
        "INSERT INTO organizations (id, name, domain, industry, employees) VALUES (?,?,?,?,?)",
        (org_id, name, domain, industry, employees),
    )
    conn.execute(
        "INSERT INTO people (org_id, first_name, last_name, linkedin_url) "
        "VALUES (?, 'A', 'B', 'x')",
        (org_id,),
    )
    conn.commit()


def _reasons(conn, **filters):
    return {r["company"]: why for r, why in enrich.classify(conn, enrich.Filters(**filters), AS_OF)}


def test_size_filters_remove_companies_before_any_spend(conn):
    _org(conn, 1, "Tiny", "t.example", "Software", 5)
    _org(conn, 2, "Right", "r.example", "Software", 120)
    _org(conn, 3, "Huge", "h.example", "Software", 90000)
    _org(conn, 4, "Unknown", "u.example", "Software", None)
    got = _reasons(conn, min_employees=20, max_employees=1000)
    assert got == {
        "Tiny": "too_small",
        "Right": "ok",
        "Huge": "too_large",
        "Unknown": "size_unknown",
    }


def test_require_icp_industry(conn):
    _org(conn, 1, "In", "i.example", "Fintech", 100)
    _org(conn, 2, "Out", "o.example", "Mining", 100)
    assert _reasons(conn, require_icp_industry=True) == {"In": "ok", "Out": "industry_not_in_icp"}
    assert _reasons(conn) == {"In": "ok", "Out": "ok"}


def test_suppressed_domain_and_org_are_never_selected(conn):
    _org(conn, 1, "Customer", "customer.example", "Software", 100)
    _org(conn, 2, "Blocked Inc", "other.example", "Software", 100)
    _org(conn, 3, "Fine", "fine.example", "Software", 100)
    conn.execute("INSERT INTO suppressions (kind, value) VALUES ('domain', 'customer.example')")
    conn.execute("INSERT INTO suppressions (kind, value) VALUES ('org', 'blocked inc')")
    conn.commit()
    assert _reasons(conn) == {"Customer": "suppressed", "Blocked Inc": "suppressed", "Fine": "ok"}


def test_not_found_is_skipped_during_cooldown_then_retried(conn):
    _org(conn, 1, "Acme", "a.example", "Software", 100)
    enrich.apply_outcomes(conn, [(1, None, None)], checked_at="2026-10-01")
    assert _reasons(conn, cooldown_days=90) == {"Acme": "cooldown"}
    assert _reasons(conn, cooldown_days=10) == {"Acme": "ok"}  # 25 days ago > 10-day cooldown


def test_outcomes_store_source_and_date_and_remember_not_found(conn):
    _org(conn, 1, "Acme", "a.example", "Software", 100)
    conn.execute("INSERT INTO people (org_id, first_name, last_name) VALUES (1, 'C', 'D')")
    conn.commit()
    ids = [r["id"] for r in conn.execute("SELECT id FROM people ORDER BY id")]
    result = enrich.apply_outcomes(
        conn,
        [(ids[0], "a@a.example", "deliverable"), (ids[1], None, None)],
        "fullenrich",
        "2026-10-26",
    )
    assert result == {"stored": 1, "not_found": 1, "suppressed": 0}
    row = conn.execute("SELECT email_source, email_checked_at FROM people WHERE id=?", (ids[0],))
    assert tuple(row.fetchone()) == ("fullenrich", "2026-10-26")
    status = {
        r["person_id"]: r["status"] for r in conn.execute("SELECT * FROM enrichment_attempts")
    }
    assert status == {ids[0]: "found", ids[1]: "not_found"}


def test_a_later_not_found_never_replaces_a_found(conn):
    _org(conn, 1, "Acme", "a.example", "Software", 100)
    enrich.apply_outcomes(conn, [(1, "a@a.example", "deliverable")], checked_at="2026-10-01")
    enrich.apply_outcomes(conn, [(1, None, None)], checked_at="2026-10-26")
    assert conn.execute("SELECT status FROM enrichment_attempts").fetchone()["status"] == "found"


def test_unfinished_job_does_not_record_not_found(conn):
    _org(conn, 1, "Acme", "a.example", "Software", 100)
    enrich.apply_outcomes(conn, [(1, None, None)], record_not_found=False)
    assert conn.execute("SELECT COUNT(*) FROM enrichment_attempts").fetchone()[0] == 0


def test_suppressed_email_is_not_stored(conn):
    _org(conn, 1, "Acme", "a.example", "Software", 100)
    conn.execute("INSERT INTO suppressions (kind, value) VALUES ('domain', 'a.example')")
    conn.commit()
    result = enrich.apply_outcomes(conn, [(1, "x@a.example", "deliverable")])
    assert result["suppressed"] == 1 and result["stored"] == 0
    assert conn.execute("SELECT email FROM people").fetchone()["email"] is None


def test_invalid_most_probable_email_is_ignored():
    body = {
        "datas": [
            {
                "custom": {"person_id": "1"},
                "contact": {
                    "most_probable_email": "bad@a.example",
                    "most_probable_email_status": "INVALID",
                },
            }
        ]
    }
    assert enrich.parse_outcomes(body) == [(1, None, None)]
    assert enrich.parse_results(body) == []


def test_not_found_record_is_an_outcome_with_no_email():
    assert enrich.parse_outcomes(V1_BODY)[2] == (3, None, None)


def test_report_states_cost_and_hides_personal_data(conn):
    _org(conn, 1, "Right", "r.example", "Software", 120)
    _org(conn, 2, "Tiny", "t.example", "Software", 3)
    report = enrich.render_report(conn, enrich.Filters(min_employees=20), AS_OF, limit=10)
    assert "To enrich now: 1" in report
    assert "Maximum cost: 1 credits" in report
    assert "Below the minimum company size | 1" in report
    assert "- Right" in report and "Tiny" not in report.split("Companies in this batch")[1]
    assert "linkedin" not in report.lower()
