---
description: Gated 10-contact FullEnrich pilot (report first, nothing spent until approved)
---

Run the enrichment pilot on the private database, step by step:

1. Run `gtm --db data/private/gtm.db enrich --limit 10 --report data/private/report.md`
   (dry run) and show the report.
2. Call the FullEnrich `get_credits` tool and tell the user the balance and the maximum cost
   (one credit per work email found, nothing when not found).
3. Stop and ask for approval. Do not continue without an explicit yes.
4. On approval only, run the same command with `--live --yes` (needs `FULLENRICH_API_KEY`).
   Without an API key, send the same contacts with the FullEnrich `enrich_bulk` tool (field
   `contact.work_emails` only), save the finished result JSON under `data/private/raw/` and run
   `gtm --db data/private/gtm.db import-results <file>`.
5. When the job is done, run `gtm --db data/private/gtm.db fetch <enrichment_id>`, then
   `gtm --db data/private/gtm.db score --top 20`, and report: emails found out of 10, the
   hit rate, and credits spent. Do not print any email address in the conversation.
