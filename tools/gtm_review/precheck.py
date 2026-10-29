"""Deterministic checks on the lines a diff adds. Cheap, no network, run before the model.

They catch what must never be merged and what a regex can see: secrets, real email addresses,
private data files. Anything that needs judgment is left to the model review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bpat-(?:na|eu)\d*-[0-9a-f-]{20,}"),  # HubSpot private app token
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][A-Za-z0-9_\-]{24,}['\"]"
    ),
]
EMAIL = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._%+-]*@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})\b")
# Reserved names that can never be a real mailbox (RFC 2606), plus commit-trailer addresses.
ALLOWED_EMAIL_DOMAINS = (
    "example",
    "example.com",
    "example.org",
    "test",
    "invalid",
    "localhost",
    "anthropic.com",
    "users.noreply.github.com",
)
PRIVATE_PATHS = re.compile(r"^(?:data/private/|.*\.db$|\.env$|out/)")


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    file: str
    line: int
    problem: str
    fix: str

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """(file, new line number, text) for every line the diff adds."""
    out: list[tuple[str, int, str]] = []
    path, line = "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            path = "" if target == "/dev/null" else target.removeprefix("b/")
        elif raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            line = int(match.group(1)) if match else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            out.append((path, line, raw[1:]))
            line += 1
        elif not raw.startswith("-"):
            line += 1
    return out


def changed_files(diff: str) -> list[str]:
    files = []
    for raw in diff.splitlines():
        if raw.startswith("+++ ") and raw[4:].strip() != "/dev/null":
            files.append(raw[4:].strip().removeprefix("b/"))
    return files


def _allowed_email(domain: str) -> bool:
    domain = domain.lower()
    return any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)


def run(diff: str) -> list[Finding]:
    findings: list[Finding] = []
    for path in changed_files(diff):
        if PRIVATE_PATHS.match(path) and not path.endswith(".example"):
            findings.append(
                Finding(
                    "high",
                    "R2",
                    path,
                    0,
                    "Adds a private data file (data/private, a database, .env or out/).",
                    "Remove it from the commit; these paths are git-ignored for a reason.",
                )
            )
    for path, line, text in added_lines(diff):
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(
                    Finding(
                        "high",
                        "R2",
                        path,
                        line,
                        "Adds what looks like a secret or API key.",
                        "Remove it, rotate the key, read it from the environment instead.",
                    )
                )
                break
        for match in EMAIL.finditer(text):
            if not _allowed_email(match.group(1)):
                findings.append(
                    Finding(
                        "high",
                        "R2",
                        path,
                        line,
                        "Adds a real-looking email address (not an *.example domain).",
                        "Use an invented .example address, or keep it in data/private.",
                    )
                )
    return findings
