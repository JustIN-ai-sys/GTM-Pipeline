-- GTM account-scoring pipeline: schema (SQLite).
-- Idempotent: safe to run on every start.

CREATE TABLE IF NOT EXISTS organizations (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    domain    TEXT,
    industry  TEXT,
    employees INTEGER,
    country   TEXT
);

CREATE TABLE IF NOT EXISTS people (
    id               INTEGER PRIMARY KEY,
    org_id           INTEGER NOT NULL REFERENCES organizations (id),
    first_name       TEXT NOT NULL,
    last_name        TEXT NOT NULL DEFAULT '',
    title            TEXT,
    email            TEXT,
    linkedin_url     TEXT,
    -- missing | unverified | deliverable | high_probability | catch_all | invalid
    email_status     TEXT NOT NULL DEFAULT 'missing',
    -- where the email came from and when it was last checked (never just a bare value)
    email_source     TEXT,
    email_checked_at TEXT,
    UNIQUE (org_id, first_name, last_name)
);

CREATE TABLE IF NOT EXISTS stages (
    name     TEXT PRIMARY KEY,
    position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id                 INTEGER PRIMARY KEY,
    org_id             INTEGER NOT NULL REFERENCES organizations (id),
    title              TEXT NOT NULL,
    value              REAL NOT NULL DEFAULT 0,
    stage              TEXT,
    status             TEXT NOT NULL DEFAULT 'open'
                       CHECK (status IN ('open', 'won', 'lost')),
    last_activity_date TEXT,
    created_date       TEXT,
    UNIQUE (org_id, title)
);

-- Ideal-customer-profile weights (0-20 points). Edit to match your ICP.
CREATE TABLE IF NOT EXISTS icp_rules (
    industry TEXT PRIMARY KEY,
    weight   INTEGER NOT NULL CHECK (weight BETWEEN 0 AND 20)
);

INSERT OR IGNORE INTO icp_rules (industry, weight) VALUES
    ('Software', 20),
    ('Software Development', 20),
    ('Fintech', 18),
    ('Healthcare', 14),
    ('Logistics', 10),
    ('Energy', 8),
    ('Retail', 5),
    ('Education', 3);

-- Observed facts about an account (for example "uses HubSpot").
-- Never a bare true/false: "not found" is not "does not use it", so we keep
-- the status, where it came from and when we looked. A later "not_found" can
-- never overwrite an earlier "confirmed" (see loader.load_signals).
CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY,
    org_id      INTEGER NOT NULL REFERENCES organizations (id),
    name        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('confirmed', 'not_found', 'unknown')),
    source      TEXT NOT NULL,
    observed_at TEXT,
    UNIQUE (org_id, name)
);

-- Points a confirmed signal adds to the score (the total is capped at 10).
CREATE TABLE IF NOT EXISTS signal_rules (
    name   TEXT PRIMARY KEY,
    points INTEGER NOT NULL CHECK (points BETWEEN 0 AND 10)
);

INSERT OR IGNORE INTO signal_rules (name, points) VALUES
    ('crm_hubspot', 4),
    ('crm_salesforce', 4),
    ('crm_pipedrive', 3);

-- Who must never be contacted or enriched: customers, opt-outs, bounces, accounts
-- someone else is working. kind is email, domain or org.
CREATE TABLE IF NOT EXISTS suppressions (
    id     INTEGER PRIMARY KEY,
    kind   TEXT NOT NULL CHECK (kind IN ('email', 'domain', 'org')),
    value  TEXT NOT NULL,
    reason TEXT,
    UNIQUE (kind, value)
);

-- One row per contact we asked the provider about. "not_found" is remembered so a
-- rerun does not pay twice for the same lookup (see the cooldown in enrich.py).
CREATE TABLE IF NOT EXISTS enrichment_attempts (
    person_id  INTEGER PRIMARY KEY REFERENCES people (id),
    status     TEXT NOT NULL CHECK (status IN ('found', 'not_found')),
    source     TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

-- One row per enrichment batch sent to the provider (no personal data stored here).
CREATE TABLE IF NOT EXISTS enrichment_runs (
    enrichment_id TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    contacts_sent INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'started',
    credits_spent REAL
);

CREATE INDEX IF NOT EXISTS idx_people_org ON people (org_id);
CREATE INDEX IF NOT EXISTS idx_deals_org ON deals (org_id);
CREATE INDEX IF NOT EXISTS idx_deals_status ON deals (status);
CREATE INDEX IF NOT EXISTS idx_signals_org ON signals (org_id);
