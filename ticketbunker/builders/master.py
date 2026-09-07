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

from .. import layout
from ..ledger import Ledger, digest


def _branch_order(bunker: Path) -> dict[str, str]:
    """slug -> recency_of_development, from the authorship observation."""
    obs = layout.paths(bunker)["authorship"]
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


def from_records(ticket_id: str, reports: list[dict]) -> str:
    """Pure Bunker aggregate, preserving repository identity and child text.

    Unlike a runtime's master, this includes accepted children from every
    repository. No local files or model calls participate in the shared view.
    """
    children = [value for value in reports if str(value.get("scope") or "").startswith("branch:")]
    children.sort(key=lambda value: (str(value.get("repository_id") or ""), str(value["scope"])))
    parts = ["<!-- master report — regenerated mechanically from accepted Bunker children. Do not edit. -->",
             "# Master report", "", f"Ticket: {ticket_id}", "", "## Digest", ""]
    for child in children:
        label = f"{child.get('repository_id') or 'Imported repository'} / {child['scope'][7:]}"
        parts.extend([f"**{label}**", *_section(str(child["content"]), "Reader brief"),
                      *_section(str(child["content"]), "State"), ""])
    for child in children:
        label = f"{child.get('repository_id') or 'Imported repository'} / {child['scope'][7:]}"
        content = str(child["content"])
        parts.extend(["---", f"# Branch: {label}",
                      f"<!-- child-digest: {digest(content)} -->", "", content.rstrip(), ""])
    return "\n".join(parts) + "\n"


def regenerate(bunker: Path, ledger: Ledger | None = None) -> Path:
    bunker = Path(bunker)
    paths = layout.paths(bunker)
    children_dir = paths["children"]
    out = paths["master"]
    children = sorted(children_dir.glob("*.md")) if children_dir.exists() else []

    recency = _branch_order(bunker)
    children.sort(key=lambda p: recency.get(p.stem, ""), reverse=True)

    from ..processors.anchorcheck import verified_stamp

    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    manifest = []
    for c in children:
        text = c.read_text(encoding="utf-8")
        manifest.append({"child": c.name, "digest": digest(text), "verified": verified_stamp(text)})

    parts = [
        "<!-- master report — regenerated mechanically from reports/branches/. Do not edit. -->",
        f"<!-- built: {ts} -->",
    ]
    for m in manifest:
        v = m["verified"]
        ref = f" branch={v['branch']} commit={v['commit']}" if v.get("commit") else ""
        parts.append(f"<!-- child: {m['child']} {m['digest']}{ref} -->")
    parts += ["", "# Master report", ""]

    if not children:
        parts.append("_No branch report components yet._")
    else:
        parts.append(f"{len(children)} branch component(s), ordered by recency of development:")
        parts.append("")
        for c in children:
            parts.append(f"- {c.stem.replace('__', '/')} — last development {recency.get(c.stem, 'unknown')}")
        parts.append("")
        # The essentials are selected mechanically and copied verbatim. Newer
        # components lead with an agent-authored Reader brief, followed by the
        # mechanical/agent State trail. Legacy components fall back to State.
        parts += ["## Digest", ""]
        for c, m in zip(children, manifest):
            v = m["verified"]
            at = f" — verified at `{v['commit']}` on {v['branch']}" if v.get("commit") else ""
            parts.append(f"**{c.stem.replace('__', '/')}**{at}")
            child_text = c.read_text(encoding="utf-8")
            brief = _section(child_text, "Reader brief")
            state = _section(child_text, "State")
            if brief:
                parts += brief
                parts += ["", "**State**"]
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
    paths = layout.paths(Path(bunker))
    out = paths["master"]
    if not out.exists():
        return []
    recorded: dict[str, str] = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        if line.startswith("<!-- child: "):
            fields = line.split()  # ['<!--', 'child:', name, digest, '-->']
            if len(fields) >= 4:
                recorded[fields[2]] = fields[3]
    stale = []
    children_dir = paths["children"]
    current = {c.name: digest(c.read_text(encoding="utf-8")) for c in children_dir.glob("*.md")} if children_dir.exists() else {}
    for name, d in current.items():
        if recorded.get(name) != d:
            stale.append(name)
    for name in recorded:
        if name not in current:
            stale.append(name + " (deleted)")
    return stale
