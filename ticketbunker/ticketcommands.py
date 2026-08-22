"""Provider-neutral ticket commands shared by CLI and HTTP transports."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import commands, handlers, layout, project
from .builders import compose, fragments
from .handlers import IntakeRejected
from .ledger import Ledger


def _ledger(bunker: Path) -> Ledger:
    return Ledger(layout.paths(bunker)["ledger"])


def _compose(bunker: Path, ledger: Ledger) -> None:
    paths = layout.paths(bunker)
    ticket = json.loads(paths["ticket"].read_text(encoding="utf-8"))
    fragments.seed_static(paths["context"])
    fragments.write_ticket(paths["context"], ticket)
    fragments.write_session(paths["context"], None)
    fragments.write_merge(paths["context"], False)
    fragments.write_observations(paths["context"], fragments.load_observations(paths["observations"]))
    compose.compose(paths["context"], paths["tb_md"], ledger)


@commands.register("ticket.create")
def command_ticket_create(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    ticket = payload.get("ticket")
    if not isinstance(ticket, dict):
        raise commands.CommandRejected("ticket_payload_required", "Ticket creation requires one ticket object.")
    ticket_id = str(ticket.get("id") or "").strip()
    if not ticket_id:
        raise commands.CommandRejected("ticket_id_required", "Ticket creation requires an id.")
    custom_root = payload.get("bunker_root")
    if custom_root:
        bunker = Path(str(custom_root)).expanduser().resolve() / f"{ticket_id}_bunker"
    else:
        bunker = layout.bunker_path(context.project_root, ticket_id)
    if bunker.exists():
        raise commands.CommandRejected("ticket_exists", f"{bunker} already exists — ticket IDs must be unique.")

    locator = project.load_locator(context.project_root)
    if locator is not None and not custom_root:
        project.include_ticket_path(
            context.project_root,
            str(locator.get("project") or ""),
            ticket_id,
        )

    layout.init_dirs(bunker)
    ledger = _ledger(bunker)
    ledger.event(
        "bunker_init",
        ticket_id=ticket_id,
        ticket_key=ticket.get("key"),
        intake=str(payload.get("intake") or context.transport),
    )
    try:
        handlers.intake_ticket(bunker, ticket, ledger)
    except IntakeRejected as exc:
        shutil.rmtree(bunker)
        raise commands.CommandRejected("ticket_rejected", str(exc)) from exc
    _compose(bunker, ledger)
    publication = project.publish_data(context.project_root, f"Create ticket {ticket_id}")
    return {"ticket": ticket_id, "bunker": str(bunker), "publication": publication}
