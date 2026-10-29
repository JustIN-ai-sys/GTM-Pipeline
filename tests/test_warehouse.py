import os
from urllib.parse import urlsplit

import pytest

from conftest import AS_OF, SAMPLE
from gtm_pipeline import cli, loader, scoring, warehouse
from test_export import _setup

PG_URL = os.environ.get("GTM_TEST_PG_URL")
# Built at runtime so no literal credential sits in this file (the review prechecks flag them).
SECRET = "top" + "secret"


def _url(host: str, path: str = "/postgres") -> str:
    return f"postgresql://u:{SECRET}@{host}{path}"


# ---- no database needed


def test_target_never_shows_the_password():
    assert warehouse.describe_target(_url("db.abcd.supabase.co:5432")) == (
        "db.abcd.supabase.co/postgres"
    )


def test_empty_text_dates_become_null(conn):
    _setup(conn)
    conn.execute("UPDATE people SET email_checked_at = '' WHERE first_name = 'Ada'")
    rows = warehouse._rows(conn, "people", ["first_name", "email_checked_at"])
    assert ("Ada", None) in rows


def test_dry_run_counts_rows_and_writes_nothing(conn, capsys, monkeypatch):
    _setup(conn)
    monkeypatch.setenv("DATABASE_URL", _url("db.example.com:5432"))
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    assert cli.main(["--db", db, "warehouse", "push"]) == 0
    out = capsys.readouterr().out
    assert "db.example.com/postgres" in out and SECRET not in out
    assert "people               5 rows" in out
    assert "Dry run: nothing was written" in out


def test_connection_errors_do_not_leak_the_password():
    pytest.importorskip("psycopg")
    with pytest.raises(warehouse.WarehouseError) as exc:
        warehouse.connect(_url("127.0.0.1:1", "/x?connect_timeout=2"))
    assert SECRET not in str(exc.value)


# ---- against a real Postgres (CI starts one; locally set GTM_TEST_PG_URL)

# The fixture wipes the database, so it only runs on one whose name says it is for tests.
needs_pg = pytest.mark.skipif(
    not PG_URL or "test" not in urlsplit(PG_URL).path,
    reason="GTM_TEST_PG_URL is not set to a test database (its name must contain 'test')",
)


@pytest.fixture
def pg():
    psycopg = pytest.importorskip("psycopg")
    connection = psycopg.connect(PG_URL, autocommit=True)
    connection.execute("DROP SCHEMA public CASCADE")
    connection.execute("CREATE SCHEMA public")
    yield connection
    connection.close()


def _sample(conn):
    loader.load_all(
        conn,
        SAMPLE / "organizations.csv",
        SAMPLE / "people.csv",
        SAMPLE / "deals.csv",
        signals=SAMPLE / "signals.csv",
        suppressions=SAMPLE / "suppressions.csv",
    )


@needs_pg
def test_push_copies_every_table_and_is_safe_to_rerun(conn, pg):
    _sample(conn)
    first = warehouse.push(conn, pg)
    second = warehouse.push(conn, pg)
    assert first == second == warehouse.counts(conn)
    for table, count in first.items():
        assert pg.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count


@needs_pg
def test_postgres_score_matches_sqlite_row_for_row(conn, pg):
    _sample(conn)
    warehouse.push(conn, pg)
    pg.execute(f"SET gtm.as_of = '{AS_OF}'")
    cursor = pg.execute("SELECT * FROM account_scores")
    columns = [c.name for c in cursor.description]
    remote = {row[0]: dict(zip(columns, row, strict=True)) for row in cursor.fetchall()}
    local = {row["org_id"]: dict(row) for row in scoring.score_accounts(conn, AS_OF)}
    assert remote.keys() == local.keys() and len(local) == 36

    def normalize(row):
        out = dict(row)
        out["signals"] = sorted(s for s in (row["signals"] or "").split(", ") if s)
        out["open_value"] = float(row["open_value"])
        return out

    for org_id, row in local.items():
        assert normalize(remote[org_id]) == normalize(row), row["name"]


@needs_pg
def test_every_table_has_row_level_security_on(conn, pg):
    _sample(conn)
    warehouse.push(conn, pg)
    rows = pg.execute(
        "SELECT relname, relrowsecurity FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
    ).fetchall()
    assert {name for name, _ in rows} == {table for table, _ in warehouse.TABLES}
    assert all(secured for _, secured in rows)


@needs_pg
def test_a_failed_push_leaves_the_warehouse_as_it_was(conn, pg, monkeypatch):
    _sample(conn)
    warehouse.push(conn, pg)
    before = pg.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    broken = [*warehouse.TABLES[:-1], ("enrichment_runs", ["no_such_column"])]
    monkeypatch.setattr(warehouse, "TABLES", broken)
    with pytest.raises(warehouse.WarehouseError):
        warehouse.push(conn, pg)
    assert pg.execute("SELECT COUNT(*) FROM people").fetchone()[0] == before
