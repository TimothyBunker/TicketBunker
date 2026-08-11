"""Session context composition (DESIGN.md §3.9).

tb.md is composed deterministically from separate fragments under
<bunker>/context/, concatenated in filename order, each bracketed by markers
carrying its filename and content digest. One file at the bunker root shows a
user exactly what is prepended to a session.
"""

from __future__ import annotations

from pathlib import Path

from ..ledger import Ledger, digest

FRAGMENT_GLOB = "*.md"
BEGIN = "<!-- tb:fragment {name} {digest} provenance={provenance} -->"
END = "<!-- tb:end {name} -->"

# Data provenance separation (DESIGN.md §4): the agent reads one composed
# document, but every fragment carries its origin, and storage never
# intermingles the types.
PROVENANCE = {
    "00_explanation.md": "ours",
    "10_workflow.md": "ours",
    "15_tbroot.md": "agent-gated",
    "20_ticket.md": "ours",
    "25_session.md": "user",
    "30_observations.md": "pipeline",
}


def provenance_of(name: str) -> str:
    return PROVENANCE.get(name, "unknown")


def compose(context_dir: Path, out_path: Path, ledger: Ledger | None = None) -> list[dict]:
    """Compose context fragments into tb.md. Returns the fragment manifest."""
    fragments = sorted(Path(context_dir).glob(FRAGMENT_GLOB))
    manifest = []
    parts = [
        "<!-- tb.md — composed session context. Do not edit: edit context/ fragments and recompose. -->",
        "",
    ]
    for frag in fragments:
        content = frag.read_text(encoding="utf-8").rstrip()
        d = digest(content)
        prov = provenance_of(frag.name)
        manifest.append({"fragment": frag.name, "digest": d, "bytes": len(content.encode()), "provenance": prov})
        parts.append(BEGIN.format(name=frag.name, digest=d, provenance=prov))
        parts.append(content)
        parts.append(END.format(name=frag.name))
        parts.append("")
    Path(out_path).write_text("\n".join(parts), encoding="utf-8")
    if ledger is not None:
        ledger.event("compose", out=str(out_path), fragments=manifest)
    return manifest
