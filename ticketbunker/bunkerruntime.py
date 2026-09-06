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


def runtime_root(bunker_id: str) -> Path:
    return platform_services.current().data_home() / "bunkers" / bunker_id / "runtime"


def ticket_dir(bunker_id: str, ticket_id: str) -> Path:
    return runtime_root(bunker_id) / f"{ticket_id}_bunker"


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
    """Copy the Bunker's accepted reports into a runtime that has none.

    The database owns accepted reports; this machine's runtime holds the
    working copies the pipeline, the truth commands, and the coworker read.
    A runtime materialized on a machine that never ran synthesis for the
    ticket (a replica, an imported copy) starts from the accepted text so
    those readers see the same reports the page shows. An existing working
    copy is never overwritten: the pipeline regenerates it and publication
    hands it back to the database.
    """
    paths = layout.paths(root)
    written: list[Path] = []
    for report in reports:
        content = str(report.get("content") or "")
        scope = str(report.get("scope") or "")
        owner = str(report.get("repository_id") or "")
        if not content or (repository_id and owner and owner != repository_id):
            continue
        if scope == "combined":
            target = paths["master"]
        elif scope.startswith("branch:"):
            target = layout.child_report(root, scope.removeprefix("branch:"))
        else:
            continue
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        written.append(target)
    return written


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


def materialize(bunker_id: str, ticket_id: str) -> dict[str, Any]:
    """Project one Bunker ticket into its runtime directory.

    Idempotent and cheap: the ticket file is rewritten only when the database
    row changed, and accepted reports are copied in only where the runtime
    has no working copy yet. Returns the directory, the attached checkout,
    the base branch, and the identities the publish step needs.
    """
    detail = bunkerdb.get_ticket(bunker_id, ticket_id)
    repositories = [
        value for value in detail.get("repositories", []) if str(value.get("local_path") or "")
    ]
    repository = repositories[0] if repositories else None
    root = ticket_dir(bunker_id, ticket_id)
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
    key = str(payload.get("key") or "")
    if not key or not config.validate_ticket_id(key):
        payload["key"] = ticket_id
    if repository is not None:
        payload["repo"] = str(repository["local_path"])
    assigned = existing.get("assigned_branches")
    payload["assigned_branches"] = [
        value for value in (assigned if isinstance(assigned, list) else []) if isinstance(value, str)
    ]
    for stamp in ("created", "updated"):
        if existing.get(stamp) and not payload.get(stamp):
            payload[stamp] = existing[stamp]
    if not existing or _meaningful(existing) != _meaningful(payload) or existing.get("repo") != payload.get("repo"):
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
        return materialize(found["bunker_id"], found["ticket_id"])["bunker"]
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
    master_path = paths["master"]
    master = master_path.read_text(encoding="utf-8") if master_path.is_file() else ""
    result = application.publish_ticket_reports(
        str(info["bunker_id"]),
        str(info["ticket_id"]),
        str(info.get("repository_id") or ""),
        branch,
        child,
        master,
    )
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
