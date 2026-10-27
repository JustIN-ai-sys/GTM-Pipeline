import csv

from conftest import AS_OF
from gtm_pipeline import export, loader
from gtm_pipeline.db import connect, init_db


def _setup(conn):
    conn.executemany(
        "INSERT INTO organizations (id, name, domain, industry, employees) VALUES (?,?,?,?,?)",
        [
            (1, "Strong", "strong.example", "Software", 120),
            (2, "Weak", "weak.example", "Mining", 5),
            (3, "Customer", "customer.example", "Software", 120),
        ],
    )
    conn.executemany(
        "INSERT INTO people (org_id, first_name, last_name, email, email_status, email_source, "
        "email_checked_at) VALUES (?,?,?,?,?,?,?)",
        [
            (1, "Ada", "Verified", "ada@strong.example", "deliverable", "fullenrich", AS_OF),
            (1, "Bo", "Typed", "bo@strong.example", "unverified", None, None),
            (1, "Cy", "Empty", None, "missing", None, None),
            (2, "Di", "Weak", "di@weak.example", "high_probability", "fullenrich", AS_OF),
            (3, "Ed", "Customer", "ed@customer.example", "deliverable", "fullenrich", AS_OF),
        ],
    )
    conn.execute(
        "INSERT INTO signals (org_id, name, status, source, observed_at) "
        "VALUES (1, 'crm_hubspot', 'confirmed', 'test', ?)",
        (AS_OF,),
    )
    conn.execute("INSERT INTO suppressions (kind, value) VALUES ('domain', 'customer.example')")
    conn.commit()


def _names(result):
    return [row["first_name"] for row in result.rows]


def test_only_verified_unsuppressed_emails_best_accounts_first(conn):
    _setup(conn)
    result = export.ready_contacts(conn, AS_OF)
    assert _names(result) == ["Ada", "Di"]
    assert result.rows[0]["score"] > result.rows[1]["score"]
    assert result.skipped == {"no_email": 1, "unverified": 1, "suppressed": 1}


def test_unverified_emails_only_on_request(conn):
    _setup(conn)
    assert "Bo" in _names(export.ready_contacts(conn, AS_OF, include_unverified=True))


def test_min_score_leaves_out_weak_accounts(conn):
    _setup(conn)
    result = export.ready_contacts(conn, AS_OF, min_score=30)
    assert _names(result) == ["Ada"]
    assert result.skipped["low_score"] == 1


def test_suppressed_email_is_never_exported_even_after_enrichment(conn):
    _setup(conn)
    conn.execute("INSERT INTO suppressions (kind, value) VALUES ('email', 'ada@strong.example')")
    conn.execute("INSERT INTO suppressions (kind, value) VALUES ('org', 'weak')")
    conn.commit()
    assert _names(export.ready_contacts(conn, AS_OF)) == []


def test_rows_carry_why_the_contact_was_chosen(conn):
    _setup(conn)
    ada = export.ready_contacts(conn, AS_OF).rows[0]
    assert ada["crm_used"] == "HubSpot"
    assert ada["next_action"]
    assert "uses HubSpot" in ada["note"]
    assert f"deliverable from fullenrich, checked {AS_OF}" in ada["note"]


def test_signal_labels():
    assert export.signal_label("crm_salesforce") == "Salesforce"
    assert export.signal_label("crm_close_io") == "Close Io"


def test_lemlist_columns(conn, tmp_path):
    _setup(conn)
    path = tmp_path / "lemlist.csv"
    export.write_csv(export.ready_contacts(conn, AS_OF).rows, path, "lemlist")
    with open(path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["email"] == "ada@strong.example"
    assert rows[0]["companyDomain"] == "strong.example"
    assert rows[0]["crmUsed"] == "HubSpot"


def test_pipedrive_export_loads_back_without_duplicates(conn, tmp_path):
    _setup(conn)
    path = tmp_path / "pipedrive.csv"
    export.write_csv(export.ready_contacts(conn, AS_OF).rows, path, "pipedrive")
    orgs = tmp_path / "orgs.csv"
    orgs.write_text("Name,Domain\nStrong,strong.example\nWeak,weak.example\n")
    other = connect(tmp_path / "other.db")
    init_db(other)
    for _ in range(2):  # loading twice must not duplicate
        loader.load_all(other, orgs, path)
    emails = sorted(r["email"] for r in other.execute("SELECT email FROM people"))
    assert emails == ["ada@strong.example", "di@weak.example"]


def test_summary_has_counts_but_no_personal_data(conn):
    _setup(conn)
    summary = export.render_summary(export.ready_contacts(conn, AS_OF), "lemlist", "x.csv")
    assert "Exported 2 contacts" in summary and "Nothing was sent" in summary
    assert "@" not in summary and "Ada" not in summary


def test_export_command_on_sample_data(sample_conn, tmp_path, capsys):
    from gtm_pipeline import cli

    db = sample_conn.execute("PRAGMA database_list").fetchone()["file"]
    out = tmp_path / "out.csv"
    args = ["--db", db, "export", "--out", str(out), "--include-unverified", "--as-of", AS_OF]
    assert cli.main(args) == 0
    assert "Nothing was sent" in capsys.readouterr().out
    with open(out, newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows and all(r["email"].endswith(".example") for r in rows)
    scores = [int(r["accountScore"]) for r in rows]
    assert scores == sorted(scores, reverse=True)
