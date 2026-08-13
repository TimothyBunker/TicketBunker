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
# Tags: observation (pipeline fact) / real (agent-located, checkable) /
# speculation (interpretation, ranked by grounding). [inference] is accepted
# as a deprecated alias of [speculation].
STRATUM_RE = re.compile(r"^\s*[-*]?\s*\[(observation|real|speculation|inference)\]", re.IGNORECASE)
CITATION_RE = re.compile(r"\(obs:|observations?/|merge[-_]base|diff|authorship|merge[-_]history|work[-_]separation|branch[- _]resolution", re.IGNORECASE)
PATHLIKE_RE = re.compile(r"[\w.-]+/[\w./-]+|[\w-]+\.\w{1,8}(:\d+)?")
GROUNDING_RE = re.compile(r"\b(grounded|adjacent|ungrounded)\b", re.IGNORECASE)


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
                Finding("stratum", "error", f"section '{name}': {len(unlabeled)} claim(s) without an [observation]/[real]/[speculation] label")
            )
        for l in claims:
            m = STRATUM_RE.match(l)
            if not m:
                continue
            tag = m.group(1).lower()
            if tag == "real" and not PATHLIKE_RE.search(l):
                findings.append(
                    Finding("real-anchor", "error", f"section '{name}': [real] claim names no path/line to check: {l.strip()[:80]}")
                )
            if tag in ("speculation", "inference"):
                if not GROUNDING_RE.search(l):
                    findings.append(
                        Finding("grounding", "error", f"section '{name}': speculation without a grounding rank (grounded/adjacent/ungrounded): {l.strip()[:80]}")
                    )
                elif not (CITATION_RE.search(l) or PATHLIKE_RE.search(l)) and "ungrounded" not in l.lower():
                    findings.append(
                        Finding("citation", "warn", f"section '{name}': grounded/adjacent speculation cites nothing checkable: {l.strip()[:80]}")
                    )
    return findings


def gate(text: str) -> tuple[bool, list[Finding]]:
    findings = lint(text)
    ok = not any(f.severity == "error" for f in findings)
    return ok, findings


# --- tbroot.md lint (DESIGN.md §3.10) -------------------------------------
# tbroot.md is consumed as ground truth by every agent in the project, so
# only real observations are allowed — no conjecture, however certain. The
# lint can't prove claims true, but it refuses the form conjecture takes.

CONJECTURE_RE = re.compile(
    r"\b(probably|likely|seems?|appears?|presumably|maybe|perhaps|"
    r"might|could be|i think|i believe|possibly|should be|expect(?:ed)?s? to)\b",
    re.IGNORECASE,
)
ROOT_MAX_LINES = 200


def lint_root(text: str) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.splitlines()
    in_comment = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if stripped.startswith("<!--") and "-->" not in stripped:
            in_comment = True
            continue
        if not stripped or stripped.startswith(("#", "<!--")):
            continue
        m = CONJECTURE_RE.search(stripped)
        if m:
            findings.append(
                Finding("conjecture", "error", f"line {i}: conjecture language ('{m.group(0)}') — tbroot.md is observations only: {stripped[:70]}")
            )
        if stripped.startswith(("-", "*")) and not PATHLIKE_RE.search(stripped):
            findings.append(
                Finding("anchor", "warn", f"line {i}: claim has no checkable anchor (path/file/command): {stripped[:70]}")
            )
    body = [l for l in lines if l.strip()]
    if len(body) > ROOT_MAX_LINES:
        findings.append(
            Finding("length", "error", f"tbroot.md is {len(body)} lines (max {ROOT_MAX_LINES}) — concise, proportional to project size")
        )
    return findings


def gate_root(text: str) -> tuple[bool, list[Finding]]:
    findings = lint_root(text)
    ok = not any(f.severity == "error" for f in findings)
    return ok, findings
