"""The inference store (DESIGN.md §3.3).

Agent working inferences, tagged real vs. speculation, persisted under
<bunker>/inferences/ as append-only JSONL. Agent-facing raw material: the
user reads the report, not this — but the report is composed from it, and
audit can trace a report claim back through the store to the ledger.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from .ledger import Ledger

TAGS = ("real", "speculation")
GROUNDINGS = ("grounded", "adjacent", "ungrounded")


def save(
    bunker: Path,
    tag: str,
    claim: str,
    ledger: Ledger,
    grounding: str | None = None,
    anchors: list[str] | None = None,
) -> dict:
    """Persist one tagged inference. Speculation requires a grounding rank;
    real requires at least one anchor (a path/line the reader can check)."""
    if tag not in TAGS:
        raise ValueError(f"tag must be one of {TAGS}, got {tag!r}")
    if tag == "speculation" and grounding not in GROUNDINGS:
        raise ValueError(f"speculation requires grounding in {GROUNDINGS}, got {grounding!r}")
    if tag == "real" and not anchors:
        raise ValueError("a real inference requires at least one anchor (path or path:line)")

    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "tag": tag,
        "claim": claim,
        "grounding": grounding,
        "anchors": anchors or [],
    }
    path = Path(bunker) / "inferences" / "inferences.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    ledger.event("inference_saved", tag=tag, grounding=grounding, anchors=anchors or [])
    return entry


def load(bunker: Path) -> list[dict]:
    path = Path(bunker) / "inferences" / "inferences.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
