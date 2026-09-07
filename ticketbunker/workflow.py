"""Safe ticket-to-branch workflows shared by CLI and localhost actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import commands, config, handlers, layout, project
from .adapters.gitrun import GitError, git, repository_lock
from .api import TicketCatalog
from .ledger import Ledger
from .processors.stages import relate_branches


def _work_ledger(source_root: Path) -> Ledger:
    return Ledger(source_root / ".git" / "ticketbunker-work.jsonl")


def _ticket(source_root: Path, ticket_id: str, bunker_id: str = "") -> tuple[dict[str, Any], Path, str]:
    from . import bunkerdb, bunkerruntime

    if not bunker_id:
        found = bunkerdb.find_bunker_ticket(source_root, f"work/{ticket_id}")
        bunker_id = str((found or {}).get("bunker_id") or "")
    if bunker_id:
        try:
            info = bunkerruntime.materialize(bunker_id, ticket_id, repo=source_root)
        except bunkerdb.BunkerDatabaseError as exc:
            raise commands.CommandRejected("repository_unavailable", str(exc)) from exc
        payload = json.loads(layout.paths(info["bunker"])["ticket"].read_text(encoding="utf-8"))
        return payload, info["bunker"], info["project_key"]
    locator = project.load_locator(source_root)
    if locator is None:
        raise commands.CommandRejected("not_initialized", "Run 'tb init' before starting ticket work.")
    project_key = str(locator.get("project") or "")
    try:
        snapshot = TicketCatalog(source_root).get_ticket(project_key, ticket_id)
    except KeyError as exc:
        raise commands.CommandRejected(
            "ticket_missing",
            f"Ticket {ticket_id} is not in project {project_key}. Run 'tb init' and choose an indexed ticket.",
        ) from exc
    return dict(snapshot.data), snapshot.bunker, project_key


def _project_document(source_root: Path, project_key: str, bunker: Path | None = None) -> dict[str, Any]:
    from . import bunkerruntime

    info = bunkerruntime.marker(bunker) if bunker else None
    if info:
        return {"base_branch": info["base_branch"]}
    path = project.project_config_dir(source_root, project_key) / "project.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise commands.CommandRejected("project_config_invalid", f"Cannot read {path}: {exc}") from exc
    return value if isinstance(value, dict) else {}


def _run(repo: Path, args: list[str], ledger: Ledger, stage: str, message: str) -> str:
    try:
        return git(repo, args, ledger, stage)
    except GitError as exc:
        detail = exc.stderr.strip() or "Git rejected the operation"
        raise commands.CommandRejected("git_workflow_failed", f"{message}\nGit said: {detail}") from exc


def state(source_root: Path, ticket_id: str, bunker_id: str = "") -> dict[str, Any]:
    """Return current local work choices without mutating repository state."""
    source_root = Path(source_root).resolve()
    ticket, _bunker, project_key = _ticket(source_root, ticket_id, bunker_id)
    ledger = Ledger(layout.paths(_bunker)["ledger"])
    branches = [
        value.strip()
        for value in _run(
            source_root,
            ["branch", "--format=%(refname:short)"],
            ledger,
            "work_state",
            "TicketBunker could not list local branches. Run 'tb doctor'.",
        ).splitlines()
        if value.strip()
    ]
    remotes = [
        value.strip()
        for value in _run(
            source_root,
            ["branch", "--remotes", "--format=%(refname:short)"],
            ledger,
            "work_state",
            "TicketBunker could not list remote branches. Run 'tb doctor'.",
        ).splitlines()
        if value.strip() and not value.strip().endswith("/HEAD")
    ]
    document = _project_document(source_root, project_key, _bunker)
    configured_base = str(document.get("base_branch") or "main")
    available = list(branches)
    available.extend(value.removeprefix("origin/") for value in remotes if value.startswith("origin/"))
    bases = sorted(set(available), key=str.casefold)
    if configured_base in bases:
        bases.remove(configured_base)
        bases.insert(0, configured_base)
    current = _run(
        source_root,
        ["branch", "--show-current"],
        ledger,
        "work_state",
        "TicketBunker could not read the current branch. Run 'tb doctor'.",
    ).strip()
    dirty = _run(
        source_root,
        ["status", "--porcelain", "--untracked-files=all"],
        ledger,
        "work_state",
        "TicketBunker could not inspect the working tree. Run 'tb doctor'.",
    ).strip()
    related, _rules, _missing = relate_branches([*branches, *remotes], str(ticket.get("key") or ticket_id), [])
    return {
        "ticket": ticket_id,
        "project": project_key,
        "current_branch": current,
        "base_branch": configured_base,
        "base_branches": bases,
        "related_branches": related,
        "clean": not bool(dirty),
        "dirty_paths": [line[3:] for line in dirty.splitlines()[:20]],
    }


def _preflight(
    source_root: Path,
    ticket_id: str,
    short_name: str,
    requested_user: str | None,
    requested_base: str | None,
    bunker_id: str = "",
) -> tuple[dict[str, Any], Path, Ledger, str, str, list[str], list[str]]:
    ticket, bunker, project_key = _ticket(source_root, ticket_id, bunker_id)
    ledger = Ledger(layout.paths(bunker)["ledger"])
    top = _run(
        source_root,
        ["rev-parse", "--show-toplevel"],
        ledger,
        "branch_preflight",
        "The selected work directory is not a Git repository.",
    ).strip()
    if Path(top).resolve() != source_root:
        raise commands.CommandRejected("repository_mismatch", f"Run the workflow from {top}.")
    dirty = _run(
        source_root,
        ["status", "--porcelain", "--untracked-files=all"],
        ledger,
        "branch_preflight",
        "TicketBunker could not inspect the working tree.",
    ).strip()
    if dirty:
        paths = ", ".join(line[3:] for line in dirty.splitlines()[:8])
        raise commands.CommandRejected(
            "worktree_dirty",
            f"The working tree has uncommitted changes ({paths}). Commit or stash them, then retry.",
        )
    user = config.sanitize_user(requested_user or config.get_user(source_root)[0] or "")
    if not user:
        raise commands.CommandRejected("user_missing", "Set a branch user with 'tb config --user NAME', then retry.")
    if not short_name.strip():
        raise commands.CommandRejected("branch_name_required", "Enter a short branch description.")
    full = config.build_branch(user, str(ticket["id"]), short_name)
    _run(
        source_root,
        ["check-ref-format", "--branch", full],
        ledger,
        "branch_preflight",
        f"{full!r} is not a valid Git branch name. Choose a simpler short description.",
    )
    document = _project_document(source_root, project_key, bunker)
    base = str(requested_base or document.get("base_branch") or "main")
    remotes = [
        line.strip()
        for line in _run(
            source_root,
            ["remote"],
            ledger,
            "branch_preflight",
            "TicketBunker could not list Git remotes.",
        ).splitlines()
        if line.strip()
    ]
    if "origin" in remotes:
        _run(
            source_root,
            ["fetch", "--prune", "origin"],
            ledger,
            "branch_fetch",
            "Fetching origin failed. Check network access and repository credentials, then retry.",
        )
    local_branches = [
        line.strip()
        for line in _run(
            source_root,
            ["branch", "--format=%(refname:short)"],
            ledger,
            "branch_preflight",
            "TicketBunker could not list local branches.",
        ).splitlines()
        if line.strip()
    ]
    remote_branches = [
        line.strip()
        for line in _run(
            source_root,
            ["branch", "--remotes", "--format=%(refname:short)"],
            ledger,
            "branch_preflight",
            "TicketBunker could not list remote branches.",
        ).splitlines()
        if line.strip() and not line.strip().endswith("/HEAD")
    ]
    if full in local_branches or f"origin/{full}" in remote_branches:
        raise commands.CommandRejected(
            "branch_exists",
            f"Branch {full} already exists. Choose it from Branches or enter a different short description.",
        )
    normalized_base = base.removeprefix("origin/")
    if normalized_base not in local_branches and f"origin/{normalized_base}" not in remote_branches:
        choices = ", ".join(local_branches) or "none"
        raise commands.CommandRejected(
            "base_branch_missing",
            f"Base branch {base!r} does not exist. Available local branches: {choices}.",
        )
    return ticket, bunker, ledger, full, normalized_base, local_branches, remote_branches


@commands.register("workflow.state")
def command_state(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    return state(context.project_root, str(payload.get("ticket") or ""), str(payload.get("bunker_id") or ""))


def _publish_assignment(source_root: Path, bunker: Path, branch: str, ledger: Ledger) -> dict[str, Any]:
    from . import application, bunkerdb, bunkerruntime

    info = bunkerruntime.marker(bunker)
    if info is None:
        handlers.assign_branch(bunker, branch, ledger)
        return project.publish_data(source_root, f"Attach {branch} to ticket")
    try:
        proposal = bunkerdb.propose_branch_assignment(info["bunker_id"], info["ticket_id"],
                                                     info["repository_id"], branch)
        accepted = application._publish_changes(info["bunker_id"], proposal) if proposal["mode"] == "remote" else None
        result = bunkerdb.apply_ticket_sync_change(info["bunker_id"], proposal, accepted=accepted)
    except (bunkerdb.BunkerDatabaseError, commands.CommandRejected) as exc:
        ledger.event("branch_assignment_pending", branch=branch)
        raise commands.CommandRejected("branch_assignment_pending",
            f"Branch {branch} exists locally, but its ticket link was not saved. "
            "Your branch and files are preserved. Sync the Bunker, then attach this existing branch again.") from exc
    handlers.assign_branch(bunker, branch, ledger)
    ledger.event("bunker_branch_assigned", branch=branch, repository_id=info["repository_id"])
    return {**result, "shared": proposal["mode"] == "remote"}


@commands.register("workflow.start")
def command_start(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    source_root = Path(context.project_root).resolve()
    with repository_lock(source_root):
        return _command_start_locked(source_root, payload)


def _command_start_locked(source_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    ticket_id = str(payload.get("ticket") or "").strip()
    short_name = str(payload.get("name") or "")
    ticket, bunker, ledger, full, base, locals_, remotes = _preflight(
        source_root,
        ticket_id,
        short_name,
        str(payload.get("user") or "") or None,
        str(payload.get("base") or "") or None,
        str(payload.get("bunker_id") or ""),
    )
    if base not in locals_:
        _run(
            source_root,
            ["switch", "--track", "-c", base, f"origin/{base}"],
            ledger,
            "branch_base",
            f"TicketBunker could not create local base branch {base} from origin/{base}.",
        )
    else:
        _run(
            source_root,
            ["switch", base],
            ledger,
            "branch_base",
            f"TicketBunker could not switch to base branch {base}.",
        )
    if f"origin/{base}" in remotes:
        _run(
            source_root,
            ["merge", "--ff-only", f"origin/{base}"],
            ledger,
            "branch_base",
            f"Base branch {base} cannot fast-forward to origin/{base}. Reconcile it manually, then retry.",
        )
    _run(
        source_root,
        ["switch", "-c", full],
        ledger,
        "branch_create",
        f"TicketBunker refreshed {base}, but could not create branch {full}.",
    )
    user = full.split("/", 1)[0]
    if ticket.get("repo") != str(source_root):
        ticket["repo"] = str(source_root)
        handlers.intake_ticket(bunker, ticket, ledger)
    handlers.intake_session_config(bunker, {"branch": full, "user": user, "baseline": base}, ledger)
    publication = _publish_assignment(source_root, bunker, full, ledger)
    return {
        "ticket": ticket_id,
        "branch": full,
        "base": base,
        "current_branch": full,
        "publication": publication,
    }


@commands.register("workflow.attach")
def command_attach(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    source_root = Path(context.project_root).resolve()
    ticket_id = str(payload.get("ticket") or "").strip()
    branch = str(payload.get("branch") or "").strip()
    ticket, bunker, _project_key = _ticket(source_root, ticket_id, str(payload.get("bunker_id") or ""))
    ledger = Ledger(layout.paths(bunker)["ledger"])
    names = [
        line.strip()
        for line in _run(
            source_root,
            ["branch", "--all", "--format=%(refname:short)"],
            ledger,
            "branch_attach",
            "TicketBunker could not list branches.",
        ).splitlines()
        if line.strip() and not line.strip().endswith("/HEAD")
    ]
    if branch not in names:
        raise commands.CommandRejected("branch_missing", f"Branch {branch!r} does not exist in {source_root}.")
    related, _rules, _missing = relate_branches(names, str(ticket.get("key") or ticket_id), [])
    if branch not in related:
        raise commands.CommandRejected(
            "branch_name_invalid",
            f"Branch {branch!r} does not contain ticket key {ticket.get('key') or ticket_id}. "
            "Rename it to the configured user/TICKET-description convention, then retry.",
        )
    user = config.sanitize_user(str(payload.get("user") or "") or config.get_user(source_root)[0] or "")
    session = {"branch": branch}
    if user:
        session["user"] = user
    handlers.intake_session_config(bunker, session, ledger)
    publication = _publish_assignment(source_root, bunker, branch, ledger)
    return {"ticket": ticket_id, "branch": branch, "publication": publication}
