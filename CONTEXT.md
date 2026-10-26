# CONTEXT.md

Current state of the project. Update it when something changes; agents read it first.

## Goal

Rank accounts worth working and fill in missing contact emails without wasting credits. The
demo runs on invented data; the real run uses companies found with FullEnrich searches.

## Data

| Dataset | Where | Committed? | What it is |
|---|---|---|---|
| Sample | `data/sample/` | yes | 36 invented companies, people and deals (`*.example`), plus signals and suppressions |
| Private | `data/private/` | **no** (git-ignored) | 31 real software companies headquartered in San Francisco, 50-500 employees, found with FullEnrich company search |

Private dataset details (aggregates only):

- Filters used: headquarters San Francisco, industry Software Development, headcount 50-200 and
  201-500, one search per technology.
- 35 signals, all `confirmed`, source `fullenrich_technographics`, observed 2026-10-26:
  HubSpot 20 companies, Salesforce 5, Pipedrive 10 (some companies match several).
- No deals and no people yet. Companies are scored on fit + signals only, so the maximum is 50
  and every tier is C. **Read the ranking by score, not by tier.**
- `data/private/raw/` keeps the raw search responses so the CSVs can be rebuilt with
  `scripts/companies_from_fullenrich.py`.

## What has been verified

- 45 tests pass, ruff is clean, the pipeline runs end to end on both datasets.
- The FullEnrich request format follows the public docs. **No `--live` run has been verified
  against a real account yet.** The first pilot (10 contacts) is the verification.
- The webhook payload shape is not documented. The parser accepts the shapes in the docs; check
  it against one real payload.

## Next steps

1. Get people for the top companies (FullEnrich `search_people` is free, 10 per call, or a CSV
   export from the app) and load them with `gtm load --people`.
2. Dry run: `gtm enrich --limit 10 --report data/private/report.md`.
3. Pilot of 10 with `--live --yes`; read the hit rate; then rerun on the rest (the cooldown
   skips anyone already looked up).
4. Add suppressions (customers, opt-outs) before any larger run.
5. Back-test the score once real deals exist.

## Open questions

- Which job titles to target at each account (see `docs/ICP.md`).
- Whether to receive results by webhook (needs a public URL) or by `gtm fetch`.
