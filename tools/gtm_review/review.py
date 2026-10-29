"""Review a pull request diff the way a senior GTM engineer would, then gate the merge.

    PYTHONPATH=tools python -m gtm_review.review --base origin/main --out review.md

Two layers:
    1. `precheck.run`: regexes for secrets, real emails and private files (no network).
    2. Claude (Anthropic API) reads the rubric, AGENTS.md and the diff, and returns a verdict
       (`approve` | `needs_human` | `block`) with findings, as JSON checked against a schema.

The worst finding wins. Exit code 1 means `block`; CI fails the check. Without
ANTHROPIC_API_KEY only the prechecks run, and the comment says so.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from gtm_review import precheck

MODEL = "claude-opus-5-5"
MAX_DIFF_CHARS = 200_000  # beyond this, ask a human instead of reviewing a partial diff
MARKER = "<!-- gtm-review -->"
ROOT = Path(__file__).resolve().parents[2]
RULES = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "G1", "G2", "G3", "G4"]
VERDICTS = ["approve", "needs_human", "block"]

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": VERDICTS},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "rule": {"type": "string", "enum": RULES},
                    "file": {"type": "string"},
                    "line": {"type": "integer"},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["severity", "rule", "file", "line", "problem", "fix"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdict", "summary", "findings"],
    "additionalProperties": False,
}


def system_prompt() -> str:
    """Stable across reviews, so it is cached: the rubric, then the repo's own rules."""
    rubric = (Path(__file__).parent / "rubric.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    return f"{rubric}\n\n# The repository's AGENTS.md (its own rules)\n\n{agents}"


def model_review(diff: str, client: Any) -> dict[str, Any]:
    """Ask Claude for a verdict. Refusals, truncation and API errors become `needs_human`."""
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=[
                {"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": f"Review this pull request diff.\n\n{diff}"}],
        )
    except _api_errors() as exc:
        # The class name only: an API error is not a verdict on the code, and never a pass.
        return _needs_human(f"The model review failed ({type(exc).__name__}).")
    if response.stop_reason in ("refusal", "max_tokens"):
        return _needs_human(f"The model review stopped early ({response.stop_reason}).")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _needs_human("The model review did not return valid JSON.")


def _api_errors() -> tuple[type[Exception], ...]:
    try:
        import anthropic
    except ImportError:
        return ()
    return (anthropic.APIError,)


def _needs_human(reason: str) -> dict[str, Any]:
    return {
        "verdict": "needs_human",
        "summary": reason,
        "findings": [
            {
                "severity": "medium",
                "rule": "G4",
                "file": "",
                "line": 0,
                "problem": reason,
                "fix": "Have a person review this pull request.",
            }
        ],
    }


def review(diff: str, client: Any | None) -> dict[str, Any]:
    """Prechecks + model review, merged. `client=None` runs the prechecks only."""
    found = [f.as_dict() for f in precheck.run(diff)]
    leak = any(f["severity"] == "high" for f in found)
    if not diff.strip():
        result = {"verdict": "approve", "summary": "Empty diff.", "findings": []}
    elif leak:
        # Do not send a key or a contact to one more third party: the precheck block is enough.
        result = {
            "verdict": "block",
            "findings": [],
            "summary": "Prechecks found a secret, personal data or a private file. "
            "The model review was skipped so that content is not sent to the API.",
        }
        client = None
    elif client is None:
        result = {
            "verdict": "approve",
            "findings": [],
            "summary": "Prechecks only: ANTHROPIC_API_KEY is not set, no model review ran.",
        }
    elif len(diff) > MAX_DIFF_CHARS:
        result = _needs_human(f"Diff too large for one review ({len(diff)} characters).")
    else:
        result = model_review(diff, client)
    result["model_review"] = client is not None and bool(diff.strip())
    result["findings"] = found + list(result.get("findings", []))
    result["verdict"] = worst(result)
    result["prechecks"] = len(found)
    return result


def worst(result: dict[str, Any]) -> str:
    """The verdict implied by the findings, never milder than the model's own verdict."""
    severities = {f["severity"] for f in result["findings"]}
    implied = (
        "block" if "high" in severities else "needs_human" if "medium" in severities else "approve"
    )
    return max(implied, result.get("verdict", "approve"), key=VERDICTS.index)


def render(result: dict[str, Any]) -> str:
    """Markdown for the pull request comment."""
    icon = {"approve": "Approve", "needs_human": "Needs a human", "block": "Block"}
    title = icon[result["verdict"]]
    if not result.get("model_review") and result["verdict"] == "approve":
        title = "Prechecks passed, no model review"
    lines = [MARKER, f"## GTM review: {title}", "", result["summary"], ""]
    if result["findings"]:
        lines += ["| Severity | Rule | Where | Problem | Fix |", "|---|---|---|---|---|"]
        for f in sorted(
            result["findings"], key=lambda f: ["high", "medium", "low"].index(f["severity"])
        ):
            where = f"`{f['file']}:{f['line']}`" if f["file"] else "-"
            cells = [f["severity"], f["rule"], where, f["problem"], f["fix"]]
            lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    else:
        lines.append("No findings.")
    lines += [
        "",
        f"_Rubric: `tools/gtm_review/rubric.md`. Model: {MODEL}. "
        f"Prechecks flagged {result['prechecks']}._",
    ]
    return "\n".join(lines) + "\n"


def git_diff(base: str) -> str:
    return subprocess.run(
        ["git", "diff", "--unified=5", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout


def make_client() -> Any | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic  # optional dependency: pip install -e ".[review]"

    return anthropic.Anthropic()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--base", help="git ref to diff against, e.g. origin/main")
    source.add_argument("--diff-file", help="read the diff from a file instead of git")
    parser.add_argument("--out", help="write the markdown comment here")
    parser.add_argument("--json", help="write the raw result here")
    parser.add_argument("--no-llm", action="store_true", help="prechecks only")
    args = parser.parse_args(argv)

    if args.diff_file:
        diff = Path(args.diff_file).read_text(encoding="utf-8")
    else:
        diff = git_diff(args.base)
    result = review(diff, None if args.no_llm else make_client())
    comment = render(result)
    print(comment)
    if args.out:
        Path(args.out).write_text(comment, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 1 if result["verdict"] == "block" else 0


if __name__ == "__main__":
    sys.exit(main())
