"""Session context composition (DESIGN.md §3.9).

tb.md is composed deterministically from separate fragments under
<bunker>/context/, concatenated in filename order, each bracketed by markers
carrying its filename and content digest. One file at the bunker root shows a
user exactly what is prepended to a session.
"""

from __future__ import annotations

from pathlib import Path

from .ledger import Ledger, digest

FRAGMENT_GLOB = "*.md"
BEGIN = "<!-- tb:fragment {name} {digest} -->"
END = "<!-- tb:end {name} -->"


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
        manifest.append({"fragment": frag.name, "digest": d, "bytes": len(content.encode())})
        parts.append(BEGIN.format(name=frag.name, digest=d))
        parts.append(content)
        parts.append(END.format(name=frag.name))
        parts.append("")
    Path(out_path).write_text("\n".join(parts), encoding="utf-8")
    if ledger is not None:
        ledger.event("compose", out=str(out_path), fragments=manifest)
    return manifest
