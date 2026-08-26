"""Cached-first shared-truth refresh for the localhost ticket viewer.

Opening a ticket is a read and must stay fast. The viewer therefore renders
the material already present in the companion checkout, then asks this module
to refresh the companion and source refs in a background thread. Every remote
operation is bounded by the existing Git adapter and every state has a named
object, actor, condition, and next action.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Mapping

from . import autoagent, commands, layout, project
from .api import TicketCatalog, TicketDatabase, TicketSnapshot
from .ledger import Ledger


STATE_FILE = "ticketbunker/shared-truth.json"
_LOCK = threading.RLock()
_RUNNING: set[tuple[str, str, str]] = set()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _git_dir(root: Path) -> Path:
    proc = subprocess.run(
        ["git", "-C", str(Path(root).resolve()), "rev-parse", "--absolute-git-dir"],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve()
    return Path(root).resolve() / layout.TB_DIR


def _state_path(root: Path) -> Path:
    return _git_dir(root) / STATE_FILE


def _key(project_key: str, ticket_id: str) -> str:
    return f"{project_key}:{ticket_id}" if project_key else ticket_id


def _read_all(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": 1, "tickets": {}}
    if not isinstance(value, dict) or not isinstance(value.get("tickets"), dict):
        return {"schema_version": 1, "tickets": {}}
    return value


def _write_all(root: Path, value: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _update(root: Path, project_key: str, ticket_id: str, **changes: Any) -> dict[str, Any]:
    with _LOCK:
        state = _read_all(root)
        tickets = state.setdefault("tickets", {})
        entry = dict(tickets.get(_key(project_key, ticket_id)) or {})
        entry.update(changes)
        tickets[_key(project_key, ticket_id)] = entry
        _write_all(root, state)
        return entry


def _ticket(
    root: Path,
    project_key: str,
    ticket_id: str,
    *,
    live_branches: bool = False,
) -> TicketSnapshot:
    if project_key:
        catalog = TicketCatalog(root)
        return catalog.database(project_key).get_ticket(
            ticket_id,
            refresh_branches=live_branches,
        )
    return TicketDatabase(root).get_ticket(ticket_id, refresh_branches=live_branches)


def _content_revision(root: Path, ticket: TicketSnapshot) -> str:
    digest = hashlib.sha256()
    patterns = (
        "ticket.json",
        "observations/*.json",
        "reports/master.md",
        "reports/branches/*.md",
    )
    for pattern in patterns:
        for path in sorted(ticket.bunker.glob(pattern)):
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size};".encode())
    refs = subprocess.run(
        [
            "git",
            "-C",
            str(Path(root).resolve()),
            "for-each-ref",
            "--format=%(refname):%(objectname)",
            "refs/heads",
            "refs/remotes",
        ],
        capture_output=True,
        text=True,
    )
    if refs.returncode == 0:
        digest.update(refs.stdout.encode())
    return digest.hexdigest()[:16]


def _current_branch(root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(Path(root).resolve()), "branch", "--show-current"],
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _same_branch(left: str, right: str) -> bool:
    return left.removeprefix("origin/") == right.removeprefix("origin/")


def synthesis_state(root: Path, ticket: TicketSnapshot) -> dict[str, str]:
    """Describe published synthesis and automatic work in user-facing terms."""
    branch = _current_branch(root)
    selected = next(
        (item for item in ticket.branches if branch and _same_branch(item.name, branch)),
        None,
    )
    try:
        automatic = autoagent.read_state(root)
    except (OSError, ValueError):
        automatic = {}
    queued = automatic.get("queued") or {}
    last = automatic.get("last") or {}
    if isinstance(queued, dict) and branch and queued.get("branch") == branch:
        return {
            "status": "updating",
            "label": "Synthesis updating",
            "detail": f"TicketBunker is checking {branch}.",
            "action": "",
        }
    if isinstance(last, dict) and selected and last.get("branch") == branch and last.get("status") == "fail":
        return {
            "status": "stopped",
            "label": "Analysis stopped",
            "detail": str(last.get("detail") or "The last ticket analysis did not finish."),
            "action": "Retry analysis",
        }
    if selected is not None:
        current = autoagent.synthesis_input_state(root, ticket.bunker, branch)
        status = str(current.get("status") or "")
        if status == "current":
            return {
                "status": "current",
                "label": "Synthesis current",
                "detail": f"The published report matches {branch}.",
                "action": "",
            }
        if status == "missing":
            return {
                "status": "missing",
                "label": "No synthesis published",
                "detail": f"{branch} has no accepted report yet.",
                "action": "Generate synthesis",
            }
        return {
            "status": "outdated",
            "label": "Synthesis outdated",
            "detail": f"{branch} changed after its last checked inputs.",
            "action": "Update synthesis",
        }
    if not ticket.reports:
        return {
            "status": "missing",
            "label": "No synthesis published",
            "detail": "Open a ticket branch to generate its first report.",
            "action": "",
        }
    if any(report.stale_in_master or report.predates_development for report in ticket.reports):
        return {
            "status": "outdated",
            "label": "Synthesis outdated",
            "detail": "At least one branch developed after its published report.",
            "action": "Open that branch to update synthesis",
        }
    return {
        "status": "current",
        "label": "Synthesis current",
        "detail": "Published branch reports match the cached observations.",
        "action": "",
    }


def state(root: Path, project_key: str, ticket_id: str) -> dict[str, Any]:
    root = Path(root).resolve()
    with _LOCK:
        entry = dict(
            (_read_all(root).get("tickets") or {}).get(_key(project_key, ticket_id)) or {}
        )
    try:
        ticket = _ticket(root, project_key, ticket_id)
    except (FileNotFoundError, KeyError, ValueError, commands.CommandRejected):
        ticket = None
    if ticket is not None:
        entry["content_revision"] = _content_revision(root, ticket)
        entry["synthesis"] = synthesis_state(root, ticket)
    entry.setdefault(
        "companion",
        {
            "status": "cached",
            "label": "Using cached companion data",
            "detail": "A background refresh starts when this ticket opens.",
            "action": "Refresh companion",
        },
    )
    entry.setdefault(
        "git",
        {
            "status": "cached",
            "label": "Using cached Git refs",
            "detail": "A background fetch starts when this ticket opens.",
            "action": "Refresh Git refs",
        },
    )
    entry.setdefault("refresh", "idle")
    entry["ticket"] = ticket_id
    entry["project"] = project_key
    return entry


def record_companion_publication(
    root: Path,
    project_key: str,
    ticket_id: str,
    publication: Mapping[str, Any],
) -> None:
    """Expose whether an accepted local report reached companion truth."""
    root = Path(root).resolve()
    if publication.get("pending"):
        companion = {
            "status": "cached",
            "label": "Companion publish pending",
            "detail": str(publication.get("summary") or "The accepted report is local only."),
            "action": str(publication.get("action") or "Run tb sync when remote access is restored."),
            "checked_at": _now(),
        }
    elif project.load_locator(root) is None:
        companion = {
            "status": "local",
            "label": "Local ticket data",
            "detail": "The accepted report is stored in this repository.",
            "action": "",
            "checked_at": _now(),
        }
    else:
        companion = {
            "status": "current",
            "label": "Companion current",
            "detail": "The accepted report is present in the companion branch.",
            "action": "",
            "checked_at": _now(),
        }
    _update(root, project_key, ticket_id, companion=companion)


def _synthesis_queue(
    root: Path,
    ticket: TicketSnapshot,
    *,
    force: bool = False,
) -> dict[str, Any]:
    branch = _current_branch(root)
    if not branch or not any(_same_branch(item.name, branch) for item in ticket.branches):
        return {"queued": False, "reason": "current_branch_has_another_ticket"}
    current = autoagent.synthesis_input_state(root, ticket.bunker, branch)
    if current.get("status") == "current" and not force:
        return {"queued": False, "reason": "inputs_unchanged", "branch": branch}
    return autoagent.enqueue(root, "ticket-refs-changed" if force else "ticket-open")


@commands.register("truth.refresh")
def command_refresh(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root = Path(context.project_root).resolve()
    project_key = str(payload.get("project") or "").strip()
    ticket_id = str(payload.get("ticket") or "").strip()
    if not ticket_id:
        raise commands.CommandRejected("ticket_required", "Choose a ticket to refresh.")

    try:
        before_ticket = _ticket(root, project_key, ticket_id)
        before_revision = _content_revision(root, before_ticket)
    except (FileNotFoundError, KeyError, ValueError, commands.CommandRejected):
        before_revision = ""

    companion: dict[str, str]
    if project.load_locator(root) is not None:
        try:
            project.setup(root)
            companion = {
                "status": "current",
                "label": "Companion current",
                "detail": "The local companion checkouts match their remote branches.",
                "action": "",
                "checked_at": _now(),
            }
        except commands.CommandRejected as exc:
            companion = {
                "status": "cached",
                "label": "Using cached companion data",
                "detail": exc.message.splitlines()[0],
                "action": "Retry companion refresh",
                "checked_at": _now(),
            }
    else:
        companion = {
            "status": "local",
            "label": "Local ticket data",
            "detail": "This project uses an embedded TicketBunker database.",
            "action": "",
            "checked_at": _now(),
        }

    ticket = _ticket(root, project_key, ticket_id)
    ledger = Ledger(_git_dir(root) / "ticketbunker" / "ticket-open.jsonl")
    ok, detail = project.refresh_source_refs(root, ledger)
    if ok:
        git_state = {
            "status": "current",
            "label": "Git refs fetched",
            "detail": "Remote-tracking refs are current; the source worktree was not changed.",
            "action": "",
            "checked_at": _now(),
        }
    else:
        remote = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        ).stdout.strip() or "origin"
        _code, summary, action = project.classify_remote_failure(
            remote, detail, retry="the ticket refresh"
        )
        git_state = {
            "status": "cached",
            "label": "Using cached Git refs",
            "detail": summary,
            "action": action,
            "checked_at": _now(),
        }
    # Resolve branches again from the now-current local and remote-tracking
    # refs. This remains a local read and lets the synthesis queue see a branch
    # discovered by the fetch that just completed.
    ticket = _ticket(root, project_key, ticket_id, live_branches=True)
    content_revision = _content_revision(root, ticket)
    queued = _synthesis_queue(
        root,
        ticket,
        force=bool(before_revision and before_revision != content_revision),
    )
    result = {
        "companion": companion,
        "git": git_state,
        "synthesis": synthesis_state(root, ticket),
        "content_revision": content_revision,
        "refresh": "idle",
        "completed_at": _now(),
        "agent": queued,
    }
    _update(root, project_key, ticket_id, **result)
    return result


def _background(root: Path, project_key: str, ticket_id: str) -> None:
    target = (str(root), project_key, ticket_id)
    try:
        commands.dispatch(
            "truth.refresh",
            {"project": project_key, "ticket": ticket_id},
            commands.CommandContext(root, transport="background"),
        )
    except Exception as exc:
        if isinstance(exc, commands.CommandRejected):
            detail = next(
                (
                    line.strip()
                    for line in exc.message.splitlines()
                    if line.strip() and not line.casefold().startswith("next:")
                ),
                "The background refresh stopped.",
            )
        else:
            detail = "The background refresh stopped before it could confirm new remote state."
        _update(
            root,
            project_key,
            ticket_id,
            refresh="failed",
            completed_at=_now(),
            companion={
                "status": "cached",
                "label": "Using cached companion data",
                "detail": detail,
                "action": "Run tb doctor, then retry refresh",
            },
        )
    finally:
        with _LOCK:
            _RUNNING.discard(target)


def queue_refresh(
    root: Path,
    project_key: str,
    ticket_id: str,
    *,
    force: bool = False,
) -> bool:
    """Start one coalesced ticket refresh and return without waiting."""
    root = Path(root).resolve()
    target = (str(root), project_key, ticket_id)
    with _LOCK:
        if target in _RUNNING:
            return False
        current = state(root, project_key, ticket_id)
        completed = str(current.get("completed_at") or "")
        if completed and not force:
            try:
                age = datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(completed)
                if age.total_seconds() < 15:
                    return False
            except ValueError:
                pass
        _RUNNING.add(target)
        _update(
            root,
            project_key,
            ticket_id,
            refresh="running",
            started_at=_now(),
            companion={
                "status": "refreshing",
                "label": "Refreshing companion data",
                "detail": "TicketBunker is checking the companion remote in the background.",
                "action": "",
            },
            git={
                "status": "refreshing",
                "label": "Fetching Git refs",
                "detail": "Only remote-tracking refs will change.",
                "action": "",
            },
        )
    thread = threading.Thread(
        target=_background,
        args=(root, project_key, ticket_id),
        name=f"tb-truth-{ticket_id}",
        daemon=True,
    )
    thread.start()
    return True


@commands.register("truth.queue")
def command_queue(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root = Path(context.project_root).resolve()
    project_key = str(payload.get("project") or "").strip()
    ticket_id = str(payload.get("ticket") or "").strip()
    if not ticket_id:
        raise commands.CommandRejected("ticket_required", "Choose a ticket to refresh.")
    queued = queue_refresh(root, project_key, ticket_id, force=bool(payload.get("force")))
    return {"queued": queued, **state(root, project_key, ticket_id)}


__all__ = [
    "command_queue",
    "command_refresh",
    "queue_refresh",
    "record_companion_publication",
    "state",
    "synthesis_state",
]
