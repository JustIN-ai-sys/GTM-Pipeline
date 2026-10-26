"""Turn saved FullEnrich `search_companies` results into loadable CSVs.

Each input file is the JSON returned by a company search. The technology you filtered
on becomes a *signal* (status "confirmed", with its source and date), because the
filter itself is the evidence: the company was returned for "uses HubSpot".

Usage:
    python scripts/companies_from_fullenrich.py OUT_DIR hubspot=raw/a.json salesforce=raw/b.json

Writes OUT_DIR/organizations.csv and OUT_DIR/signals.csv (signal name: crm_<technology>).
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

SOURCE = "fullenrich_technographics"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    orgs: dict[str, dict] = {}
    signals: set[tuple[str, str]] = set()
    for spec in argv[2:]:
        tech, _, path = spec.partition("=")
        for company in json.loads(Path(path).read_text())["companies"]:
            name = company["name"]
            orgs.setdefault(
                name,
                {
                    "Organization - Name": name,
                    "Organization - Website": company.get("domain") or "",
                    "Organization - Industry": company.get("industry") or "",
                    "Organization - Employees": company.get("headcount") or "",
                    "Organization - Country": company.get("headquarters_country") or "",
                },
            )
            signals.add((name, f"crm_{tech.lower()}"))

    with open(out / "organizations.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(next(iter(orgs.values()))))
        writer.writeheader()
        writer.writerows(orgs.values())
    today = date.today().isoformat()
    with open(out / "signals.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Organization", "Signal", "Status", "Source", "Observed at"])
        for name, signal in sorted(signals):
            writer.writerow([name, signal, "confirmed", SOURCE, today])
    print(f"{len(orgs)} companies, {len(signals)} signals -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
