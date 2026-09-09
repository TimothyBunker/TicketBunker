"""Machine-local runtime for Bunker tickets (DESIGN.md §3.4, §3.13).

A Bunker ticket lives in the Bunker database. When an agent works on it, the
deterministic stages, the harness, the lint and anchor gates, and the master
builder all operate on a bunker-shaped directory. This module projects one
ticket into such a directory under the application data home, keyed by
Bunker and ticket, so the whole pipeline runs unchanged, and it hands every
accepted report back to the Bunker database as the shared truth. Nothing here
is written into a source repository.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from . import bunkerdb, config, layout, platform_services

MARKER = "bunker.json"
_PROJECTION_EXCLUDED = {"project", "source_kind", "source_reference"}
# Keys the pipeline's own ticket intake stamps or owns. They never decide
# whether the projection changed, so a rerun keeps its input fingerprint.
_MECHANICAL = {"created", "updated", "repo", "assigned_branches"}


def _meaningful(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in _MECHANICAL}


def runtime_root(bunker_id: str, repository_id: str = "") -> Path:
    root = bunkerdb.database_path(bunker_id).parent / "runtime"
    if repository_id:
        # Reuse the database identifier guard, not unchecked path segments.
        bunkerdb.database_path(repository_id)
        root = root / "repositories" / repository_id
    return root


def ticket_dir(bunker_id: str, ticket_id: str, repository_id: str = "") -> Path:
    detail = bunkerdb.get_ticket(bunker_id, ticket_id)
    repository = select_repository(detail, repository_id=repository_id)
    return runtime_root(bunker_id, str((repository or {}).get("id") or "")) / f"{ticket_id}_bunker"


def select_repository(detail: dict[str, Any], *, repo: Path | None = None,
                      repository_id: str = "") -> dict[str, Any] | None:
    candidates = list(detail.get("repositories") or [])
    if repository_id:
        candidates = [value for value in candidates if value["id"] == repository_id]
    if repo is not None:
        identity = platform_services.local_path_identity(repo)
        candidates = [value for value in candidates if value.get("local_path")
                      and platform_services.local_path_identity(Path(value["local_path"])) == identity]
    elif not repository_id:
        candidates = [value for value in candidates if value.get("local_path")]
    if not candidates and (repository_id or repo is not None):
        raise bunkerdb.BunkerDatabaseError("This repository is not attached to the ticket's Project. Select an allowed repository in Project configuration.")
    if len(candidates) > 1:
        raise bunkerdb.BunkerDatabaseError("Choose a repository for this ticket before starting work.")
    return candidates[0] if candidates else None


def marker(bunker_dir: Path) -> dict[str, Any] | None:
    """The Bunker identity of a runtime directory; None for legacy bunkers."""
    path = Path(bunker_dir) / MARKER
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("bunker_id") else None


def _write_if_changed(path: Path, text: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")
    return True


def _seed_reports(root: Path, reports: list[dict[str, Any]], repository_id: str) -> list[Path]:
    """Reconcile accepted reports without discarding unpublished working text.

    The database owns accepted reports; this machine's runtime holds the
    working copies the pipeline, the truth commands, and the coworker read.
    A runtime materialized on a machine that never ran synthesis for the
    ticket (a replica, an imported copy) starts from the accepted text so
    those readers see the same reports the page shows. A receipt identifies
    clean working copies that may advance with the database; changed local
    copies remain intact and their next publication checks that baseline.
    """
    paths = layout.paths(root)
    receipt = root / "accepted-reports.json"
    try:
        previous = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    fingerprints = dict(previous)
    revisions = dict(previous.get("_revisions") or {})
    written: list[Path] = []
    for report in reports:
        content = str(report.get("content") or "")
        scope = str(report.get("scope") or "")
        owner = str(report.get("repository_id") or "")
        if not content or (repository_id and owner and owner != repository_id):
            continue
        if scope == "combined" and any(str(value.get("scope") or "").startswith("branch:")
                                        and value.get("repository_id") == repository_id for value in reports):
            continue  # Runtime Combined is repository-local, unlike the database aggregate.
        if scope == "combined":
            target = paths["master"]
        elif scope.startswith("branch:"):
            target = layout.child_report(root, scope.removeprefix("branch:"))
        else:
            continue
        normalized = content if content.endswith("\n") else content + "\n"
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        key = target.relative_to(root).as_posix()
        if target.exists():
            current = hashlib.sha256(target.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            if current == digest:
                fingerprints[key] = digest
                revisions[key] = hashlib.sha256(content.encode("utf-8")).hexdigest()
                continue
            if current != previous.get(key):
                # A locally changed/unpublished component must not be discarded.
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(normalized, encoding="utf-8")
        fingerprints[key] = digest
        revisions[key] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        written.append(target)
    fingerprints["_revisions"] = revisions
    _write_if_changed(receipt, json.dumps(fingerprints, sort_keys=True) + "\n")
    if any(path.parent == paths["children"] for path in written) or not paths["master"].exists():
        from .builders.master import regenerate
        regenerate(root)
    return written


def check_results(root: Path, branches: list[dict[str, Any]], checks: dict[str, Any]) -> dict[str, Any]:
    """Read local deterministic evidence, independently of agent publication."""
    observations = layout.paths(root)["observations"]
    empty = {"branch": "", "commit": "", "checked_at": "", "stale": None, "results": []}
    try:
        value = json.loads((observations / "check.json").read_text(encoding="utf-8"))
        branch = str(value.get("branch") or "")
        outcomes = value.get("per_branch", {}).get(branch, {})
        if not isinstance(outcomes, dict) or not outcomes:
            return empty
    except (OSError, ValueError, AttributeError):
        return empty
    rows = []
    for kind in ("build", "test"):
        outcome = outcomes.get(kind)
        if not isinstance(outcome, dict):
            continue
        text = ""
        # Logs are local evidence, never an arbitrary path from the agent.
        log = (root / str(outcome.get("log") or "")).resolve()
        if log.is_relative_to((observations / "checks").resolve()):
            try:
                with log.open(encoding="utf-8", errors="replace") as stream:
                    text = stream.read(24000)
            except OSError:
                text = "Log is unavailable on this computer."
        rows.append({**{key: outcome.get(key) for key in ("command", "passed", "exit", "timed_out", "seconds", "first_error")}, "kind": kind, "log_text": text})
    commit = str(value.get("commit") or "")
    current = next((item for item in branches if item.get("name") == branch), {})
    recorded_commands = value.get("commands")
    if not isinstance(recorded_commands, dict):
        recorded_commands = {kind: item.get("command") for kind, item in outcomes.items() if isinstance(item, dict)}
    changed_commands = any(str(checks.get(kind) or "").strip() != str(recorded_commands.get(kind) or "").strip()
                           for kind in ("build", "test"))
    return {"branch": branch, "commit": commit, "checked_at": str(value.get("checked_at") or ""),
            "stale": changed_commands or (commit != current.get("commit") if commit and current.get("commit") else None),
            "results": rows}


def effective_checks(bunker_id: str, project_id: str, ticket_id: str, repo_root: str = "") -> dict[str, Any]:
    """The build/test checks that apply to one ticket, and where they came from.

    Precedence: the ticket's own override, then the Project's commands, then
    commands detected from the repository when the Project allows discovery,
    then the Bunker default. The result names its ``source`` so the page can
    say which one is in force.
    """
    from . import checks as checks_mod

    timeout = bunkerdb.CHECK_TIMEOUT_DEFAULT
    for scope in ((project_id or None), None):
        try:
            documents = bunkerdb.get_configuration(bunker_id, project_id=scope).get("documents") or {}
        except bunkerdb.BunkerDatabaseError:
            continue
        content = (documents.get("checks") or {}).get("content")
        if not isinstance(content, dict):
            continue
        timeout = int(content.get("timeout_seconds") or timeout)
        own = (content.get("tickets") or {}).get(ticket_id) if isinstance(content.get("tickets"), dict) else None
        if isinstance(own, dict) and (own.get("build") or own.get("test")):
            return {"build": str(own.get("build") or ""), "test": str(own.get("test") or ""), "timeout_seconds": timeout, "source": "ticket"}
        if content.get("build") or content.get("test"):
            return {"build": str(content.get("build") or ""), "test": str(content.get("test") or ""), "timeout_seconds": timeout, "source": "project" if scope else "bunker"}
        if content.get("discover") and repo_root:
            found = checks_mod.detect(repo_root)
            if found:
                first = found[0]
                return {"build": first["build"], "test": first["test"], "timeout_seconds": timeout, "source": "discovered", "detail": f"{first['tool']}: {first['reason']}"}
    return {"build": "", "test": "", "timeout_seconds": timeout, "source": "none"}


def _checks_for(bunker_id: str, project_id: str, ticket_id: str = "", repo_root: str = "") -> dict[str, Any]:
    value = effective_checks(bunker_id, project_id, ticket_id, repo_root)
    return value if value.get("build") or value.get("test") else {}


def materialize(bunker_id: str, ticket_id: str, *, repo: Path | None = None,
                repository_id: str = "") -> dict[str, Any]:
    """Project one Bunker ticket into its runtime directory.

    Idempotent and cheap: the ticket file is rewritten only when the database
    row changed, and accepted reports advance only clean runtime copies.
    Returns the directory, the attached checkout,
    the base branch, and the identities the publish step needs.
    """
    detail = bunkerdb.get_ticket(bunker_id, ticket_id)
    repository = select_repository(detail, repo=repo, repository_id=repository_id)
    owner = str((repository or {}).get("id") or "")
    root = runtime_root(bunker_id, owner) / f"{ticket_id}_bunker"
    old = runtime_root(bunker_id) / f"{ticket_id}_bunker"
    old_info = marker(old)
    if owner and not root.exists() and old_info and old_info.get("repository_id") == owner:
        from . import autoagent
        state = autoagent.read_state(Path(repository["local_path"])) if repository.get("local_path") else {}
        if state.get("running") or state.get("queued"):
            raise bunkerdb.BunkerDatabaseError("A synthesis is still using the previous ticket runtime. Let it finish, then reopen the ticket to migrate its local work.")
        root.parent.mkdir(parents=True, exist_ok=True)
        old.rename(root)  # Preserve all existing forms, observations, and drafts.
    layout.init_dirs(root)
    existing: dict[str, Any] = {}
    try:
        loaded = json.loads(layout.paths(root)["ticket"].read_text(encoding="utf-8"))
        existing = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        existing = {}
    payload = {
        key: value
        for key, value in dict(detail["ticket"]).items()
        if key not in _PROJECTION_EXCLUDED
    }
    payload["id"] = ticket_id
    # Presentation labels from trackers are not legacy runtime enum values.
    # Keep the database snapshot unchanged; normalize only recognized spellings
    # at this compatibility boundary. Unknown values still fail typed intake.
    from . import types
    kind = str(payload.get("type") or "task").strip().casefold()
    if kind in types.TICKET_TYPES:
        payload["type"] = kind
    status = str(payload.get("status") or "todo").strip().casefold().replace(" ", "-")
    status = {"open": "todo", "to-do": "todo"}.get(status, status)
    if status in types.TICKET_STATUSES:
        payload["status"] = status
    key = str(payload.get("key") or "")
    if not key or not config.validate_ticket_id(key):
        payload["key"] = ticket_id
    if repository is not None:
        payload["repo"] = str(repository["local_path"])
    assignments = payload.get("branch_assignments")
    assigned = (assignments if isinstance(assignments, dict) else {}).get(owner, payload.get("assigned_branches", []))
    payload.pop("branch_assignments", None)
    payload["assigned_branches"] = [
        value for value in (assigned if isinstance(assigned, list) else []) if isinstance(value, str)
    ]
    for stamp in ("created", "updated"):
        if existing.get(stamp) and not payload.get(stamp):
            payload[stamp] = existing[stamp]
    if (not existing or _meaningful(existing) != _meaningful(payload)
            or existing.get("repo") != payload.get("repo")
            or existing.get("assigned_branches") != payload.get("assigned_branches")):
        layout.paths(root)["ticket"].write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _seed_reports(root, detail.get("reports") or [], str((repository or {}).get("id") or ""))
    info = {
        "bunker_id": bunker_id,
        "ticket_id": ticket_id,
        "checks": _checks_for(bunker_id, str(detail["project"].get("id") or ""), ticket_id, str((repository or {}).get("local_path") or "")),
        "project_id": str(detail["project"].get("id") or ""),
        "project_key": str(detail["project"].get("key") or ""),
        "repository_id": str(repository.get("id") or "") if repository else "",
        "repository_path": str(repository.get("local_path") or "") if repository else "",
        "base_branch": str((repository or {}).get("default_branch") or "main"),
    }
    _write_if_changed(root / MARKER, json.dumps(info, indent=2, sort_keys=True) + "\n")
    return {
        **info,
        "bunker": root,
        "repo": Path(info["repository_path"]) if info["repository_path"] else None,
    }


def resolve(repo: Path, branch: str) -> Path | None:
    """The runtime directory for the Bunker ticket a checkout branch names."""
    found = bunkerdb.find_bunker_ticket(repo, branch)
    if found is None:
        return None
    try:
        return materialize(found["bunker_id"], found["ticket_id"], repo=repo,
                           repository_id=found["repository_id"])["bunker"]
    except bunkerdb.BunkerDatabaseError:
        return None


def publish_reports(bunker_dir: Path, branch: str, ledger: Any | None = None) -> dict[str, Any] | None:
    """Hand an accepted child report and the regenerated master to the Bunker.

    Returns None for legacy bunkers, which keep their companion publication.
    The database is the shared truth; the runtime files stay as this
    machine's working copies.
    """
    info = marker(bunker_dir)
    if info is None:
        return None
    from . import application

    paths = layout.paths(Path(bunker_dir))
    child = layout.child_report(Path(bunker_dir), branch).read_text(encoding="utf-8")
    try:
        receipts = json.loads((Path(bunker_dir) / "accepted-reports.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        receipts = {}
    if not isinstance(receipts, dict):
        receipts = {}
    child_key = layout.child_report(Path(bunker_dir), branch).relative_to(bunker_dir).as_posix()
    prior = str((receipts.get("_revisions") or {}).get(child_key) or receipts.get(child_key) or "")
    master_path = paths["master"]
    master = master_path.read_text(encoding="utf-8") if master_path.is_file() else ""
    result = application.publish_ticket_reports(
        str(info["bunker_id"]),
        str(info["ticket_id"]),
        str(info.get("repository_id") or ""),
        branch,
        child,
        master,
        expected_child_revision=f"r:{prior[:16]}" if prior else "",
    )
    detail = bunkerdb.get_ticket(str(info["bunker_id"]), str(info["ticket_id"]))
    _seed_reports(Path(bunker_dir), detail.get("reports") or [], str(info.get("repository_id") or ""))
    if ledger is not None:
        ledger.event(
            "bunker_report_published",
            bunker=str(info["bunker_id"]),
            branch=branch,
            shared=bool(result.get("shared")),
            created=result.get("created"),
            updated=result.get("updated"),
        )
    return result


__all__ = ["MARKER", "marker", "materialize", "publish_reports", "resolve", "runtime_root", "ticket_dir"]
