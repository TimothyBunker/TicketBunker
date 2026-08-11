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


def intake_ticket(bunker: Path, payload: dict, ledger: Ledger) -> Path:
    _gate("ticket", payload, ledger, ticket_id=payload.get("id"))
    dest = layout.paths(bunker)["ticket"]
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return dest


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


def intake_report_component(bunker: Path, branch: str, text: str, ledger: Ledger) -> Path:
    _gate("report_component", {"branch": branch, "text": text}, ledger, branch=branch)
    dest = layout.child_report(bunker, branch)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest
