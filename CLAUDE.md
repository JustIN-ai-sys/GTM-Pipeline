@AGENTS.md

## Claude Code specifics

- Read `CONTEXT.md` first: it says what data is loaded and what the next step is.
- Use the FullEnrich MCP tools for free searches (`search_companies`, `search_people`) and to
  check `get_credits`. Announce the credit cost before any `enrich_bulk` or export.
- The user writes in French; reply in French. Code, README and commit messages stay in English.
- Use `/enrich-pilot` for the gated 10-contact pilot.
