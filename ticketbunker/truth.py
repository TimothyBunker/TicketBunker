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


def _relative_age(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return ""
    seconds = max(0, int((datetime.datetime.now(datetime.timezone.utc) - parsed).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = days // 30
    return f"{months} month{'s' if months != 1 else ''} ago"


def _commit_changes(root: Path, previous: str, branch: str) -> int | None:
    if not previous or not branch:
        return None
    proc = subprocess.run(
        ["git", "-C", str(Path(root).resolve()), "rev-list", "--count", f"{previous}..{branch}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _synthesis_detail(updated_at: str, changes: int | None, fallback: str) -> str:
    age = _relative_age(updated_at)
    if age and changes is not None:
        if changes == 0:
            return f"Updated {age} · no commits since."
        return f"Updated {age} · {changes} commit{'s' if changes != 1 else ''} since."
    if age:
        return f"Updated {age}."
    return fallback


def synthesis_state(root: Path, ticket: TicketSnapshot) -> dict[str, Any]:
    """Describe user-requested synthesis, age, and changes since publication."""
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
    running = automatic.get("running") or {}
    last = automatic.get("last") or {}
    active = running if isinstance(running, dict) and running.get("branch") == branch else queued
    if (
        isinstance(active, dict)
        and branch
        and active.get("branch") == branch
        and autoagent.was_user_requested(active)
    ):
        return {
            "status": "updating",
            "label": "Synthesizing",
            "detail": f"TicketBunker is updating {branch} because you requested it.",
            "action": "",
        }
    if (
        isinstance(last, dict)
        and selected
        and last.get("branch") == branch
        and last.get("status") == "fail"
        and autoagent.was_user_requested(last)
    ):
        return {
            "status": "stopped",
            "label": "Synthesis stopped",
            "detail": str(last.get("detail") or "The last synthesis did not finish."),
            "action": "Retry synthesis",
            "command": "synthesize",
            "log_url": "/truth/agent-log",
        }
    if selected is not None:
        current = autoagent.synthesis_input_state(root, ticket.bunker, branch)
        status = str(current.get("status") or "")
        report = next(
            (item for item in ticket.reports if _same_branch(item.branch, branch)),
            None,
        )
        updated_at = str(current.get("published_at") or (report.verified_at if report else ""))
        previous = str(report.verified_commit if report else "")
        changes = _commit_changes(root, previous, branch)
        common = {"updated_at": updated_at, "changes": changes}
        if status == "current":
            return {
                "status": "current",
                "label": "Synthesis current",
                "detail": _synthesis_detail(updated_at, changes, f"The synthesis matches {branch}."),
                "action": "",
                **common,
            }
        if status == "missing":
            available = selected.ahead
            detail = f"{branch} has not been synthesized yet."
            if available is not None:
                detail += f" {available} commit{'s' if available != 1 else ''} on this branch."
            return {
                "status": "missing",
                "label": "Not synthesized",
                "detail": detail,
                "action": "Synthesize",
                "command": "synthesize",
                "changes": available,
            }
        if (
            status == "unchecked"
            and report is not None
            and report.verified_commit
            and str(current.get("head") or "").startswith(report.verified_commit)
            and not report.predates_development
        ):
            return {
                "status": "current",
                "label": "Synthesis current",
                "detail": _synthesis_detail(updated_at, changes, f"The synthesis matches {branch}."),
                "action": "",
                **common,
            }
        return {
            "status": "outdated",
            "label": "Synthesis needs update",
            "detail": _synthesis_detail(
                updated_at,
                changes,
                f"{branch} or its ticket context changed after the last synthesis.",
            ),
            "action": "Update synthesis",
            "command": "synthesize",
            **common,
        }
    if not ticket.reports:
        return {
            "status": "missing",
            "label": "Not synthesized",
            "detail": "Switch to a ticket branch, then press Synthesize.",
            "action": "",
        }
    stale = next(
        (report for report in ticket.reports if report.stale_in_master or report.predates_development),
        None,
    )
    if stale is not None:
        return {
            "status": "outdated",
            "label": "Synthesis needs update",
            "detail": f"Switch to {stale.branch}, then press Update synthesis.",
            "action": "",
        }
    latest = max((report.verified_at for report in ticket.reports if report.verified_at), default="")
    return {
        "status": "current",
        "label": "Synthesis current",
        "detail": _synthesis_detail(latest, None, "Published branch reports match cached Git state."),
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


@commands.register("truth.refresh")
def command_refresh(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root = Path(context.project_root).resolve()
    project_key = str(payload.get("project") or "").strip()
    ticket_id = str(payload.get("ticket") or "").strip()
    if not ticket_id:
        raise commands.CommandRejected("ticket_required", "Choose a ticket to refresh.")

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
    # refs. This remains a local read. Synthesis is a separate user action.
    ticket = _ticket(root, project_key, ticket_id, live_branches=True)
    content_revision = _content_revision(root, ticket)
    result = {
        "companion": companion,
        "git": git_state,
        "synthesis": synthesis_state(root, ticket),
        "content_revision": content_revision,
        "refresh": "idle",
        "completed_at": _now(),
    }
    _update(root, project_key, ticket_id, **result)
    return result


@commands.register("truth.synthesize")
def command_synthesize(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Queue synthesis only after an explicit user action."""
    root = Path(context.project_root).resolve()
    project_key = str(payload.get("project") or "").strip()
    ticket_id = str(payload.get("ticket") or "").strip()
    if not ticket_id:
        raise commands.CommandRejected("ticket_required", "Choose a ticket to synthesize.")
    ticket = _ticket(root, project_key, ticket_id, live_branches=True)
    branch = _current_branch(root)
    branches = [item.name for item in ticket.branches]
    if not branch or not any(_same_branch(name, branch) for name in branches):
        available = ", ".join(branches[:3]) or "a branch assigned to this ticket"
        raise commands.CommandRejected(
            "synthesis_branch_required",
            f"Synthesis runs against the checked-out ticket branch.\n"
            f"Next: Switch to {available}, then press Synthesize again.",
        )
    automatic = autoagent.read_state(root)
    for key in ("queued", "running"):
        active = automatic.get(key) or {}
        if isinstance(active, dict) and active.get("branch") == branch:
            return {"queued": False, **state(root, project_key, ticket_id)}
    queued = autoagent.enqueue(root, "user-requested-synthesis")
    if not queued.get("queued"):
        if queued.get("reason") == "agent_disabled":
            raise commands.CommandRejected(
                "synthesis_disabled",
                "Synthesis is disabled on this machine.\n"
                "Next: Run tb init without --core-only, then press Synthesize again.",
            )
        raise commands.CommandRejected(
            "synthesis_not_started",
            "Synthesis could not start for the checked-out branch.\n"
            "Next: Run tb agent status, resolve the named issue, then retry.",
        )
    return {"queued": True, **state(root, project_key, ticket_id)}


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
    "command_synthesize",
    "queue_refresh",
    "record_companion_publication",
    "state",
    "synthesis_state",
]
