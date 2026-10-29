#!/usr/bin/env bash
# Run the whole pipeline on the invented sample data. Spends nothing, sends nothing.
#   pip install -e ".[dev]" && bash scripts/demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p out
DB=out/demo.db
AS_OF=2026-10-26
rm -f "$DB"

step() { printf '\n\033[1m$ %s\033[0m\n' "$*"; "$@"; }

step gtm --db "$DB" load --orgs data/sample/organizations.csv \
  --people data/sample/people.csv --deals data/sample/deals.csv \
  --signals data/sample/signals.csv --suppressions data/sample/suppressions.csv
step gtm --db "$DB" score --as-of "$AS_OF" --top 8 --out out/ranking.csv
step gtm --db "$DB" enrich --as-of "$AS_OF" --limit 10 --min-employees 20
step gtm --db "$DB" export --as-of "$AS_OF" --format lemlist --out out/lemlist.csv \
  --min-score 45 --include-unverified
step gtm --db "$DB" push hubspot --as-of "$AS_OF" --min-score 45 --include-unverified

printf '\nDone. Files in out/ (git-ignored): ranking.csv, lemlist.csv, demo.db\n'
