-- The account score as a Postgres view: same rules and numbers as sql/score_accounts.sql.
-- A test runs both on the same data and compares every row.
--
-- The view scores as of today. For a reproducible score, set the date for the session first:
--     SET gtm.as_of = '2026-10-26';
--     SELECT name, score, tier, next_action FROM gtm.account_scores LIMIT 10;

CREATE OR REPLACE FUNCTION gtm_as_of() RETURNS date
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(NULLIF(current_setting('gtm.as_of', true), '')::date, current_date)
$$;

DROP VIEW IF EXISTS account_scores;  -- recreated each push, so a changed column list is fine

CREATE VIEW account_scores WITH (security_invoker = true) AS
WITH
max_pos AS (
    SELECT GREATEST(1, COALESCE(MAX(position), 1)) AS n FROM stages
),
open_deals AS (
    SELECT d.org_id, d.value, d.last_activity_date, COALESCE(s.position, 0) AS position
    FROM deals AS d
    LEFT JOIN stages AS s ON s.name = d.stage
    WHERE d.status = 'open'
),
deal_agg AS (
    SELECT
        org_id,
        COUNT(*) AS open_deals,
        SUM(value) AS open_value,
        MAX(position) AS best_position,
        MAX(last_activity_date) AS last_activity
    FROM open_deals
    GROUP BY org_id
),
people_agg AS (
    SELECT
        org_id,
        COUNT(*) AS contacts,
        SUM(CASE WHEN email IS NOT NULL AND email <> '' THEN 1 ELSE 0 END) AS contacts_with_email
    FROM people
    GROUP BY org_id
),
signal_agg AS (
    SELECT
        s.org_id,
        LEAST(10, SUM(r.points)) AS pts_signals,
        string_agg(s.name, ', ' ORDER BY s.name) AS signal_names
    FROM signals AS s
    JOIN signal_rules AS r ON r.name = s.name
    WHERE s.status = 'confirmed'
      AND s.observed_at IS NOT NULL
      AND gtm_as_of() - s.observed_at <= 365
    GROUP BY s.org_id
),
base AS (
    SELECT
        o.id AS org_id,
        o.name,
        o.industry,
        o.employees,
        COALESCE(da.open_deals, 0) AS open_deals,
        COALESCE(da.open_value, 0) AS open_value,
        COALESCE(da.best_position, 0) AS best_position,
        COALESCE(pa.contacts, 0) AS contacts,
        COALESCE(pa.contacts_with_email, 0) AS contacts_with_email,
        COALESCE(sa.pts_signals, 0) AS pts_signals,
        sa.signal_names,
        LEAST(20, GREATEST(0, COALESCE(r.weight, 0))) AS fit_industry,
        gtm_as_of() - da.last_activity AS days_since_activity
    FROM organizations AS o
    LEFT JOIN deal_agg AS da ON da.org_id = o.id
    LEFT JOIN people_agg AS pa ON pa.org_id = o.id
    LEFT JOIN signal_agg AS sa ON sa.org_id = o.id
    LEFT JOIN icp_rules AS r ON lower(r.industry) = lower(o.industry)
),
points AS (
    SELECT
        b.*,
        CASE
            WHEN b.employees BETWEEN 50 AND 500 THEN 10
            WHEN b.employees BETWEEN 11 AND 49 OR b.employees BETWEEN 501 AND 2000 THEN 6
            WHEN b.employees IS NOT NULL THEN 2
            ELSE 0
        END AS fit_size,
        ROUND(25.0 * b.best_position / (SELECT n FROM max_pos))::integer AS pts_stage,
        CASE
            WHEN b.open_value >= 50000 THEN 15
            WHEN b.open_value >= 10000 THEN 10
            WHEN b.open_value >= 1000 THEN 5
            ELSE 0
        END AS pts_value,
        CASE
            WHEN b.days_since_activity IS NULL THEN 0
            WHEN b.days_since_activity <= 7 THEN 10
            WHEN b.days_since_activity <= 14 THEN 7
            WHEN b.days_since_activity <= 30 THEN 3
            ELSE 0
        END AS pts_recency,
        CASE
            WHEN b.contacts_with_email >= 3 THEN 10
            WHEN b.contacts_with_email = 2 THEN 7
            WHEN b.contacts_with_email = 1 THEN 4
            ELSE 0
        END AS pts_coverage
    FROM base AS b
),
totals AS (
    SELECT
        p.*,
        p.fit_industry + p.fit_size + p.pts_stage + p.pts_value
            + p.pts_recency + p.pts_coverage + p.pts_signals AS score
    FROM points AS p
)
SELECT
    org_id,
    name,
    industry,
    employees,
    open_deals,
    open_value,
    contacts,
    contacts_with_email,
    days_since_activity,
    COALESCE(signal_names, '') AS signals,
    fit_industry + fit_size AS pts_fit,
    pts_stage,
    pts_value,
    pts_recency,
    pts_coverage,
    pts_signals,
    score,
    CASE WHEN score >= 70 THEN 'A' WHEN score >= 45 THEN 'B' ELSE 'C' END AS tier,
    CASE
        WHEN contacts = 0 THEN 'Add a contact'
        WHEN contacts_with_email = 0 THEN 'Enrich contact emails'
        WHEN open_deals = 0 THEN 'Create first deal'
        WHEN days_since_activity > 30 THEN 'Re-engage (stale 30+ days)'
        WHEN 1.0 * best_position / (SELECT n FROM max_pos) >= 0.6 THEN 'Push to next stage'
        ELSE 'Nurture'
    END AS next_action
FROM totals
ORDER BY score DESC, open_value DESC, name;
