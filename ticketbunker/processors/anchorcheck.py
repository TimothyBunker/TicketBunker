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
import urllib.parse
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


CLAIM_LINE_RE = re.compile(r"^\s*[-*]\s*\[(observation|real|speculation|inference)\]", re.IGNORECASE)
# Bunker-internal citations are shared between components by construction
# and carry no overlap signal.
_BUNKER_INTERNAL = ("observations/", "inferences/", "reports/", "sessions/", "context/")


def claim_paths(text: str) -> set[str]:
    """Repo paths named by any tagged claim — the mechanical candidate
    signal for 'these components talk about the same code'. A candidate,
    not a verdict: deciding disagreement is the merge session's job."""
    out = set()
    for raw in text.splitlines():
        if not CLAIM_LINE_RE.match(raw):
            continue
        for m in ANCHOR_RE.finditer(raw):
            path = m.group("path").rstrip("/")
            if path.startswith(_BUNKER_INTERNAL) or "_bunker" in path:
                continue
            if not re.search(r"[A-Za-z]", path):
                continue  # diff-stat noise like "+1/-1", not a path
            out.add(path)
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
    relative = _bunker_relative(bunker, clean)
    if not _citable(relative):
        return None
    local = bunker / relative
    if local.exists():
        if line is None or not local.is_file():
            return "bunker"
        try:
            if line <= local.read_text(encoding="utf-8").count("\n") + 1:
                return "bunker"
        except (OSError, UnicodeDecodeError):
            return None
    return None


# Bunker files an anchor may rest on: the ticket record and the pipeline's
# mechanical output. The form the agent is writing, its transcript, the
# composed prompt, and the context fragments are the agent's own working
# material and never count as evidence for a claim.
_CITABLE_PREFIXES = ("observations/", "inferences/", "reports/")
_CITABLE_FILES = ("ticket.json",)


def _citable(relative: str) -> bool:
    return relative in _CITABLE_FILES or relative.startswith(_CITABLE_PREFIXES)


def _bunker_relative(bunker: Path, path: str) -> str:
    """Normalize a bunker citation to the bunker's own layout.

    Agents see the bunker's absolute location and sometimes cite
    ``runtime/ECS-4_bunker/ticket.json`` or ``.tb/ECS-4_bunker/ticket.json``
    instead of ``ticket.json``. Anything up to and including the bunker's
    directory name is a prefix, not part of the anchor.
    """
    absolute = str(Path(bunker).resolve())
    if path.startswith(absolute + "/"):
        return path[len(absolute) + 1 :]
    parts = path.split("/")
    name = Path(bunker).name
    if name in parts:
        return "/".join(parts[parts.index(name) + 1 :])
    if parts and parts[0] == ".tb":
        return "/".join(parts[1:])
    return path


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


def annotate(
    text: str,
    repo: Path,
    ref: str,
    ts: str,
    generation: dict[str, str] | None = None,
) -> str:
    """Stamp the component with the ref its anchors were verified against —
    plus the known harness identity — mechanical provenance the master and
    read model surface, never agent-authored."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short", ref],
        capture_output=True, text=True,
    )
    sha = proc.stdout.strip() if proc.returncode == 0 else "unknown"
    fields = {"branch": ref, "commit": sha, "ts": ts}
    for key in ("harness", "provider", "model"):
        if generation and generation.get(key):
            fields[key] = str(generation[key])
    encoded = " ".join(
        f"{key}={urllib.parse.quote(str(value), safe='/:.+_-')}" for key, value in fields.items()
    )
    stamp = f"<!-- tb:verified {encoded} -->\n"
    return stamp + VERIFIED_RE.sub("", text)


def verified_stamp(text: str) -> dict:
    """The tb:verified fields of a component, {} when never verified."""
    m = re.search(r"<!-- tb:verified (.*?) -->", text)
    if not m:
        return {}
    return {
        key: urllib.parse.unquote(value)
        for key, value in (kv.split("=", 1) for kv in m.group(1).split() if "=" in kv)
    }


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
