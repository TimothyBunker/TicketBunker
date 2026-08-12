"""Intake handlers (DESIGN.md §3.12–3.13).

The only write paths into a bunker for typed information. Each handler:
declares its type, validates the payload against the registry, writes to the
layout-defined location, and ledgers the acceptance or rejection. There is no
side channel that writes without the type check — locally these handlers are
the validator; when the backend exists they call it and it holds the same
registry.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from .. import layout, types
from ..ledger import Ledger


class IntakeRejected(ValueError):
    def __init__(self, type_name: str, errors: list[str]):
        super().__init__(f"{type_name} rejected: " + "; ".join(errors))
        self.type_name = type_name
        self.errors = errors


def _gate(type_name: str, payload: dict, ledger: Ledger, **context) -> None:
    errors = types.validate(type_name, payload)
    if errors:
        ledger.event("intake_rejected", info_type=type_name, errors=errors, **context)
        raise IntakeRejected(type_name, errors)
    ledger.event("intake_accepted", info_type=type_name, **context)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# Keys the machinery writes for itself. They never count as a ticket edit:
# every intake is still ledgered, but 'updated' only moves when a field a
# person cares about changed (log everything, show what's relevant).
_MECHANICAL_KEYS = {"created", "updated", "repo"}


def intake_ticket(bunker: Path, payload: dict, ledger: Ledger) -> Path:
    payload = dict(payload)
    dest = layout.paths(bunker)["ticket"]
    old: dict = {}
    if dest.exists():
        try:
            old = json.loads(dest.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
    payload.setdefault("created", old.get("created") or _now())
    meaningful_new = {k: v for k, v in payload.items() if k not in _MECHANICAL_KEYS}
    meaningful_old = {k: v for k, v in old.items() if k not in _MECHANICAL_KEYS}
    if meaningful_new == meaningful_old and old.get("updated"):
        payload["updated"] = old["updated"]
    else:
        payload["updated"] = _now()
    _gate("ticket", payload, ledger, ticket_id=payload.get("id"))
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return dest


def add_comment(bunker: Path, author: str, text: str, ledger: Ledger,
                when: str | None = None, source: str | None = None) -> Path:
    """Carry a tracker comment over onto the ticket, verbatim, keeping the
    original author. Ours provenance: imported context, not agent output."""
    ticket_path = layout.paths(bunker)["ticket"]
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    comment: dict = {"author": author, "text": text, "when": when or _now()}
    if source:
        comment["source"] = source
    ticket["comments"] = list(ticket.get("comments") or []) + [comment]
    return intake_ticket(bunker, ticket, ledger)


def assign_branch(bunker: Path, branch: str, ledger: Ledger) -> Path:
    """Record an explicit branch→ticket assignment on the ticket itself.

    The assignment travels with the repo (it lives in ticket.json, ours
    provenance) and re-enters through the ticket gate like any other edit;
    assigning an already-assigned branch is a no-op re-intake."""
    ticket_path = layout.paths(bunker)["ticket"]
    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    assigned = list(ticket.get("assigned_branches") or [])
    if branch not in assigned:
        assigned.append(branch)
    ticket["assigned_branches"] = assigned
    return intake_ticket(bunker, ticket, ledger)


def intake_session_config(bunker: Path, payload: dict, ledger: Ledger) -> Path:
    _gate("session_config", payload, ledger, branch=payload.get("branch"))
    dest = layout.session_config(bunker, payload["branch"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return dest


def intake_inference(bunker: Path, payload: dict, ledger: Ledger) -> dict:
    _gate("inference", payload, ledger, tag=payload.get("tag"), grounding=payload.get("grounding"))
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "tag": payload["tag"],
        "claim": payload["claim"],
        "grounding": payload.get("grounding"),
        "anchors": payload.get("anchors") or [],
    }
    log = layout.paths(bunker)["inference_log"]
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load_inferences(bunker: Path) -> list[dict]:
    log = layout.paths(bunker)["inference_log"]
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def intake_report_component(bunker: Path, branch: str, text: str, ledger: Ledger,
                            repo: Path | None = None, refs: list[str] | None = None) -> Path:
    if repo is not None and refs:
        from ..processors import anchorcheck

        errors = anchorcheck.verify(text, repo, refs, bunker)
        if errors:
            ledger.event("intake_rejected", info_type="report_component", branch=branch, errors=errors)
            raise IntakeRejected("report_component", errors)
        text = anchorcheck.annotate(text, repo, refs[0], _now())
    _gate("report_component", {"branch": branch, "text": text}, ledger, branch=branch)
    dest = layout.child_report(bunker, branch)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest
