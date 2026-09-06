"""Turn a branch report's Standing into a tracker update a person can post.

The report is written for the ticket page; a tracker comment is read by
people who will never open TicketBunker. This module rewrites the Standing
verdict, Next, Ask, and Checklist into plain Markdown: trust tags and
grounding ranks are dropped, source references become ``path:line``, and
nothing is added that the report did not say. The user edits and posts it;
nothing is sent by this module.
"""

from __future__ import annotations

import re
from typing import Any

CLAIM_RE = re.compile(
    r"^\s*[-*]\s*\[(observation|real|speculation|inference)\]\s*"
    r"(?:(?:grounded|adjacent|ungrounded)\s*(?:[—–:-]\s*)?)?",
    re.IGNORECASE,
)
VERDICT_RE = re.compile(r"^Verdict:\s*(not started|in progress|blocked|looks done)\s*(?:[—–:-]\s*)?", re.IGNORECASE)
ASPECT_RE = re.compile(r"^(Risk|Next|Ask|Done|Partial|Missing|Unclear|Defect):\s*", re.IGNORECASE)
BRACKET_REF_RE = re.compile(r"@\[([^\]\n]+)\]:(\d+)")
MENTION_REF_RE = re.compile(r"(^|[\s(`])@([A-Za-z0-9_./-]+)")
VERDICT_LABELS = {
    "not started": "Not started",
    "in progress": "In progress",
    "blocked": "Blocked",
    "looks done": "Looks done",
}
MARKS = {"done": "✅", "partial": "🟡", "missing": "❌", "unclear": "❔"}


def _part(text: str, parent: str, heading: str) -> list[str]:
    """Claim lines of one ``### heading`` under ``## parent``."""
    inside_parent = False
    inside = False
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            inside_parent = line[3:].strip().casefold() == parent.casefold()
            inside = False
            continue
        if inside_parent and line.startswith("### "):
            inside = line[4:].strip().casefold() == heading.casefold()
            continue
        if inside and re.match(r"^\s*[-*]\s", line):
            lines.append(line)
    return lines


def _section_claims(text: str, heading: str) -> list[str]:
    """Claim lines of one top-level ``## heading``."""
    inside = False
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            inside = line[3:].strip().casefold() == heading.casefold()
            continue
        if inside and re.match(r"^\s*[-*]\s", line):
            lines.append(line)
    return lines


def plain(claim: str) -> str:
    """One claim as prose: no tag, no rank, references as path:line."""
    body = CLAIM_RE.sub("", claim, count=1).strip()
    body = BRACKET_REF_RE.sub(lambda m: f"`{m.group(1)}:{m.group(2)}`", body)
    body = MENTION_REF_RE.sub(lambda m: f"{m.group(1)}`{m.group(2)}`", body)
    body = body.replace("``", "`")
    return body


def compose(report_text: str, *, ticket_id: str, branch: str, commit: str = "") -> dict[str, Any]:
    """A tracker-ready update from the report's Standing and Checklist.

    Returns the Markdown ``text`` plus the parts it was built from so a page
    can show them separately. An empty ``text`` means the report has no
    Standing and no Checklist to speak from.
    """
    standing = _part(report_text, "Reader brief", "Standing")
    checklist = _part(report_text, "Reader brief", "Checklist")
    verdict = ""
    finding = ""
    next_step = ""
    ask = ""
    for claim in standing:
        body = plain(claim)
        matched = VERDICT_RE.match(body)
        if matched and not verdict:
            verdict = matched.group(1).lower()
            finding = body[matched.end():].strip()
            continue
        aspect = ASPECT_RE.match(body)
        if aspect and aspect.group(1).lower() == "next" and not next_step:
            next_step = body[aspect.end():].strip()
        elif aspect and aspect.group(1).lower() == "ask" and not ask:
            ask = body[aspect.end():].strip()
    defects: list[str] = []
    for claim in [*_part(report_text, "Reader brief", "What"), *_section_claims(report_text, "What")]:
        body = plain(claim)
        aspect = ASPECT_RE.match(body)
        if aspect and aspect.group(1).lower() == "defect":
            text_value = body[aspect.end():].strip()
            if text_value not in defects:
                defects.append(text_value)
    items: list[dict[str, str]] = []
    for claim in checklist:
        body = plain(claim)
        aspect = ASPECT_RE.match(body)
        if not aspect:
            continue
        items.append({"mark": aspect.group(1).lower(), "text": body[aspect.end():].strip()})
    lines: list[str] = []
    if verdict:
        lines.append(f"**{VERDICT_LABELS.get(verdict, verdict)}** — {finding}" if finding else f"**{VERDICT_LABELS.get(verdict, verdict)}**")
    if next_step:
        lines.append(f"**Next:** {next_step}")
    if ask:
        lines.append(f"**Ask:** {ask}")
    if defects:
        lines.append("")
        lines.append("**Defects found:**")
        lines.extend(f"- {defect}" for defect in defects)
    if items:
        lines.append("")
        for item in items:
            lines.append(f"- {MARKS.get(item['mark'], '•')} {item['text']}")
    if lines:
        lines.append("")
        where = f"`{branch}`" + (f" at `{commit[:12]}`" if commit else "")
        lines.append(f"_Synthesized by TicketBunker for {ticket_id} on {where}._")
    return {
        "text": "\n".join(lines).strip(),
        "verdict": verdict,
        "finding": finding,
        "next": next_step,
        "ask": ask,
        "defects": defects,
        "checklist": items,
    }


__all__ = ["compose", "plain"]
