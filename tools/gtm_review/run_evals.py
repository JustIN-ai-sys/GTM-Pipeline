"""Measure the reviewer on labelled diffs: does it block what must be blocked, and only that?

    PYTHONPATH=tools python -m gtm_review.run_evals --out eval-report.md

Each case in evals/cases.json has an expected verdict and the rules a correct review cites
(any one of them is enough). The gate, checked in CI when the reviewer changes:
    - every `block` case is blocked (a missed block is the costly failure),
    - no `approve` or `needs_human` case is blocked (a false block stops good work),
    - at least MIN_ACCURACY of the cases get exactly the expected verdict,
    - at least MIN_RULE_RECALL of the cases with expected rules cite one of them.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gtm_review import review

EVALS = Path(__file__).parent / "evals"
MARKER = "<!-- gtm-evals -->"
MIN_ACCURACY = 0.8
MIN_RULE_RECALL = 0.8


def load_cases() -> list[dict[str, Any]]:
    cases = json.loads((EVALS / "cases.json").read_text(encoding="utf-8"))
    for case in cases:
        case["diff"] = (EVALS / "diffs" / f"{case['id']}.diff").read_text(encoding="utf-8")
    return cases


def score(cases: list[dict[str, Any]], reviewer: Callable[[str], dict[str, Any]]) -> dict:
    rows = []
    for case in cases:
        result = reviewer(case["diff"])
        cited = {f["rule"] for f in result["findings"]}
        rows.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "got": result["verdict"],
                "rule_hit": (not case["rules"]) or bool(cited & set(case["rules"])),
                "cited": sorted(cited),
            }
        )
    blocks = [r for r in rows if r["expected"] == "block"]
    others = [r for r in rows if r["expected"] != "block"]
    with_rules = [r for r, c in zip(rows, cases, strict=True) if c["rules"]]
    metrics = {
        "cases": len(rows),
        "accuracy": sum(r["got"] == r["expected"] for r in rows) / len(rows),
        "missed_blocks": sum(r["got"] != "block" for r in blocks),
        "false_blocks": sum(r["got"] == "block" for r in others),
        "rule_recall": sum(r["rule_hit"] for r in with_rules) / max(1, len(with_rules)),
    }
    metrics["passed"] = (
        metrics["missed_blocks"] == 0
        and metrics["false_blocks"] == 0
        and metrics["accuracy"] >= MIN_ACCURACY
        and metrics["rule_recall"] >= MIN_RULE_RECALL
    )
    return {"metrics": metrics, "rows": rows}


def render(report: dict) -> str:
    m = report["metrics"]
    lines = [
        MARKER,
        f"## Reviewer evals: {'PASS' if m['passed'] else 'FAIL'}",
        "",
        f"{m['cases']} cases · exact verdict {m['accuracy']:.0%} (min {MIN_ACCURACY:.0%}) · "
        f"missed blocks {m['missed_blocks']} · false blocks {m['false_blocks']} · "
        f"rule recall {m['rule_recall']:.0%} (min {MIN_RULE_RECALL:.0%})",
        "",
        "| Case | Expected | Got | Rule cited | Rules |",
        "|---|---|---|---|---|",
    ]
    for r in report["rows"]:
        mark = "" if r["got"] == r["expected"] else " ✗"
        lines.append(
            f"| {r['id']} | {r['expected']} | {r['got']}{mark} | "
            f"{'yes' if r['rule_hit'] else 'no'} | {', '.join(r['cited']) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", help="write the markdown report here")
    args = parser.parse_args(argv)
    client = review.make_client()
    if client is None:
        print("ANTHROPIC_API_KEY is not set: the evals need the model.", file=sys.stderr)
        return 2
    report = score(load_cases(), lambda diff: review.review(diff, client))
    text = render(report)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0 if report["metrics"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
