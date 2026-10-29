# gtm-pipeline

Turn a CRM export into a ranked list of accounts to work, and fill in the missing contact emails.

I built it to automate work I was doing by hand in a spreadsheet: deciding which accounts deserve
the next hour, and finding the emails of the people to contact there. Each step is now a small,
tested command, and every pull request gets an automated review by an agent (see
[Review agent](#review-agent) for what it checks and its limits).

```
CRM export or company list (CSV) -> SQLite -> SQL account score -> ranked list + next action
                                       ^                                   |
                                       | emails found                      v
                  FullEnrich  <-  contacts with no email     gtm export / gtm push hubspot
                  (API, webhook or saved JSON result)                      |
                                                                           v
                              lemlist or Pipedrive CSV, or HubSpot (verified, not suppressed)

pull request -> prechecks + review agent (rubric, evals) -> comment + pass / fail check
```

## Results so far

On real data (31 San Francisco software companies found with FullEnrich company search, kept out
of this repository):

- **Accounts**: ranked on fit and confirmed CRM signals (HubSpot 20, Pipedrive 10, Salesforce 5).
- **People**: 13 decision makers found at 9 of the top 10 accounts with the free people search
  (founders, chiefs of staff, RevOps, sales).
- **Pilot enrichment, 10 contacts: 8 work emails found, all `DELIVERABLE`, 8 credits spent**
  (nothing charged for the 2 not found, which are now in a 90-day cooldown). I sent the 10
  contacts through the FullEnrich connector in Claude Code, not through `gtm enrich --live` (I
  had no API key yet), then stored the saved result with `gtm import-results`.
- **Hand-off**: 8 contacts at 6 accounts written to a CSV for a sequencer, best accounts first.
  Not imported or sent yet.

What I learned from the pilot: the result format of the FullEnrich connector differs from the
one in the API docs (no `custom` field, `work_emails` instead of `emails`). The parser now reads
both and maps results back by LinkedIn URL; a test covers it.

## What it does

| Command | What happens |
|---|---|
| `gtm load` | Loads organizations (required) and, optionally, people, deals, signals and suppressions from CSV. Safe to re-run: it upserts and never erases an email or a confirmed signal. |
| `gtm score` | Scores every account from 0 to 100 in plain SQL and suggests a next action. |
| `gtm health` | Open pipeline by stage: deals, value, average days since activity, stale deals. |
| `gtm enrich` | Filters who is worth paying for, prints a report with the maximum cost, then (only with `--live --yes`) sends batches of 100 to FullEnrich. **Dry run by default.** |
| `gtm serve` | Small webhook receiver that stores the emails FullEnrich sends back. |
| `gtm fetch <id>` | Pulls the results of one enrichment by id, as a fallback to the webhook. |
| `gtm import-results <file>` | Stores a saved result (JSON from the app, the API or the MCP connector). |
| `gtm export` | Writes the contacts ready to work as a lemlist or Pipedrive CSV. **Sends nothing.** |
| `gtm push hubspot` | Writes the same contacts, their companies and account scores to HubSpot. **Dry run by default.** |
| `gtm warehouse push` | Copies every table to Postgres or Supabase and creates the `account_scores` view. **Dry run by default.** |

## Quick start (2 minutes)

Python 3.11+. No runtime dependencies (standard library only). No API key needed: everything
below runs on invented sample data and spends nothing.

```bash
git clone https://github.com/JustIN-ai-sys/GTM-Pipeline.git && cd GTM-Pipeline
pip install -e ".[dev]"
bash scripts/demo.sh      # load -> score -> enrichment dry run -> export -> HubSpot dry run
pytest -q                 # 93 tests (4 need a Postgres, see Warehouse)
```

`scripts/demo.sh` writes into `out/` (git-ignored). Each command is printed before it runs, so
you can rerun any step by hand.

What it prints on the sample data (invented companies):

```text
$ gtm score --as-of 2026-10-26 --top 6
name          score  tier  open_deals  open_value  contacts_with_email  next_action
Blue Labs     77     A     2           80000.0     4                    Push to next stage
Iron Health   73     A     1           65000.0     2                    Push to next stage
Iron Grid     67     B     1           15000.0     3                    Nurture
Quick Retail  66     B     2           74000.0     2                    Push to next stage
Open Ledger   63     B     2           95000.0     0                    Enrich contact emails
Maple Pay     63     B     2           65800.0     1                    Push to next stage

$ gtm enrich --limit 10 --min-employees 20 --as-of 2026-10-26
| Outcome | People |
|---|---|
| Eligible | 19 |
| Already has an email | 34 |
| On the suppression list (domain or organization) | 2 |
| Below the minimum company size | 10 |

**To enrich now: 10** (19 eligible, limit 10), in 1 batch(es).
Maximum cost: 10 credits (one per work email found; nothing is charged when no email is found).
Dry run: nothing was sent and no credit was spent.

$ gtm export --out out/lemlist.csv --min-score 45 --include-unverified --as-of 2026-10-26
Exported 23 contacts for lemlist to out/lemlist.csv
Accounts: 11, best score 77
Skipped - No email: 31
Skipped - On the suppression list (email, domain or organization): 2
Skipped - Account below --min-score: 9
Nothing was sent. Read the file, then import it yourself.
```

## The score

All the logic is in [`sql/score_accounts.sql`](sql/score_accounts.sql), so it can be read and
changed without touching Python. Maximum 100 points:

| Part | Points | Rule |
|---|---|---|
| Fit | 30 | Industry weight from the `icp_rules` table (up to 20) plus company size band (up to 10) |
| Stage | 25 | Furthest stage reached by an open deal, relative to the length of the pipeline |
| Value | 15 | Total open deal value, in bands |
| Recency | 10 | Days since the last activity on an open deal |
| Coverage | 10 | Number of contacts that have an email |
| Signals | 10 | Confirmed signals seen in the last 365 days, from the `signal_rules` table (HubSpot 4, Salesforce 4, Pipedrive 3, capped at 10) |

Tiers: **A** from 70, **B** from 45, otherwise **C**. Won and lost deals are ignored.
`next_action` is the first matching rule: add a contact, enrich emails, create a first deal,
re-engage if stale for 30+ days, push to the next stage, otherwise nurture.

A list of companies with no deals can score at most 50 (fit, signals, coverage), so every tier is
C. Read such a list by score, not by tier.

A signal is never a bare yes/no. It is a status (`confirmed`, `not_found`, `unknown`) with a
source and a date, and a later `not_found` never replaces an earlier `confirmed`: not finding a
technology is not proof that a company stopped using it.

Edit the weights to match your own ideal customer profile:

```sql
UPDATE icp_rules SET weight = 20 WHERE industry = 'Fintech';
```

## Using your own Pipedrive data

Export organizations, people and deals from Pipedrive as CSV. The loader reads the native headers
(`Organization - Name`, `Person - Email - Work`, `Deal - Stage`, ...) and also plain ones
(`Name`, `Email`, `Stage`). See the alias tables at the top of
[`loader.py`](src/gtm_pipeline/loader.py) if your export uses custom field names.
Pass your stage names in order with `--stages "Lead,Qualified,Demo,Proposal,Won"`.

Real exports contain personal data. The `.gitignore` excludes `*.db` and `out/`; keep exports
out of the repository.

## Enrichment

Paying for an email is the only step with a cost, so it goes through three gates before any
request is built:

1. **Filter** (`enrich.classify`). A contact is dropped if it already has an email, has nothing
   to look up (no LinkedIn URL, or no name plus company domain), is on the suppression list
   (domain or organization), is outside the size range (`--min-employees`, `--max-employees`),
   outside the ICP industries (`--require-icp-industry`), or was looked up and not found in the
   last `--cooldown-days` (default 90).
2. **Report.** `gtm enrich` prints, and with `--report FILE` saves, a markdown summary: how many
   people were removed and why, how many remain, the number of batches and the maximum cost. It
   lists companies only, never names or emails.
3. **Confirm.** Nothing is sent without `--live --yes` and `FULLENRICH_API_KEY`.

Then it calls `POST https://app.fullenrich.com/api/v2/contact/enrich/bulk` in batches of 100.
Each contact carries its own `person_id` in the `custom` field, so results map back to the right
row. Results that come back without it (the FullEnrich MCP connector does this) are mapped by
normalized LinkedIn URL; `gtm import-results <file>` stores such a file with
the same rules as `fetch`.

- **Start with `--limit 10`.** Read the hit rate, then rerun on the rest. Reruns are cheap: found
  contacts have an email, and "not found" is remembered in `enrichment_attempts`.
- **Every email keeps its source and date** (`email_source`, `email_checked_at`).
- **Webhook or fetch.** `gtm serve` is built to store what FullEnrich posts back (tested on the
  payload shapes in the docs, not yet on a real callback). Set `WEBHOOK_TOKEN`; requests without
  the right `?token=` get a 401. `gtm fetch <id>` pulls the same results on demand.
- **Never overwrites** an email that is already stored, and never stores an email on the
  suppression list.
- **Errors do not leak the key.** HTTP errors raise a message with the status code only.

## Warehouse (Postgres, Supabase)

SQLite is the working copy on my laptop. The warehouse is where the team and the dashboards
read: `gtm warehouse push` copies every table to Postgres (Supabase is Postgres) and creates
the score as a view, so anyone can query the ranking in SQL without running this tool.

```bash
pip install -e ".[warehouse]"
export DATABASE_URL="postgresql://postgres:<password>@db.<project>.supabase.co:5432/postgres"
gtm warehouse push            # dry run: row counts and the target host (never the password)
gtm warehouse push --yes      # replaces the warehouse tables in one transaction
```

- **Typed model** ([`sql/postgres/schema.sql`](sql/postgres/schema.sql)): the same tables as
  SQLite with real types (dates, numbers), keys, foreign keys and checks.
- **The score as a view** ([`sql/postgres/account_scores.sql`](sql/postgres/account_scores.sql)):
  `SELECT * FROM account_scores` gives today's ranking; `SET gtm.as_of = '2026-10-26'` replays
  any date. A test runs both versions on the 36 sample companies and checks that every row,
  score and next action is identical.
- **All or nothing**: one transaction; if anything fails, the warehouse keeps its previous
  data (tested).
- **Locked down for Supabase**: row level security is on for every table with no policy, so
  Supabase's public API returns nothing; only the direct database connection reads the data.
  The tables hold contacts and emails.
- It is a full refresh, not an incremental sync: fine for thousands of rows, not millions.

## Hand-off to outreach

`gtm export --format lemlist|pipedrive --out FILE` writes the contacts that are ready to work,
best accounts first. It never calls a sending tool or a CRM API: a person reads the file and
imports it.

- **Verified emails only** by default (`deliverable`, `high_probability`). Emails typed into the
  CRM and never checked need `--include-unverified`.
- **Suppressions are checked again** at export time (email, domain, organization): the list may
  have grown since the enrichment.
- `--min-score N` leaves out weak accounts.
- **Each row says why the contact was chosen**: account score, confirmed CRM signals and next
  action. The lemlist file has them as extra columns (`accountScore`, `crmUsed`,
  `nextAction`), meant to become custom variables on import (not tried yet); in Pipedrive they
  go in the note, with the email's source and check date.
- The Pipedrive format uses the same headers the loader reads, so an export loads back with
  `gtm load --people` without duplicates (a test covers it).

## HubSpot

`gtm push hubspot` sends the same contacts as `gtm export` to HubSpot through the CRM API, with
their companies and the account score.

- **Dry run by default**: it prints how many companies and contacts it would write. It calls the
  API only with `--live --yes` and `HUBSPOT_TOKEN` set.
- **No duplicates**: companies are matched by domain (else exact name), contacts by email. A
  rerun updates instead of creating. So far this is tested against an in-memory fake of the API
  only; the first run on a real HubSpot portal is still to do.
- **The CRM keeps its own data**: on an existing record only the `gtm_*` properties change
  (`gtm_score`, `gtm_tier`, `gtm_next_action`, `gtm_signals` on companies; `gtm_reason`,
  `gtm_email_source` on contacts), never a name, title or owner a rep may have edited. The
  properties are created on the first live run.
- Each contact is associated with its company.

Setup: in HubSpot, Settings > Integrations > Private Apps > Create a private app, with the scopes
`crm.objects.companies.read`, `crm.objects.companies.write`, `crm.objects.contacts.read`,
`crm.objects.contacts.write`, `crm.schemas.companies.write`, `crm.schemas.contacts.write`. Copy
the token into `HUBSPOT_TOKEN` (never into a file that is committed), then:

```bash
gtm push hubspot --min-score 40                # dry run: counts only
gtm push hubspot --min-score 40 --live --yes   # writes to HubSpot
```

## Review agent

Every pull request is reviewed by an agent
([`.github/workflows/review.yml`](.github/workflows/review.yml)), the way a senior GTM engineer
would review it: does the change protect money, people's data, the CRM, and the truth of what
the README claims?

1. **Prechecks** ([`tools/gtm_review/precheck.py`](tools/gtm_review/precheck.py)), no model:
   regexes on the added lines for API keys, real email addresses (anything not on an `.example`
   domain) and private files (`data/private/`, `*.db`, `.env`). A hit makes the verdict `block`.
2. **Model review** ([`tools/gtm_review/review.py`](tools/gtm_review/review.py)): Claude reads the
   [rubric](tools/gtm_review/rubric.md), the repo's own rules (`AGENTS.md`) and the diff, and
   returns a verdict (`approve`, `needs_human`, `block`) with findings tied to a rule (R1 spend
   gate ... R9 no key leak, G1 untested behaviour, G2 docs overclaim, G3 scope creep, G4 bug),
   as JSON checked against a schema. A refusal, a cut-off answer or a diff too large to read whole
   becomes `needs_human`, never a pass.
3. **Gate**: the worst finding sets the verdict. The agent posts it as a comment on the pull
   request and fails the check on `block`. The check stops a merge only once it is made a
   required status check in the branch protection rules of `main`.

**Evals** ([`tools/gtm_review/evals/`](tools/gtm_review/evals/)): 15 labelled diffs against this
repo: 9 that must be blocked (live by default, a CRM write without `--yes`, a committed contact
list, the API key in an error message, an overwritten email, a `not_found` replacing a
`confirmed`, duplicates on rerun, guessing a language from a first name, suppressions skipped
before paying), 3 that need a human (untested tier change, README overclaim, a plugin framework
nobody asked for) and 3 clean ones. `run_evals.py` fails if the agent misses one block, blocks one
good change, gets under 80% exact verdicts, or cites the right rule in under 80% of cases. It runs
in CI whenever the reviewer, its rubric or `AGENTS.md` changes
([`.github/workflows/review-evals.yml`](.github/workflows/review-evals.yml)).

```bash
pip install -e ".[review]"
export ANTHROPIC_API_KEY=...                                  # without it: prechecks only
PYTHONPATH=tools python -m gtm_review.review --base origin/main    # review your branch
PYTHONPATH=tools python -m gtm_review.run_evals                     # measure the reviewer
```

## Using it with Claude Code

The repository is set up so Claude Code starts with the right context: `CLAUDE.md` imports
`AGENTS.md` (architecture, commands, rules such as "never spend credits without approval"),
`CONTEXT.md` says what data is loaded and what the next step is, and `docs/` records the ICP and
the decisions. `/enrich-pilot` walks through the gated 10-contact pilot (report, balance, approval, then the API
or the FullEnrich connector). Real data goes in
`data/private/`, which git ignores.

## Tests and CI

`pytest` runs 93 tests: parsing, idempotent loading, signals and suppressions, the score
arithmetic, the enrichment filters, cooldown and report, batching, the API client with a fake
network, both result formats (API docs and MCP connector), the export gates and the Pipedrive
round trip, the HubSpot push against an in-memory fake API (matching, reruns, associations), the
webhook server on a local port, and the review agent with a fake model client (prechecks, verdict
merging, refusals, eval scoring). Four tests need a real Postgres (`GTM_TEST_PG_URL`, a
database whose name contains `test`): the warehouse push, its rerun, its rollback, row level
security and the score parity with SQLite. CI starts a Postgres 16 service for them. The GitHub
Actions workflow
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) lints with ruff, runs the tests on
Python 3.11 to 3.13, then runs every command on the sample data. That last step checks that each
command succeeds, not what it prints.

## Limits (read before relying on it)

- **The sample data is invented**: fictional companies, people and `.example` domains. Real data
  is never committed.
- **I have not run `--live` against a real FullEnrich account.** The request format follows the
  public API docs; the tests use a fake network. The webhook parser accepts the response shapes
  shown in the docs (`datas`, `data`, a single record) but should be checked against a real
  payload before trusting it.
- **First real pilot (2026-10-27), run through the FullEnrich MCP connector, not `--live`:**
  10 contacts sent (work emails only), 8 emails found, all `DELIVERABLE`; 8 credits charged,
  nothing for the 2 not found. Its result format is now covered by the parser and a test, and
  re-importing the real result with `gtm import-results` gives the same 8/10.
- **The HubSpot push is tested against a fake API only** until I run it on a real portal.
- **The review agent ran for real for the first time on 2026-10-29**, on its own pull request.
  Evals: 15/15 exact verdicts, 0 missed blocks, 0 false blocks, the expected rule cited in 15/15.
  That is one run on 15 cases I wrote myself, together with the rubric: a small, friendly set,
  not proof that it generalises. Its first review flagged one overclaim in this very README
  ("before it can be merged"), a CI bug that hid failing eval reports, and one false positive
  (it thought `gtm load` did not create the schema). The second flagged that a pull request
  could weaken its own reviewer and that a leaked key would still be sent to the model. The leak
  is fixed. The reviewer's code, rubric and `AGENTS.md` now come from `main`, but the workflow
  file itself still comes from the pull request, so a pull request can still switch the gate off
  until `.github/` is protected (CODEOWNERS plus a required review, or a ruleset). The scores are posted on each pull request that changes the reviewer.
- **No outreach has been sent from this project.** `gtm export` writes a file; the score has not
  been checked against replies or won deals yet (there are no deals on the real list).
- **The warehouse push is tested against Postgres 16** (locally and in CI) and run once on the
  real data into a local Postgres. Not yet run against a Supabase project.
- The score is a transparent heuristic, not a model. The weights are my judgment, not tuned on
  conversion data.
- Single-user: no authentication on `serve` beyond the shared token, no TLS (put it behind a
  proxy).

## Next steps

1. Make the `review` check a required status check on `main`.
2. Run `gtm push hubspot --live --yes` once on a real HubSpot portal.
3. Run `gtm warehouse push --yes` against a Supabase project.
4. Back-test the score: do tier A accounts actually convert more? Adjust weights from the data.

## Layout

```
AGENTS.md CLAUDE.md CONTEXT.md   context for coding agents
docs/       ICP and decisions
sql/        schema, score, pipeline health
src/        loader, scoring, enrich, export, hubspot, warehouse, webhook, cli
tools/      gtm_review: the pull request review agent, its rubric and evals
tests/      93 tests
scripts/    demo.sh, sample data generator, FullEnrich company search -> CSV
data/sample fictional exports in Pipedrive's native header format
```
