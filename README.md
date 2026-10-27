# gtm-pipeline

Turn a CRM export into a ranked list of accounts to work, and fill in the missing contact emails.

I built this from my own workflow: I manage a deal pipeline, I score which accounts deserve the
next hour, and I enrich the contacts that have no email. Doing that by hand in a spreadsheet does
not scale, so each step is now a small, tested command.

```
CRM export (CSV)  ->  SQLite  ->  SQL account score  ->  ranked list + next action
                         ^                                        |
                         |  emails found                          v
              FullEnrich bulk API  <-  contacts with no email     gtm export
              (webhook, fetch or saved JSON)                      |
                                                                  v
                                           lemlist / Pipedrive CSV (verified, not suppressed)
```

## Results so far

On real data (31 San Francisco software companies found with FullEnrich company search, kept out
of this repository):

- **Accounts**: ranked on fit and confirmed CRM signals (HubSpot 20, Pipedrive 10, Salesforce 5).
- **People**: 13 decision makers found at 9 of the top 10 accounts with the free people search
  (founders, chiefs of staff, RevOps, sales).
- **Pilot enrichment, 10 contacts: 8 work emails found, all `DELIVERABLE`, 8 credits spent**
  (nothing charged for the 2 not found, which are now in a 90-day cooldown).
- **Hand-off**: 8 contacts at 6 accounts exported for a sequencer, best accounts first.

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

## Quick start

Python 3.11+. No runtime dependencies (standard library only).

```bash
pip install -e ".[dev]"
python scripts/make_sample_data.py          # fictional data, already committed in data/sample

gtm init
gtm load --orgs data/sample/organizations.csv \
         --people data/sample/people.csv --deals data/sample/deals.csv \
         --signals data/sample/signals.csv --suppressions data/sample/suppressions.csv
gtm score --as-of 2026-10-26 --top 8 --out out.csv
gtm health --as-of 2026-10-26
gtm enrich --limit 30 --min-employees 20    # dry run: a report, spends nothing
gtm export --format lemlist --out out/lemlist.csv --min-score 45 --include-unverified
pytest
```

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
row. Results that come back without it (the FullEnrich MCP connector, or a JSON exported from the
app) are mapped by normalized LinkedIn URL; `gtm import-results <file>` stores such a file with
the same rules as `fetch`.

- **Start with `--limit 10`.** Read the hit rate, then rerun on the rest. Reruns are cheap: found
  contacts have an email, and "not found" is remembered in `enrichment_attempts`.
- **Every email keeps its source and date** (`email_source`, `email_checked_at`).
- **Webhook first.** `gtm serve` stores what FullEnrich posts back. Set `WEBHOOK_TOKEN`; requests
  without the right `?token=` get a 401. `gtm fetch <id>` is the fallback.
- **Never overwrites** an email that is already stored, and never stores an email on the
  suppression list.
- **Errors do not leak the key.** HTTP errors raise a message with the status code only.

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
  action. In lemlist they become variables (`{{accountScore}}`, `{{crmUsed}}`,
  `{{nextAction}}`); in Pipedrive they go in the note, with the email's source and check date.
- The Pipedrive format uses the same headers the loader reads, so an export loads back with
  `gtm load --people` without duplicates (a test covers it).

## Using it with Claude Code

The repository is set up so Claude Code starts with the right context: `CLAUDE.md` imports
`AGENTS.md` (architecture, commands, rules such as "never spend credits without approval"),
`CONTEXT.md` says what data is loaded and what the next step is, and `docs/` records the ICP and
the decisions. `/enrich-pilot` runs the gated 10-contact pilot. Real data goes in
`data/private/`, which git ignores.

## Tests and CI

`pytest` runs 61 tests: parsing, idempotent loading, signals and suppressions, the score
arithmetic, the enrichment filters, cooldown and report, batching, the API client with a fake
network, both result formats (API docs and MCP connector), the export gates and the Pipedrive
round trip, and the webhook server on a local port. The GitHub Actions workflow
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) lints with ruff, runs the tests on
Python 3.11 to 3.13, then runs the whole pipeline on the sample data as a smoke test, export included.

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
- **No outreach has been sent from this project.** `gtm export` writes a file; the score has not
  been checked against replies or won deals yet (there are no deals on the real list).
- SQLite, not a warehouse. The SQL uses standard CTEs; moving to Postgres or BigQuery means
  replacing `julianday()` with date arithmetic and adding a loading step.
- The score is a transparent heuristic, not a model. The weights are my judgment, not tuned on
  conversion data.
- Single-user: no authentication on `serve` beyond the shared token, no TLS (put it behind a
  proxy).

## Next steps

1. Run the first real pilot (10 contacts) and check the webhook payload against the parser.
2. Pull deals directly from the Pipedrive API instead of CSV.
3. Back-test the score: do tier A accounts actually convert more? Adjust weights from the data.

## Layout

```
AGENTS.md CLAUDE.md CONTEXT.md   context for coding agents
docs/       ICP and decisions
sql/        schema, score, pipeline health
src/        loader, scoring, enrich, webhook, cli
tests/      45 tests
scripts/    sample data generator, FullEnrich company search -> CSV
data/sample fictional exports in Pipedrive's native header format
```
