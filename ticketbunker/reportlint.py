"""Report lint gate (DESIGN.md §3.6) — the mechanical publish-time checks.

Schema: required sections present; claims carry stratum labels; inferences
cite observations. Length: per-section budgets. A component that fails the
gate is not accepted into reports/branches/.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REQUIRED_SECTIONS = ["Who", "What", "Where", "Why", "State"]
SECTION_BUDGET_LINES = 60
STRATUM_RE = re.compile(r"^\s*[-*]?\s*\[(observation|inference)\]", re.IGNORECASE)
CITATION_RE = re.compile(r"\(obs:|observations?/|merge[-_]base|diff|authorship|merge[-_]history|work[-_]separation|branch[- _]resolution", re.IGNORECASE)


@dataclass
class Finding:
    check: str
    severity: str  # "error" blocks publish, "warn" does not
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.check}: {self.message}"


def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def lint(text: str) -> list[Finding]:
    findings: list[Finding] = []
    sections = split_sections(text)

    for name in REQUIRED_SECTIONS:
        if name not in sections:
            findings.append(Finding("schema", "error", f"missing required section '## {name}'"))

    for name, lines in sections.items():
        body = [l for l in lines if l.strip()]
        if len(body) > SECTION_BUDGET_LINES:
            findings.append(
                Finding("length", "error", f"section '{name}' is {len(body)} lines (budget {SECTION_BUDGET_LINES}) — summarize harder")
            )
        claims = [l for l in body if l.strip().startswith(("-", "*"))]
        unlabeled = [l for l in claims if not STRATUM_RE.match(l)]
        if claims and unlabeled:
            findings.append(
                Finding("stratum", "error", f"section '{name}': {len(unlabeled)} claim(s) without an [observation]/[inference] label")
            )
        for l in claims:
            m = STRATUM_RE.match(l)
            if m and m.group(1).lower() == "inference" and not CITATION_RE.search(l):
                findings.append(
                    Finding("citation", "warn", f"section '{name}': inference cites no observation: {l.strip()[:80]}")
                )
    return findings


def gate(text: str) -> tuple[bool, list[Finding]]:
    findings = lint(text)
    ok = not any(f.severity == "error" for f in findings)
    return ok, findings
