"""Master report regeneration (DESIGN.md §3.4).

Full regeneration, never incremental: the master is always rebuilt from the
current set of child components, ordered by the ticket's branch order
(recency of development, or priority when assigned), and records each child's
digest so master-vs-child staleness is mechanically detectable. Assembly is
mechanical — the master inherits, never re-invents, the children's claims.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from ..ledger import Ledger, digest


def _branch_order(bunker: Path) -> dict[str, str]:
    """slug -> recency_of_development, from the authorship observation."""
    obs = bunker / "observations" / "authorship.json"
    if not obs.exists():
        return {}
    per_branch = json.loads(obs.read_text(encoding="utf-8")).get("per_branch", {})
    return {
        branch.replace("/", "__"): (info.get("recency_of_development") or "")
        for branch, info in per_branch.items()
    }


def _section(text: str, name: str) -> list[str]:
    """The lines of one `## name` section, verbatim — selection, never
    rewriting."""
    lines, keep = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            keep = line[3:].strip().lower() == name.lower()
            continue
        if keep and line.strip():
            lines.append(line)
    return lines


def regenerate(bunker: Path, ledger: Ledger | None = None) -> Path:
    bunker = Path(bunker)
    children_dir = bunker / "reports" / "branches"
    out = bunker / "reports" / "master.md"
    children = sorted(children_dir.glob("*.md")) if children_dir.exists() else []

    recency = _branch_order(bunker)
    children.sort(key=lambda p: recency.get(p.stem, ""), reverse=True)

    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    manifest = [{"child": c.name, "digest": digest(c.read_text(encoding="utf-8"))} for c in children]

    parts = [
        "<!-- master report — regenerated mechanically from reports/branches/. Do not edit. -->",
        f"<!-- built: {ts} -->",
    ]
    for m in manifest:
        parts.append(f"<!-- child: {m['child']} {m['digest']} -->")
    parts += ["", "# Master report", ""]

    if not children:
        parts.append("_No branch report components yet._")
    else:
        parts.append(f"{len(children)} branch component(s), ordered by recency of development:")
        parts.append("")
        for c in children:
            parts.append(f"- {c.stem.replace('__', '/')} — last development {recency.get(c.stem, 'unknown')}")
        parts.append("")
        # The essentials, selected mechanically (each child's State section,
        # claims verbatim) so a reader gets the whole ticket without scrolling
        # the full components below.
        parts += ["## Digest", ""]
        for c in children:
            parts.append(f"**{c.stem.replace('__', '/')}**")
            state = _section(c.read_text(encoding="utf-8"), "State")
            parts += state if state else ["- _component has no State section_"]
            parts.append("")
        for c in children:
            parts.append("---")
            parts.append("")
            parts.append(f"# Branch: {c.stem.replace('__', '/')}")
            parts.append("")
            parts.append(c.read_text(encoding="utf-8").rstrip())
            parts.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    if ledger is not None:
        ledger.event("master_regenerated", out=str(out), children=manifest)
    return out


def stale_children(bunker: Path) -> list[str]:
    """Children whose current digest differs from what the master recorded."""
    out = Path(bunker) / "reports" / "master.md"
    if not out.exists():
        return []
    recorded: dict[str, str] = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        if line.startswith("<!-- child: "):
            fields = line.split()  # ['<!--', 'child:', name, digest, '-->']
            if len(fields) >= 4:
                recorded[fields[2]] = fields[3]
    stale = []
    children_dir = Path(bunker) / "reports" / "branches"
    current = {c.name: digest(c.read_text(encoding="utf-8")) for c in children_dir.glob("*.md")} if children_dir.exists() else {}
    for name, d in current.items():
        if recorded.get(name) != d:
            stale.append(name)
    for name in recorded:
        if name not in current:
            stale.append(name + " (deleted)")
    return stale
