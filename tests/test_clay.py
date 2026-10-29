import csv

from conftest import AS_OF
from gtm_pipeline import clay, cli, scoring


def _orgs(conn):
    conn.executemany(
        "INSERT INTO organizations (id, name, domain, industry, employees) VALUES (?,?,?,?,?)",
        [
            (1, "Acme", "acme.example", "Software", 120),
            (2, "Beta Corp", None, "Software", 120),
            (3, "Gamma", "gamma.example", "Software", 120),
        ],
    )
    conn.commit()


def _csv(tmp_path, rows, header=("Company Name", "Company Domain", "Tech Stack")):
    path = tmp_path / "clay.csv"
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def _signals(conn):
    return {
        (r["org_id"], r["name"]): (r["status"], r["source"], r["observed_at"])
        for r in conn.execute("SELECT * FROM signals")
    }


def test_tech_stack_cell_becomes_dated_sourced_signals(conn, tmp_path):
    _orgs(conn)
    path = _csv(
        tmp_path,
        [
            ("Acme", "https://www.acme.example/", '["Google Analytics", "HubSpot", "Stripe"]'),
            ("Beta Corp", "", "Salesforce Sales Cloud, Segment"),
        ],
    )
    result = clay.import_tech_stack(conn, path, as_of=AS_OF)
    assert result["matched"] == 2 and result["confirmed"] == 2 and result["not_found"] == 4
    got = _signals(conn)
    assert got[(1, "crm_hubspot")] == ("confirmed", "clay_tech_stack", AS_OF)
    assert got[(1, "crm_salesforce")][0] == "not_found"
    assert got[(2, "crm_salesforce")][0] == "confirmed"  # matched by name: no domain stored


def test_empty_cells_and_unknown_companies_store_nothing(conn, tmp_path):
    _orgs(conn)
    path = _csv(tmp_path, [("Gamma", "gamma.example", ""), ("Nobody", "nobody.example", "HubSpot")])
    result = clay.import_tech_stack(conn, path, as_of=AS_OF)
    assert result["no_data"] == 1 and result["unmatched"] == 1
    assert _signals(conn) == {}
    assert conn.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 3


def test_a_later_not_found_never_replaces_a_confirmed(conn, tmp_path):
    _orgs(conn)
    clay.import_tech_stack(conn, _csv(tmp_path, [("Acme", "acme.example", "HubSpot")]), as_of=AS_OF)
    later = _csv(tmp_path, [("Acme", "acme.example", "Intercom")])
    clay.import_tech_stack(conn, later, as_of="2026-11-30")
    assert _signals(conn)[(1, "crm_hubspot")] == ("confirmed", "clay_tech_stack", AS_OF)


def test_signals_feed_the_score(conn, tmp_path):
    _orgs(conn)
    before = {r["org_id"]: r["pts_signals"] for r in scoring.score_accounts(conn, AS_OF)}
    path = _csv(tmp_path, [("Acme", "acme.example", "HubSpot, Pipedrive")])
    clay.import_tech_stack(conn, path, as_of=AS_OF)
    after = {r["org_id"]: r["pts_signals"] for r in scoring.score_accounts(conn, AS_OF)}
    assert before[1] == 0 and after[1] == 7  # HubSpot 4 + Pipedrive 3


def test_import_clay_command(conn, tmp_path, capsys):
    _orgs(conn)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    path = _csv(
        tmp_path,
        [("Acme", "acme.example", "Salesforce")],
        header=("Company Name", "Company Domain", "Website Technology Stack"),
    )
    args = ["--db", db, "import-clay", str(path), "--column", "Website Technology Stack"]
    assert cli.main(args + ["--as-of", AS_OF]) == 0
    assert "1 confirmed, 2 not found" in capsys.readouterr().out
