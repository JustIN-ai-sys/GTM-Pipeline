# Decisions

| Decision | Why |
|---|---|
| SQLite and plain SQL for the score | Readable by a non-engineer, no infrastructure, easy to test. Moving to a warehouse means replacing `julianday()`. |
| Standard library only at runtime | Nothing to install to run it; fewer things to break. |
| Dry run by default, `--live --yes` to spend | An enrichment call costs money and cannot be undone. |
| Filter, then report, then spend | The report shows who is left and the maximum cost before any request exists. |
| Signals are `status + source + date` | "Not found" is not "does not use it". A `confirmed` signal is never overwritten by a later `not_found`. |
| `enrichment_attempts` with a 90-day cooldown | A rerun must not pay twice for the same lookup. |
| Suppressions are checked twice | Before the request (domain, org) and when the result comes back (email, domain). |
| Test on 10 contacts first | The hit rate decides whether the other contacts are worth the credits. |
| Real data stays in `data/private/` | Contacts and emails are personal data; employer or client CRM data must never enter a public repository. |
| Score weights are judgment, not a model | They are written down and editable; back-test them once deals exist. |
| Export a CSV, do not push to a sequencer or CRM | Sending email cannot be undone; a person reads the list before importing it. |
| Suppressions checked a third time at export | The do-not-contact list may have grown between enrichment and outreach. |
| Verified emails only in exports by default | A bounce hurts the sending domain more than a missing contact costs. |
| HubSpot: update only `gtm_*` properties on existing records | The CRM belongs to the reps; a sync must not overwrite a name, title or owner they edited. |
| Review agent = regex prechecks, then the model | Secrets and real emails must never pass, whatever the model says; judgment calls go to the model. |
| A refusal, a cut-off answer or an oversized diff is `needs_human`, never a pass | A review that did not happen must not look like an approval. |
| Evals gate on zero missed blocks and zero false blocks | A missed block lets harm through; a false block teaches the team to ignore the agent. |
| Map results by LinkedIn URL when `custom` is missing | The MCP connector and app exports return no `custom` field; LinkedIn URL is the key that was sent. |
