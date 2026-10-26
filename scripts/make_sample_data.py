"""Generate fictional CRM exports in Pipedrive's native header format.

Everything is invented: company names, people, and *.example domains.
Run: python scripts/make_sample_data.py
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "sample"
AS_OF = date(2026, 10, 26)
rng = random.Random(42)

PREFIX = ["Blue", "North", "Open", "Quick", "Iron", "Bright", "Lunar", "Maple", "Delta", "Nova"]
SUFFIX = [
    "Robotics",
    "Ledger",
    "Health",
    "Freight",
    "Grid",
    "Cloud",
    "Learn",
    "Retail",
    "Pay",
    "Labs",
]
INDUSTRIES = ["Software", "Fintech", "Healthcare", "Logistics", "Energy", "Retail", "Education"]
SIZES = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000"]
FIRST = ["Alex", "Sam", "Jordan", "Taylor", "Robin", "Casey", "Morgan", "Jamie", "Riley", "Quinn"]
LAST = [
    "Archer",
    "Bishop",
    "Carver",
    "Dalton",
    "Ellis",
    "Foster",
    "Grant",
    "Hayes",
    "Irwin",
    "Keller",
]
TITLES = ["VP Sales", "Head of RevOps", "CFO", "Chief of Staff", "Sales Ops Manager", "Founder"]
STAGES = ["Lead", "Contacted", "Meeting", "Proposal", "Negotiation"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    names = [f"{p} {s}" for p in PREFIX for s in SUFFIX]
    rng.shuffle(names)
    orgs = names[:36]

    with open(OUT / "organizations.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "Organization - Name",
                "Organization - Website",
                "Organization - Industry",
                "Organization - Employees",
                "Organization - Country",
            ]
        )
        for name in orgs:
            slug = name.lower().replace(" ", "-")
            w.writerow(
                [
                    name,
                    f"https://www.{slug}.example",
                    rng.choice(INDUSTRIES),
                    rng.choice(SIZES),
                    rng.choice(["United States", "France", "Germany"]),
                ]
            )

    with open(OUT / "people.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "Person - First name",
                "Person - Last name",
                "Person - Organization",
                "Person - Job title",
                "Person - Email - Work",
                "Person - LinkedIn",
            ]
        )
        seen: set[tuple[str, str, str]] = set()
        for name in orgs:
            slug = name.lower().replace(" ", "-")
            for _ in range(rng.randint(0, 4)):
                first, last = rng.choice(FIRST), rng.choice(LAST)
                if (name, first, last) in seen:
                    continue
                seen.add((name, first, last))
                has_email = rng.random() < 0.55
                email = f"{first.lower()}.{last.lower()}@{slug}.example" if has_email else ""
                linkedin = (
                    f"https://www.linkedin.com/in/{first.lower()}-{last.lower()}-{slug}-example"
                )
                w.writerow([first, last, name, rng.choice(TITLES), email, linkedin])

    with open(OUT / "deals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "Deal - Title",
                "Deal - Organization",
                "Deal - Value",
                "Deal - Stage",
                "Deal - Status",
                "Deal - Last activity date",
                "Deal - Deal created",
            ]
        )
        for name in orgs:
            for index in range(rng.choice([0, 1, 1, 2])):
                last = AS_OF - timedelta(days=rng.choice([1, 3, 6, 9, 13, 20, 28, 45, 70]))
                created = last - timedelta(days=rng.randint(5, 90))
                w.writerow(
                    [
                        f"{name} - deal {index + 1}",
                        name,
                        rng.choice([800, 2500, 9000, 15000, 30000, 65000]),
                        rng.choice(STAGES),
                        rng.choices(["Open", "Won", "Lost"], [8, 1, 1])[0],
                        last.isoformat(),
                        created.isoformat(),
                    ]
                )

    # Extra files use their own generator so the three files above never change.
    extra = random.Random(7)
    with open(OUT / "signals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Organization", "Signal", "Status", "Source", "Observed at"])
        for name in orgs:
            for signal in ("crm_hubspot", "crm_salesforce"):
                status = extra.choices(["confirmed", "not_found"], [3, 7])[0]
                if status == "confirmed" or extra.random() < 0.5:
                    w.writerow([name, signal, status, "sample_technographics", "2026-10-20"])

    with open(OUT / "suppressions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Kind", "Value", "Reason"])
        w.writerow(["org", orgs[0], "existing customer"])
        w.writerow(
            ["domain", f"{orgs[1].lower().replace(' ', '-')}.example", "asked not to be contacted"]
        )


if __name__ == "__main__":
    main()
