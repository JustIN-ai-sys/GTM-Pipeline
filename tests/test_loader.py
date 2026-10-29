from conftest import SAMPLE
from gtm_pipeline import loader


def test_parse_employees_handles_ranges_and_noise():
    assert loader.parse_employees("51-200") == 125
    assert loader.parse_employees("~120") == 120
    assert loader.parse_employees("1,500") == 1500
    assert loader.parse_employees("") is None
    assert loader.parse_employees("unknown") is None


def test_normalize_domain():
    assert loader.normalize_domain("https://www.Acme.example/about") == "acme.example"
    assert loader.normalize_domain(None) is None


def test_parse_value_and_date():
    assert loader.parse_value("$12,500.50") == 12500.5
    assert loader.parse_value("n/a") == 0.0
    assert loader.parse_date("2026-10-01 14:03:00") == "2026-10-01"
    assert loader.parse_date("01/10/2026") is None


def test_sample_data_loads(sample_conn):
    orgs = sample_conn.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]
    people = sample_conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    deals = sample_conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    assert orgs == 36
    assert people > 0 and deals > 0


def test_loading_twice_does_not_duplicate(conn):
    args = (SAMPLE / "organizations.csv", SAMPLE / "people.csv", SAMPLE / "deals.csv")
    loader.load_all(conn, *args)
    before = [
        conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("organizations", "people", "deals")
    ]
    loader.load_all(conn, *args)
    after = [
        conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("organizations", "people", "deals")
    ]
    assert before == after


def test_reload_never_erases_an_enriched_email(conn, tmp_path):
    orgs = tmp_path / "o.csv"
    orgs.write_text("Name,Website domain\nAcme,acme.example\n")
    people = tmp_path / "p.csv"
    people.write_text("First name,Last name,Organization,Email\nAda,Lovelace,Acme,\n")
    deals = tmp_path / "d.csv"
    deals.write_text("Title,Organization,Value,Stage,Status\n")
    loader.load_all(conn, orgs, people, deals)
    conn.execute("UPDATE people SET email = 'ada@acme.example', email_status = 'deliverable'")
    conn.commit()
    loader.load_all(conn, orgs, people, deals)  # CRM export still has no email
    row = conn.execute("SELECT email, email_status FROM people").fetchone()
    assert (row["email"], row["email_status"]) == ("ada@acme.example", "deliverable")


def test_native_pipedrive_name_column_is_split(conn, tmp_path):
    orgs = tmp_path / "o.csv"
    orgs.write_text("Organization - Name\nAcme\n")
    people = tmp_path / "p.csv"
    people.write_text("Person - Name,Person - Organization\nGrace Brewster Hopper,Acme\n")
    deals = tmp_path / "d.csv"
    deals.write_text("Deal - Title,Deal - Organization\n")
    loader.load_all(conn, orgs, people, deals)
    row = conn.execute("SELECT first_name, last_name FROM people").fetchone()
    assert (row["first_name"], row["last_name"]) == ("Grace", "Brewster Hopper")


def _orgs_only(conn, tmp_path):
    orgs = tmp_path / "o.csv"
    orgs.write_text("Name,Website domain\nAcme,acme.example\n")
    loader.load_all(conn, orgs)  # people and deals are optional
    return orgs


def test_companies_can_be_loaded_without_people_or_deals(conn, tmp_path):
    _orgs_only(conn, tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 1


def test_signal_is_loaded_with_source_and_date(conn, tmp_path):
    _orgs_only(conn, tmp_path)
    sig = tmp_path / "s.csv"
    sig.write_text(
        "Organization,Signal,Status,Source,Observed at\n"
        "Acme,CRM_HubSpot,confirmed,fullenrich_technographics,2026-10-26\n"
    )
    counts = loader.load_signals(conn, sig)
    row = conn.execute("SELECT name, status, source, observed_at FROM signals").fetchone()
    assert counts == {"signals": 1, "signals_skipped": 0}
    assert tuple(row) == ("crm_hubspot", "confirmed", "fullenrich_technographics", "2026-10-26")


def test_not_found_never_downgrades_a_confirmed_signal(conn, tmp_path):
    _orgs_only(conn, tmp_path)
    for status, source in (("confirmed", "first"), ("not_found", "second")):
        sig = tmp_path / f"{status}.csv"
        sig.write_text(
            "Organization,Signal,Status,Source,Observed at\n"
            f"Acme,crm_hubspot,{status},{source},2026-10-26\n"
        )
        loader.load_signals(conn, sig)
    row = conn.execute("SELECT status, source FROM signals").fetchone()
    assert (row["status"], row["source"]) == ("confirmed", "first")


def test_signals_without_source_or_known_account_are_skipped(conn, tmp_path):
    _orgs_only(conn, tmp_path)
    sig = tmp_path / "s.csv"
    sig.write_text(
        "Organization,Signal,Status,Source,Observed at\n"
        "Acme,crm_hubspot,confirmed,,2026-10-26\n"
        "Ghost,crm_hubspot,confirmed,x,2026-10-26\n"
    )
    assert loader.load_signals(conn, sig) == {"signals": 0, "signals_skipped": 2}


def test_suppressions_are_normalised(conn, tmp_path):
    sup = tmp_path / "x.csv"
    sup.write_text(
        "Kind,Value,Reason\ndomain,https://www.Acme.example/,customer\norg,Acme Inc,x\nbad,y,z\n"
    )
    assert loader.load_suppressions(conn, sup) == 2
    values = {tuple(r) for r in conn.execute("SELECT kind, value FROM suppressions")}
    assert values == {("domain", "acme.example"), ("org", "acme inc")}


def test_crm_email_gets_a_source(conn, tmp_path):
    orgs = tmp_path / "o.csv"
    orgs.write_text("Name\nAcme\n")
    people = tmp_path / "p.csv"
    people.write_text("First name,Last name,Organization,Email\nAda,L,Acme,ada@acme.example\n")
    loader.load_all(conn, orgs, people)
    row = conn.execute("SELECT email_source FROM people").fetchone()
    assert row["email_source"] == "crm_import"


def test_clay_work_email_column_is_read(conn, tmp_path):
    orgs = tmp_path / "o.csv"
    orgs.write_text("Name\nAcme\n")
    people = tmp_path / "p.csv"
    people.write_text("First name,Last name,Organization,Work Email\nAda,L,Acme,ada@acme.example\n")
    loader.load_all(conn, orgs, people)
    assert conn.execute("SELECT email FROM people").fetchone()["email"] == "ada@acme.example"
