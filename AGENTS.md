# AGENTS.md

Instructions for any coding agent (Claude Code reads `CLAUDE.md`, which imports this file).
Read `CONTEXT.md` for the current state of the data and what is already decided.

## What this project is

`gtm-pipeline` turns a CRM export (or a plain list of companies) into a ranked list of accounts
to work, then finds missing work emails through FullEnrich. Three layers, kept separate:

- **Context**: what we know and where it came from. Tables `organizations`, `people`, `deals`,
  `signals`, `suppressions`, `enrichment_attempts`. Code: `loader.py`, `sql/schema.sql`.
- **Orchestration**: what to do next. `sql/score_accounts.sql` (the score), `enrich.classify`
  (who is worth paying for), `scoring.py`.
- **Activation**: actions with side effects. `enrich.start_bulk` (spends credits), `webhook.py`
  (receives results). Nothing here runs unless the user passes `--live --yes`. `export.py`
  writes a CSV for lemlist or Pipedrive; it never sends anything. `hubspot.py` writes the same
  contacts to HubSpot, only with `--live --yes` and `HUBSPOT_TOKEN`.

## Commands

```bash
pip install -e ".[dev]"
pytest -q                 # 81 tests, standard library only at runtime
bash scripts/demo.sh      # whole pipeline on the sample data, spends nothing
ruff check . && ruff format .
gtm --db data/private/gtm.db init
gtm --db data/private/gtm.db load --orgs data/private/organizations.csv \
    --signals data/private/signals.csv
gtm --db data/private/gtm.db score --top 20 --out data/private/ranking.csv
gtm --db data/private/gtm.db enrich --limit 10 --report data/private/report.md   # dry run
gtm --db data/private/gtm.db import-results data/private/raw/result.json  # saved JSON result
gtm --db data/private/gtm.db export --format lemlist --out data/private/out/lemlist.csv
gtm --db data/private/gtm.db push hubspot --min-score 40          # dry run; --live --yes writes

# review agent (needs pip install -e ".[review]" and ANTHROPIC_API_KEY; without it, prechecks only)
PYTHONPATH=tools python -m gtm_review.review --base origin/main   # review your branch
PYTHONPATH=tools python -m gtm_review.run_evals                    # measure the reviewer
```

Before you open a pull request, run the review agent on your branch and fix what it blocks. Work
goes through pull requests, not straight to `main`: that is where the agent reviews it.

## Rules that must not be broken

1. **Never spend credits or write to a CRM on your own.** `gtm enrich` is a dry run unless `--live --yes` is given
   by the user in this conversation. Always show the report first and state the maximum cost.
   Check the balance with the FullEnrich `get_credits` tool before any live run.
2. **Never commit personal data or secrets.** `data/private/`, `*.db`, `.env`, `out/` are
   git-ignored. Real contacts, emails and API keys stay out of the repository and out of any
   commit message, issue or README. Only aggregates (counts, score distributions) may be shared.
3. **Never use employer or client CRM data** in this repository. The sample data is invented
   (`*.example` domains); the private data comes from FullEnrich searches the user ran.
4. **Statuses carry a source and a date, never a bare boolean.** "Not found" is not "does not
   use it". A later `not_found` must never replace an earlier `confirmed`.
5. **Never overwrite an existing email.** Loads and enrichment only fill empty values.
6. **Filter before paying.** ICP size and industry, suppressions and the not-found cooldown are
   applied in `enrich.classify` before any request is built.
7. **Idempotent loads.** Re-running `gtm load` must not duplicate rows or erase data (tests
   cover it). Keep upserts.
8. **No guessing about people.** Do not infer nationality, age or anything personal from a name.
9. **Errors must not leak the API key.** Keep error messages to status codes.

## Style

Python 3.11+, standard library only at runtime, type hints, line length 100, ruff rules
`E,F,I,UP,B`. SQL stays in `sql/` so it can be read without Python. Every behaviour change
comes with a test; the network is faked with an injected opener (see `tests/test_enrich.py`).
README is in English, first person, and lists limits honestly (no `--live` run has been
verified against a real account unless `CONTEXT.md` says so).

## Where things are

```
sql/schema.sql            tables, ICP weights, signal rules
sql/score_accounts.sql    the 0-100 score
src/gtm_pipeline/         loader, scoring, enrich, export, hubspot, webhook, cli
tools/gtm_review/         PR review agent: prechecks, rubric, model review, evals
tests/                    pytest, fake network
data/sample/              invented demo data (committed)
data/private/             real data from FullEnrich (git-ignored)
docs/ICP.md               who counts as a good account, and why
docs/DECISIONS.md         decisions and their reasons
.claude/commands/         slash commands (/enrich-pilot)
```
