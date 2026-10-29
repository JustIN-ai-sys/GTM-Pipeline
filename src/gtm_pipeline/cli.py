"""Command line: init, load, score, health, enrich, fetch, import-results, import-clay, export,
push, warehouse, serve."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from gtm_pipeline import clay, enrich, export, hubspot, loader, scoring, warehouse
from gtm_pipeline.db import connect, init_db


def _print_table(rows, columns: list[str]) -> None:
    widths = [max(len(c), *(len(str(r[c])) for r in rows)) if rows else len(c) for c in columns]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True)))
    for row in rows:
        print("  ".join(str(row[c]).ljust(w) for c, w in zip(columns, widths, strict=True)))


def cmd_init(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    init_db(conn)
    print(f"Database ready: {args.db}")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    init_db(conn)
    stages = [s.strip() for s in args.stages.split(",")] if args.stages else None
    counts = loader.load_all(
        conn,
        args.orgs,
        args.people,
        args.deals,
        stages,
        signals=args.signals,
        suppressions=args.suppressions,
        email_source=args.email_source,
    )
    print("Loaded:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    rows = scoring.score_accounts(conn, args.as_of)
    _print_table(
        rows[: args.top],
        ["name", "score", "tier", "open_deals", "open_value", "contacts_with_email", "next_action"],
    )
    if args.out:
        scoring.write_scores_csv(rows, args.out)
        print(f"\nFull ranking written to {args.out} ({len(rows)} accounts)")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    rows = scoring.pipeline_health(conn, args.as_of)
    _print_table(rows, ["stage", "deals", "value", "avg_days_since_activity", "stale_deals"])
    return 0


def _filters(args: argparse.Namespace) -> enrich.Filters:
    return enrich.Filters(
        min_employees=args.min_employees,
        max_employees=args.max_employees,
        require_icp_industry=args.require_icp_industry,
        cooldown_days=args.cooldown_days,
    )


def cmd_enrich(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    filters = _filters(args)
    report = enrich.render_report(conn, filters, args.as_of, args.limit)
    print(report)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(report)
        print(f"Report written to {args.report}")
    if not args.live:
        print("Dry run: nothing was sent and no credit was spent.")
        return 0
    api_key = os.environ.get("FULLENRICH_API_KEY")
    if not api_key:
        print("FULLENRICH_API_KEY is not set.", file=sys.stderr)
        return 2
    if not args.yes:
        print("Refusing to spend credits without --yes.", file=sys.stderr)
        return 2
    rows = enrich.candidates(conn, args.limit, filters, args.as_of)
    for payload in enrich.build_payloads(rows, "gtm-pipeline", args.webhook_url):
        enrichment_id = enrich.start_bulk(payload, api_key)
        enrich.record_run(conn, enrichment_id, len(payload["data"]))
        print(f"Started {enrichment_id} ({len(payload['data'])} contacts)")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    api_key = os.environ.get("FULLENRICH_API_KEY")
    if not api_key:
        print("FULLENRICH_API_KEY is not set.", file=sys.stderr)
        return 2
    conn = connect(args.db)
    body = enrich.fetch_results(args.enrichment_id, api_key)
    return _store_results(conn, body, "fullenrich_fetch", args.as_of)


def cmd_import_results(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    with open(args.file, encoding="utf-8") as handle:
        body = json.load(handle)
    return _store_results(conn, body, args.source, args.as_of)


def _store_results(conn, body, source: str, as_of: str | None) -> int:
    outcomes = enrich.parse_outcomes(body, enrich.linkedin_index(conn))
    result = enrich.apply_outcomes(
        conn,
        outcomes,
        source=source,
        checked_at=as_of,
        record_not_found=enrich.is_finished(body),  # an unfinished job proves nothing
    )
    n_records = len(enrich.records(body))
    status = str(body.get("status") or "") if isinstance(body, dict) else ""
    print(f"status={status or 'unknown'} records={n_records} matched={len(outcomes)} {result}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    result = export.ready_contacts(conn, args.as_of, args.min_score, args.include_unverified)
    export.write_csv(result.rows, args.out, args.format)
    print(export.render_summary(result, args.format, args.out))
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    result = export.ready_contacts(conn, args.as_of, args.min_score, args.include_unverified)
    push_plan = hubspot.plan(result.rows)
    print(hubspot.render_plan(push_plan))
    if not args.live:
        print("Dry run: nothing was sent to HubSpot.")
        return 0
    token = os.environ.get("HUBSPOT_TOKEN")
    if not token:
        print("HUBSPOT_TOKEN is not set.", file=sys.stderr)
        return 2
    if not args.yes:
        print("Refusing to write to the CRM without --yes.", file=sys.stderr)
        return 2
    client = hubspot.Client(token)
    print(hubspot.push(push_plan, client), f"api_calls={client.calls}")
    return 0


def cmd_import_clay(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    init_db(conn)
    result = clay.import_tech_stack(conn, args.file, args.column, args.source, args.as_of)
    print(
        f"Clay rows: {result['rows']} · matched accounts: {result['matched']} · "
        f"no data: {result['no_data']} · unknown companies: {result['unmatched']}"
    )
    print(
        f"Signals: {result['confirmed']} confirmed, {result['not_found']} not found "
        "(a not found never replaces a confirmed). Rescore with gtm score."
    )
    return 0


def cmd_warehouse(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    url = args.url or os.environ.get("DATABASE_URL")
    target = warehouse.describe_target(url) if url else "no DATABASE_URL set"
    print(f"Warehouse push to {target}, schema {warehouse.SCHEMA}:")
    for table, count in warehouse.counts(conn).items():
        print(f"  {table:<20} {count} rows")
    if not args.live:
        print("Dry run: nothing was written. Add --live --yes to replace the warehouse tables.")
        return 0
    if not url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    if not args.yes:
        print("Refusing to copy contacts to the warehouse without --yes.", file=sys.stderr)
        return 2
    try:
        pg = warehouse.connect(url)
        try:
            written = warehouse.push(conn, pg)
        finally:
            pg.close()
    except warehouse.WarehouseError as exc:
        print(f"Warehouse push failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Done: {sum(written.values())} rows in {len(written)} tables, view account_scores ready."
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from gtm_pipeline.webhook import make_server

    token = os.environ.get("WEBHOOK_TOKEN")
    if not token:
        print("Warning: WEBHOOK_TOKEN is not set; any caller is accepted.", file=sys.stderr)
    server = make_server(args.db, token, args.host, args.port)
    print(f"Listening on http://{args.host}:{args.port}/webhook/fullenrich (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gtm", description=__doc__)
    parser.add_argument("--db", default="gtm.db", help="SQLite file (default: gtm.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the schema").set_defaults(func=cmd_init)

    p = sub.add_parser("load", help="load organizations, people and deals CSV exports")
    p.add_argument("--orgs", required=True)
    p.add_argument("--people", help="people CSV (optional)")
    p.add_argument(
        "--email-source",
        default="crm_import",
        help="where the people's emails come from, stored with each email (e.g. clay_enrichment)",
    )
    p.add_argument("--deals", help="deals CSV (optional)")
    p.add_argument(
        "--signals", help="CSV of observed facts: Organization, Signal, Status, Source, Observed at"
    )
    p.add_argument("--suppressions", help="CSV of do-not-contact entries: Kind, Value, Reason")
    p.add_argument("--stages", help="comma-separated stage names, in pipeline order")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("score", help="rank accounts with the SQL score")
    p.add_argument("--as-of", help="YYYY-MM-DD (default: today)")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--out", help="write the full ranking to this CSV")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("health", help="open pipeline by stage")
    p.add_argument("--as-of", help="YYYY-MM-DD (default: today)")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("enrich", help="find missing work emails (dry run by default)")
    p.add_argument("--limit", type=int, help="pilot size; start with 10")
    p.add_argument("--min-employees", type=int, help="skip companies smaller than this")
    p.add_argument("--max-employees", type=int, help="skip companies larger than this")
    p.add_argument(
        "--require-icp-industry",
        action="store_true",
        help="skip industries that have no positive weight in icp_rules",
    )
    p.add_argument(
        "--cooldown-days",
        type=int,
        default=enrich.DEFAULT_COOLDOWN_DAYS,
        help="do not look up again someone not found in the last N days (default: 90)",
    )
    p.add_argument("--as-of", help="YYYY-MM-DD (default: today)")
    p.add_argument("--report", help="also write the markdown report to this file")
    p.add_argument("--webhook-url", help="public URL FullEnrich will call when a batch is done")
    p.add_argument("--live", action="store_true", help="really call the API")
    p.add_argument("--yes", action="store_true", help="confirm that credits will be spent")
    p.set_defaults(func=cmd_enrich)

    p = sub.add_parser("fetch", help="pull the results of a finished enrichment by id")
    p.add_argument("enrichment_id")
    p.add_argument("--as-of", help="YYYY-MM-DD stored as the check date (default: today)")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser(
        "import-results",
        help="store a saved enrichment result (JSON from the app, the API or the MCP connector)",
    )
    p.add_argument("file")
    p.add_argument("--source", default="fullenrich_import", help="stored as email_source")
    p.add_argument("--as-of", help="YYYY-MM-DD stored as the check date (default: today)")
    p.set_defaults(func=cmd_import_results)

    p = sub.add_parser(
        "export", help="write a CSV of ready contacts for lemlist or Pipedrive (sends nothing)"
    )
    p.add_argument("--format", choices=sorted(export.FORMATS), default="lemlist")
    p.add_argument("--out", required=True, help="CSV path; it holds personal data, keep it private")
    p.add_argument("--min-score", type=int, default=0, help="leave out accounts below this score")
    p.add_argument(
        "--include-unverified",
        action="store_true",
        help="also export emails typed into the CRM that were never verified",
    )
    p.add_argument("--as-of", help="YYYY-MM-DD for the score (default: today)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser(
        "push", help="write ready contacts and account scores to HubSpot (dry run by default)"
    )
    p.add_argument("crm", choices=["hubspot"])
    p.add_argument("--min-score", type=int, default=0, help="leave out accounts below this score")
    p.add_argument("--include-unverified", action="store_true")
    p.add_argument("--as-of", help="YYYY-MM-DD for the score (default: today)")
    p.add_argument("--live", action="store_true", help="really call the HubSpot API")
    p.add_argument("--yes", action="store_true", help="confirm that the CRM will be written to")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("import-clay", help="turn a Clay table export (CSV) into CRM signals")
    p.add_argument("file")
    p.add_argument("--column", default="Tech Stack", help="the tech stack column's name")
    p.add_argument("--source", default="clay_tech_stack", help="stored as the signals' source")
    p.add_argument("--as-of", help="YYYY-MM-DD stored as the observation date (default: today)")
    p.set_defaults(func=cmd_import_clay)

    p = sub.add_parser(
        "warehouse", help="copy the database and the score view to Postgres or Supabase"
    )
    p.add_argument("action", choices=["push"])
    p.add_argument("--url", help="Postgres URL (default: the DATABASE_URL variable)")
    p.add_argument("--live", action="store_true", help="really write to the warehouse")
    p.add_argument("--yes", action="store_true", help="confirm that the tables are replaced")
    p.set_defaults(func=cmd_warehouse)

    p = sub.add_parser("serve", help="run the webhook receiver")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
