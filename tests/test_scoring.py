from conftest import AS_OF
from gtm_pipeline import scoring
from gtm_pipeline.loader import ensure_stages


def _seed(conn):
    ensure_stages(conn, ["Lead", "Contacted", "Meeting", "Proposal", "Negotiation"])
    conn.executescript(
        """
        INSERT INTO organizations (id, name, domain, industry, employees) VALUES
            (1, 'Hot Co', 'hot.example', 'Software', 120),
            (2, 'Cold Co', 'cold.example', 'Education', 5),
            (3, 'Empty Co', NULL, NULL, NULL);
        INSERT INTO people (org_id, first_name, last_name, email, email_status) VALUES
            (1, 'A', 'One', 'a@hot.example', 'deliverable'),
            (1, 'B', 'Two', 'b@hot.example', 'deliverable'),
            (1, 'C', 'Three', 'c@hot.example', 'deliverable'),
            (2, 'D', 'Four', NULL, 'missing');
        INSERT INTO deals (org_id, title, value, stage, status, last_activity_date) VALUES
            (1, 'big', 80000, 'Negotiation', 'open', '2026-10-24'),
            (2, 'old', 500, 'Lead', 'open', '2026-07-01'),
            (2, 'closed', 90000, 'Negotiation', 'won', '2026-10-25');
        """
    )
    conn.commit()


def test_hot_account_outranks_cold_and_empty(conn):
    _seed(conn)
    rows = {r["name"]: r for r in scoring.score_accounts(conn, AS_OF)}
    assert rows["Hot Co"]["score"] > rows["Cold Co"]["score"] > rows["Empty Co"]["score"]
    assert rows["Hot Co"]["tier"] == "A"
    assert rows["Empty Co"]["tier"] == "C"


def test_score_breakdown_for_hot_account(conn):
    _seed(conn)
    hot = next(r for r in scoring.score_accounts(conn, AS_OF) if r["name"] == "Hot Co")
    assert hot["pts_fit"] == 30  # Software (20) + 120 employees (10)
    assert hot["pts_stage"] == 25  # Negotiation is the last stage
    assert hot["pts_value"] == 15  # 80k open
    assert hot["pts_recency"] == 10  # 2 days ago
    assert hot["pts_coverage"] == 10  # three emails
    assert hot["pts_signals"] == 0
    assert hot["score"] == 90


def test_won_deals_do_not_count_as_open_pipeline(conn):
    _seed(conn)
    cold = next(r for r in scoring.score_accounts(conn, AS_OF) if r["name"] == "Cold Co")
    assert cold["open_deals"] == 1
    assert cold["open_value"] == 500


def test_next_action_rules(conn):
    _seed(conn)
    actions = {r["name"]: r["next_action"] for r in scoring.score_accounts(conn, AS_OF)}
    assert actions["Empty Co"] == "Add a contact"
    assert actions["Cold Co"] == "Enrich contact emails"
    assert actions["Hot Co"] == "Push to next stage"


def test_every_score_is_between_0_and_100(sample_conn):
    rows = scoring.score_accounts(sample_conn, AS_OF)
    assert len(rows) == 36
    assert all(0 <= r["score"] <= 100 for r in rows)
    assert [r["score"] for r in rows] == sorted((r["score"] for r in rows), reverse=True)


def test_pipeline_health_counts_only_open_deals(sample_conn):
    total_open = sample_conn.execute("SELECT COUNT(*) FROM deals WHERE status='open'").fetchone()[0]
    rows = scoring.pipeline_health(sample_conn, AS_OF)
    assert sum(r["deals"] for r in rows) == total_open


def _add_signal(conn, status, observed_at, name="crm_hubspot"):
    conn.execute(
        "INSERT INTO signals (org_id, name, status, source, observed_at) VALUES (1, ?, ?, 't', ?)",
        (name, status, observed_at),
    )
    conn.commit()


def _hot(conn):
    return next(r for r in scoring.score_accounts(conn, AS_OF) if r["name"] == "Hot Co")


def test_confirmed_signal_adds_points_and_reaches_100(conn):
    _seed(conn)
    _add_signal(conn, "confirmed", "2026-10-20")
    hot = _hot(conn)
    assert hot["pts_signals"] == 4
    assert hot["signals"] == "crm_hubspot"
    assert hot["score"] == 94


def test_signals_are_capped_at_10_points(conn):
    _seed(conn)
    _add_signal(conn, "confirmed", "2026-10-20", "crm_hubspot")
    _add_signal(conn, "confirmed", "2026-10-20", "crm_salesforce")
    _add_signal(conn, "confirmed", "2026-10-20", "crm_pipedrive")  # 4 + 4 + 3 = 11
    assert _hot(conn)["pts_signals"] == 10
    assert _hot(conn)["score"] == 100


def test_not_found_and_stale_signals_score_nothing(conn):
    _seed(conn)
    _add_signal(conn, "not_found", "2026-10-20", "crm_hubspot")
    _add_signal(conn, "confirmed", "2024-01-01", "crm_salesforce")  # older than a year
    assert _hot(conn)["pts_signals"] == 0
