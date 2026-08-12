"""Publish-time anchor verification (DESIGN.md §3.6).

The lint checks that [real] claims are *formatted* like truth; this checks
they are anchored to something that exists. Every anchor a [real] claim
names must resolve — in the source repo at the branch or baseline ref, or
inside the bunker itself (observation files) — and a cited line number must
fall inside the file. A claim whose anchors all fail is rejected at the
gate, closing the "formatted like truth vs checked like truth" gap.

This proves existence, not meaning: an anchor that resolves can still be
cited dishonestly. That residue is what report grading is for.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# An anchor as reports write them: `path` or `path:line`, where path has a
# slash or an extension. Backticks optional. Matches the forms the report
# instructions demand and the lint's PATHLIKE_RE accepts.
ANCHOR_RE = re.compile(r"`?(?P<path>[A-Za-z0-9_.-]*/[A-Za-z0-9_./-]+|[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,8})(?::(?P<line>\d+))?`?")
REAL_LINE_RE = re.compile(r"^\s*[-*]\s*\[real\]", re.IGNORECASE)


def extract_real_anchors(text: str) -> list[tuple[str, str, int | None]]:
    """(claim_excerpt, path, line) for every anchor on every [real] claim."""
    out = []
    for raw in text.splitlines():
        if not REAL_LINE_RE.match(raw):
            continue
        excerpt = raw.strip()[:80]
        for m in ANCHOR_RE.finditer(raw):
            line = int(m.group("line")) if m.group("line") else None
            out.append((excerpt, m.group("path"), line))
    return out


def _file_at_ref(repo: Path, ref: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"],
        capture_output=True, text=True,
    )
    return proc.stdout if proc.returncode == 0 else None


def _resolves(repo: Path, refs: list[str], bunker: Path, path: str, line: int | None) -> str | None:
    """The place this anchor resolved, or None. Checks the repo at each ref,
    then the bunker's own files (observation anchors are bunker-relative)."""
    clean = path.rstrip("/")
    for ref in refs:
        content = _file_at_ref(repo, ref, clean)
        if content is None:
            # A directory anchor (ticketbunker/types/) is a tree, not a blob.
            proc = subprocess.run(
                ["git", "-C", str(repo), "cat-file", "-e", f"{ref}:{clean}"],
                capture_output=True,
            )
            if proc.returncode == 0:
                return ref
            continue
        if line is None or line <= content.count("\n") + 1:
            return ref
    local = bunker / clean
    if local.exists():
        if line is None or not local.is_file():
            return "bunker"
        try:
            if line <= local.read_text(encoding="utf-8").count("\n") + 1:
                return "bunker"
        except (OSError, UnicodeDecodeError):
            return None
    return None


def verify(text: str, repo: Path, refs: list[str], bunker: Path) -> list[str]:
    """Errors for every [real] claim none of whose anchors resolve. A claim
    with at least one live anchor passes — extra context references (words
    that merely look pathlike) must not sink a claim its real anchor holds."""
    errors = []
    by_claim: dict[str, list[tuple[str, int | None]]] = {}
    for excerpt, path, line in extract_real_anchors(text):
        by_claim.setdefault(excerpt, []).append((path, line))
    for excerpt, anchors in by_claim.items():
        results = [(p, l, _resolves(repo, refs, bunker, p, l)) for p, l in anchors]
        if not any(r for _, _, r in results):
            detail = ", ".join(f"{p}:{l}" if l else p for p, l, _ in results)
            errors.append(
                f"[real] claim has no resolvable anchor (checked {', '.join(refs)} and the bunker): "
                f"{detail} — in: {excerpt}"
            )
    return errors


VERIFIED_RE = re.compile(r"^<!-- tb:verified .*-->\n?", re.MULTILINE)


def annotate(text: str, repo: Path, ref: str, ts: str) -> str:
    """Stamp the component with the ref its anchors were verified against —
    mechanical provenance the master surfaces, never agent-authored."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short", ref],
        capture_output=True, text=True,
    )
    sha = proc.stdout.strip() if proc.returncode == 0 else "unknown"
    stamp = f"<!-- tb:verified branch={ref} commit={sha} ts={ts} -->\n"
    return stamp + VERIFIED_RE.sub("", text)


def verified_stamp(text: str) -> dict:
    """The tb:verified fields of a component, {} when never verified."""
    m = re.search(r"<!-- tb:verified (.*?) -->", text)
    if not m:
        return {}
    return dict(kv.split("=", 1) for kv in m.group(1).split() if "=" in kv)


def predates(stamp_ts: str, recency: str) -> bool:
    """True when development moved after the component was verified. ISO
    timestamps may carry different UTC offsets (git author dates are local),
    so compare instants, never strings."""
    import datetime

    try:
        verified = datetime.datetime.fromisoformat(stamp_ts)
        latest = datetime.datetime.fromisoformat(recency)
    except (ValueError, TypeError):
        return False
    if verified.tzinfo is None or latest.tzinfo is None:
        return False
    return latest > verified
