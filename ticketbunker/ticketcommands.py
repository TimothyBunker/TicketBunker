"""Provider-neutral ticket commands shared by CLI and HTTP transports."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import commands, handlers, layout, project, ticketindex, types
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


_LOCAL_FIELDS = {"assigned_branches", "created", "key", "repo", "updated"}
_MECHANICAL_FIELDS = {"created", "updated"}


def _read_ticket(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise commands.CommandRejected("ticket_invalid", f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise commands.CommandRejected("ticket_invalid", f"{path} must contain one ticket object.")
    return value


def _indexed(source_root: Path, project_key: str, ticket_id: str) -> bool:
    indexed = ticketindex.read(project.tickets_dir(source_root, project_key))
    return indexed is not None and any(
        isinstance(value, dict) and value.get("id") == ticket_id
        for value in indexed["tickets"]
    )


def _candidate(
    context: commands.CommandContext,
    incoming: dict[str, Any],
) -> tuple[str, Path, dict[str, Any], str]:
    ticket_id = str(incoming.get("id") or "").strip()
    if not ticket_id:
        raise commands.CommandRejected("ticket_id_required", "Ticket synchronization requires an id.")
    locator = project.load_locator(context.project_root)
    project_key = str((locator or {}).get("project") or "")
    bunker = layout.bunker_path(context.project_root, ticket_id)
    if not bunker.is_dir() and locator is not None and _indexed(context.project_root, project_key, ticket_id):
        bunker = project.materialize_ticket(context.project_root, project_key, ticket_id)

    old = _read_ticket(layout.paths(bunker)["ticket"]) if bunker.is_dir() else {}
    candidate = {**old, **incoming}
    if old:
        for field in _LOCAL_FIELDS:
            if field in old:
                candidate[field] = old[field]
        old_metadata = old.get("metadata")
        new_metadata = incoming.get("metadata")
        if isinstance(old_metadata, dict) and isinstance(new_metadata, dict):
            candidate["metadata"] = {**old_metadata, **new_metadata}
    candidate.setdefault("key", ticket_id)
    candidate.setdefault("type", "task")
    candidate.setdefault("status", "todo")
    candidate.setdefault("people_of_interest", [])
    candidate.setdefault("references", [])
    candidate.setdefault("labels", [])

    errors = types.validate("ticket", candidate)
    if errors:
        raise commands.CommandRejected("ticket_rejected", "ticket rejected: " + "; ".join(errors))
    if not old:
        return ticket_id, bunker, candidate, "created"
    meaningful_old = {key: value for key, value in old.items() if key not in _MECHANICAL_FIELDS}
    meaningful_new = {key: value for key, value in candidate.items() if key not in _MECHANICAL_FIELDS}
    return ticket_id, bunker, candidate, "unchanged" if meaningful_old == meaningful_new else "updated"


def _sync_tickets(
    context: commands.CommandContext,
    tickets: list[dict[str, Any]],
    *,
    intake: str,
    publish: bool,
) -> dict[str, Any]:
    if not tickets:
        raise commands.CommandRejected("tickets_required", "Ticket synchronization requires at least one ticket.")
    prepared = [_candidate(context, ticket) for ticket in tickets]
    ids = [ticket_id for ticket_id, _bunker, _candidate_value, _action in prepared]
    if len(ids) != len(set(ids)):
        raise commands.CommandRejected("ticket_duplicate", "A synchronization batch cannot repeat a ticket id.")

    results = []
    for ticket_id, bunker, candidate, action in prepared:
        if action != "unchanged":
            locator = project.load_locator(context.project_root)
            if action == "created" and locator is not None:
                project.include_ticket_path(
                    context.project_root,
                    str(locator.get("project") or ""),
                    ticket_id,
                )
            if action == "created":
                layout.init_dirs(bunker)
            ledger = _ledger(bunker)
            if action == "created":
                ledger.event(
                    "bunker_init",
                    ticket_id=ticket_id,
                    ticket_key=candidate.get("key"),
                    intake=intake,
                )
            handlers.intake_ticket(bunker, candidate, ledger)
            _compose(bunker, ledger)
        results.append({"ticket": ticket_id, "action": action, "bunker": str(bunker)})

    changed = [value for value in results if value["action"] != "unchanged"]
    publication = (
        project.publish_data(
            context.project_root,
            f"Synchronize {len(changed)} ticket{'s' if len(changed) != 1 else ''}",
        )
        if publish and changed
        else {"published": False}
    )
    return {
        "tickets": results,
        "created": sum(value["action"] == "created" for value in results),
        "updated": sum(value["action"] == "updated" for value in results),
        "unchanged": sum(value["action"] == "unchanged" for value in results),
        "publication": publication,
    }


@commands.register("ticket.upsert")
def command_ticket_upsert(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    ticket = payload.get("ticket")
    if not isinstance(ticket, dict):
        raise commands.CommandRejected("ticket_payload_required", "Ticket synchronization requires one ticket object.")
    result = _sync_tickets(
        context,
        [ticket],
        intake=str(payload.get("intake") or context.transport),
        publish=bool(payload.get("publish", True)),
    )
    return {**result, **result["tickets"][0]}


@commands.register("ticket.sync")
def command_ticket_sync(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    tickets = payload.get("tickets")
    if not isinstance(tickets, list) or not all(isinstance(value, dict) for value in tickets):
        raise commands.CommandRejected("tickets_required", "Ticket synchronization requires a list of ticket objects.")
    return _sync_tickets(
        context,
        tickets,
        intake=str(payload.get("intake") or context.transport),
        publish=bool(payload.get("publish", True)),
    )
