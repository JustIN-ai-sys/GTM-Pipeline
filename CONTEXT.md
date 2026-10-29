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
- No deals. Companies are scored on fit + signals (+ coverage once emails exist), every tier is
  C. **Read the ranking by score, not by tier.**
- People (2026-10-27): 13 found with free `search_people` at 9 of the top 10 companies
  (founders, chiefs of staff, RevOps, one sales director). No usable match at the 10th.
- `data/private/raw/` keeps the raw search responses so the CSVs can be rebuilt with
  `scripts/companies_from_fullenrich.py`.

## What has been verified

- 81 tests pass, ruff is clean, the pipeline runs end to end on both datasets.
- The FullEnrich request format follows the public docs. **No `--live` run has been verified
  against a real account yet.**
- Pilot 1 (2026-10-27, via the MCP `enrich_bulk` tool, work emails only): **8 emails found out
  of 10** (all `DELIVERABLE`), 8 credits charged, 0 for the 2 not found. Results stored with
  source `fullenrich_mcp`; the 2 not found are in the 90-day cooldown.
- The MCP result shape is `results[].contact_info.most_probable_work_email {email, status}` and
  `work_emails[]`, with no `custom` field. `parse_outcomes` reads it and maps by LinkedIn URL;
  `gtm import-results` on the saved pilot result reproduces 8 found / 2 not found.
- `gtm export` on the private data: 8 contacts at 6 accounts ready for a sequencer (nothing sent).
- `gtm push hubspot` is tested against an in-memory fake API only. Dry run on the private data:
  6 companies, 8 contacts. Not yet run on a real HubSpot portal (needs `HUBSPOT_TOKEN`, and
  `api.hubapi.com` is blocked in the cloud session: run it locally or allow the host).
- Review agent (`tools/gtm_review/`, `.github/workflows/review.yml`): prechecks and verdict logic
  are unit-tested with a fake model client. **The model review and the 15-case evals have not run
  against the API yet**: they need the `ANTHROPIC_API_KEY` repository secret. Prechecks alone
  flag only the committed-contacts case and none of the clean cases.
- The webhook payload shape is not documented. The parser accepts the shapes in the docs; check
  it against one real payload.

## Next steps

0. Add the `ANTHROPIC_API_KEY` secret, run the reviewer evals, record the scores here and in the
   README, and open every change as a pull request so the agent reviews it.
1. Hit rate is above 6/10: enrich the remaining 3 eligible people (max 3 credits), then search
   people for the next companies in the ranking.
2. Add suppressions (customers, opt-outs) before any larger run or any export.
3. Import the export into a sequencer, track replies, and feed them back as signals or deals.
4. Verify a real `--live` run once an API key is available.
5. Back-test the score once real deals or replies exist.

## Open questions

- Which job titles to target at each account (see `docs/ICP.md`).
- Whether to receive results by webhook (needs a public URL) or by `gtm fetch`.
