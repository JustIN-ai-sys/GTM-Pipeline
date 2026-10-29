-- GTM warehouse schema (Postgres / Supabase). Same model as sql/schema.sql, with real types.
-- Idempotent: safe to run on every push. `gtm warehouse push` refreshes every table from the
-- local SQLite database in one transaction, so ids are kept as they are there.

CREATE TABLE IF NOT EXISTS organizations (
    id        bigint PRIMARY KEY,
    name      text NOT NULL UNIQUE,
    domain    text,
    industry  text,
    employees integer,
    country   text
);

CREATE TABLE IF NOT EXISTS people (
    id               bigint PRIMARY KEY,
    org_id           bigint NOT NULL REFERENCES organizations (id),
    first_name       text NOT NULL,
    last_name        text NOT NULL DEFAULT '',
    title            text,
    email            text,
    linkedin_url     text,
    -- missing | unverified | deliverable | high_probability | catch_all | invalid
    email_status     text NOT NULL DEFAULT 'missing',
    email_source     text,
    email_checked_at date,
    UNIQUE (org_id, first_name, last_name)
);

CREATE TABLE IF NOT EXISTS stages (
    name     text PRIMARY KEY,
    position integer NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id                 bigint PRIMARY KEY,
    org_id             bigint NOT NULL REFERENCES organizations (id),
    title              text NOT NULL,
    value              double precision NOT NULL DEFAULT 0,
    stage              text,
    status             text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'won', 'lost')),
    last_activity_date date,
    created_date       date,
    UNIQUE (org_id, title)
);

CREATE TABLE IF NOT EXISTS icp_rules (
    industry text PRIMARY KEY,
    weight   integer NOT NULL CHECK (weight BETWEEN 0 AND 20)
);

-- A fact is never a bare boolean: status, source and date (see sql/schema.sql).
CREATE TABLE IF NOT EXISTS signals (
    id          bigint PRIMARY KEY,
    org_id      bigint NOT NULL REFERENCES organizations (id),
    name        text NOT NULL,
    status      text NOT NULL CHECK (status IN ('confirmed', 'not_found', 'unknown')),
    source      text NOT NULL,
    observed_at date,
    UNIQUE (org_id, name)
);

CREATE TABLE IF NOT EXISTS signal_rules (
    name   text PRIMARY KEY,
    points integer NOT NULL CHECK (points BETWEEN 0 AND 10)
);

CREATE TABLE IF NOT EXISTS suppressions (
    id     bigint PRIMARY KEY,
    kind   text NOT NULL CHECK (kind IN ('email', 'domain', 'org')),
    value  text NOT NULL,
    reason text,
    UNIQUE (kind, value)
);

CREATE TABLE IF NOT EXISTS enrichment_attempts (
    person_id  bigint PRIMARY KEY REFERENCES people (id),
    status     text NOT NULL CHECK (status IN ('found', 'not_found')),
    source     text NOT NULL,
    checked_at date NOT NULL
);

CREATE TABLE IF NOT EXISTS enrichment_runs (
    enrichment_id text PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    contacts_sent integer NOT NULL,
    status        text NOT NULL DEFAULT 'started',
    credits_spent double precision
);

CREATE INDEX IF NOT EXISTS idx_people_org ON people (org_id);
CREATE INDEX IF NOT EXISTS idx_deals_org ON deals (org_id);
CREATE INDEX IF NOT EXISTS idx_signals_org ON signals (org_id);

-- Supabase serves every table in `public` through its REST API. Row level security with no
-- policy means that API returns nothing; the direct Postgres connection (the owner) still works.
-- These tables hold contacts and emails: never add a policy that opens them to `anon`.
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE people ENABLE ROW LEVEL SECURITY;
ALTER TABLE stages ENABLE ROW LEVEL SECURITY;
ALTER TABLE deals ENABLE ROW LEVEL SECURITY;
ALTER TABLE icp_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE suppressions ENABLE ROW LEVEL SECURITY;
ALTER TABLE enrichment_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE enrichment_runs ENABLE ROW LEVEL SECURITY;
