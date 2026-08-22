"""Project discovery and companion-repository storage.

Companion mode is the default: the source repository commits only a small
locator while an ignored local ``.tb/`` contains checkouts of the canonical
configuration and data branches.  Git remote permissions remain the sole
publication authority.
"""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import commands
from .adapters.gitrun import GitError, git, git_status
from .ledger import Ledger


LOCATOR_FILE = ".ticketbunker.json"
LOCAL_DIR = ".tb"
DEFAULT_CONFIG_BRANCH = "ticketbunker/config"
DEFAULT_DATA_BRANCH = "ticketbunker/data"
SCHEMA_VERSION = 1
PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*$")


class ProjectError(commands.CommandRejected):
    pass


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def find_source_root(start: Path | None = None) -> Path | None:
    """Find the enclosing source repository without requiring TicketBunker."""
    proc = subprocess.run(
        ["git", "-C", str((start or Path.cwd()).resolve()), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    return Path(proc.stdout.strip()).resolve() if proc.returncode == 0 else None


def locator_path(source_root: Path) -> Path:
    return Path(source_root) / LOCATOR_FILE


def load_locator(source_root: Path) -> dict[str, Any] | None:
    path = locator_path(source_root)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProjectError("locator_invalid", f"{path} is not valid TicketBunker JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectError("locator_invalid", f"{path} must contain one JSON object")
    return value


def local_root(source_root: Path) -> Path:
    return Path(source_root) / LOCAL_DIR


def config_checkout(source_root: Path) -> Path:
    return local_root(source_root) / "config"


def data_checkout(source_root: Path) -> Path:
    return local_root(source_root) / "data"


def config_projects_dir(source_root: Path) -> Path:
    return config_checkout(source_root) / "projects"


def data_projects_dir(source_root: Path) -> Path:
    return data_checkout(source_root) / "projects"


def project_config_dir(source_root: Path, project_key: str | None = None) -> Path:
    locator = load_locator(source_root) or {}
    key = project_key or str(locator.get("project") or "")
    return config_projects_dir(source_root) / key


def project_data_dir(source_root: Path, project_key: str | None = None) -> Path:
    locator = load_locator(source_root) or {}
    key = project_key or str(locator.get("project") or "")
    return data_projects_dir(source_root) / key


def tickets_dir(source_root: Path, project_key: str | None = None) -> Path:
    return project_data_dir(source_root, project_key) / "tickets"


def _init_ledger(source_root: Path) -> Ledger:
    git_dir = source_root / ".git"
    return Ledger(git_dir / "ticketbunker-init.jsonl")


def _identity(source_root: Path, ledger: Ledger) -> dict[str, str]:
    name = git(source_root, ["config", "--get", "user.name"], ledger, "project_identity", check=False).strip()
    email = git(source_root, ["config", "--get", "user.email"], ledger, "project_identity", check=False).strip()
    return {"name": name or "TicketBunker", "email": email or "ticketbunker@localhost"}


def _source_remote(source_root: Path, ledger: Ledger) -> str:
    return git(source_root, ["config", "--get", "remote.origin.url"], ledger, "project_identity", check=False).strip()


def _source_branch(source_root: Path, ledger: Ledger) -> str:
    branch = git(source_root, ["branch", "--show-current"], ledger, "project_identity", check=False).strip()
    return branch or "main"


def _branch_exists(source_root: Path, remote: str, branch: str, ledger: Ledger) -> bool:
    output, status = git_status(
        source_root,
        ["ls-remote", "--exit-code", "--heads", remote, f"refs/heads/{branch}"],
        ledger,
        "companion_probe",
    )
    return status == 0 and bool(output.strip())


def _require_existing_remote(source_root: Path, remote: str, ledger: Ledger) -> None:
    """Reject an absent/inaccessible companion before creating local state.

    An empty remote is valid: TicketBunker creates its two owned branches, but
    never creates the hosting repository itself.
    """
    output, status = git_status(
        source_root,
        ["ls-remote", remote],
        ledger,
        "companion_probe",
    )
    if status != 0:
        detail = output.strip() or "the Git remote could not be reached"
        raise ProjectError(
            "bunker_repo_unreachable",
            "TicketBunker could not connect to that repository. Create an empty Git "
            "repository first, check the URL and your access, then try again. "
            "TicketBunker never creates the repository for you.\n"
            f"Git said: {detail}",
        )


def _configure_identity(checkout: Path, identity: dict[str, str], ledger: Ledger) -> None:
    git(checkout, ["config", "user.name", identity["name"]], ledger, "companion_setup")
    git(checkout, ["config", "user.email", identity["email"]], ledger, "companion_setup")
    git(checkout, ["config", "merge.ours.driver", "true"], ledger, "companion_setup")


def _checkout_branch(
    source_root: Path,
    remote: str,
    branch: str,
    target: Path,
    identity: dict[str, str],
    ledger: Ledger,
) -> bool:
    """Create a local checkout. Return True when the remote branch existed."""
    exists = _branch_exists(source_root, remote, branch, ledger)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if exists:
        git(
            source_root,
            ["clone", "--single-branch", "--branch", branch, remote, str(target)],
            ledger,
            "companion_clone",
        )
    else:
        target.mkdir()
        git(target, ["init"], ledger, "companion_setup")
        git(target, ["switch", "--orphan", branch], ledger, "companion_setup")
        git(target, ["remote", "add", "origin", remote], ledger, "companion_setup")
    _configure_identity(target, identity, ledger)
    return exists


def _commit_and_push(checkout: Path, branch: str, message: str, ledger: Ledger) -> None:
    git(checkout, ["add", "-A"], ledger, "companion_publish")
    dirty = git(checkout, ["status", "--porcelain"], ledger, "companion_publish").strip()
    if dirty:
        git(checkout, ["commit", "-m", message], ledger, "companion_publish")
    try:
        git(
            checkout,
            ["push", "-u", "origin", f"HEAD:refs/heads/{branch}"],
            ledger,
            "companion_publish",
        )
    except GitError as exc:
        raise ProjectError(
            "remote_publish_rejected",
            f"The configuration was prepared, but the Git remote rejected {branch!r}. "
            "Check your remote permission, fetch the latest configuration, and retry.\n"
            f"Git said: {exc.stderr.strip() or 'push rejected'}",
        ) from exc


STANDARD_FIELD_SETS: dict[str, dict[str, Any]] = {
    "ticket.standard": {
        "id": "ticket.standard",
        "version": 1,
        "fields": [
            {"key": "title", "label": "Title", "type": "text", "required": True},
            {"key": "description", "label": "Description", "type": "markdown", "required": False},
            {"key": "type", "label": "Type", "type": "enum", "values": ["bug", "task", "story"]},
            {"key": "priority", "label": "Priority", "type": "string", "required": False},
        ],
    },
    "agile.workflow": {
        "id": "agile.workflow",
        "version": 1,
        "fields": [
            {
                "key": "status",
                "label": "Status",
                "type": "enum",
                "required": True,
                "values": ["backlog", "todo", "in-progress", "in-review", "done", "closed"],
            }
        ],
    },
    "ticket.people": {
        "id": "ticket.people",
        "version": 1,
        "fields": [
            {"key": "assignee", "label": "Assignee", "type": "person"},
            {"key": "reporter", "label": "Reporter", "type": "person"},
            {"key": "people_of_interest", "label": "People of interest", "type": "people"},
        ],
    },
    "ticket.tags": {
        "id": "ticket.tags",
        "version": 1,
        "fields": [{"key": "labels", "label": "Tags", "type": "tags"}],
    },
}


# Provider paths are starter compositions, not core ticket fields. They mirror
# the documented issue payloads closely enough to make first setup useful and
# remain fully replaceable through project configuration.
STANDARD_INTEGRATIONS: dict[str, dict[str, Any]] = {
    "github": {
        "label": "GitHub",
        "template": "github-issues",
        "template_applied": True,
        "fields": {
            "title": "title",
            "description": "body",
            "status": "state",
            "assignee": "assignee.login",
            "reporter": "user.login",
            "labels": "labels[].name",
        },
    },
    "gitlab": {
        "label": "GitLab",
        "template": "gitlab-issues",
        "template_applied": True,
        "fields": {
            "title": "title",
            "description": "description",
            "type": "type",
            "status": "state",
            "assignee": "assignees[].username",
            "reporter": "author.username",
            "labels": "labels[]",
        },
    },
    "jira": {
        "label": "Jira",
        "template": "jira-cloud-issues-v3",
        "template_applied": True,
        "fields": {
            "title": "fields.summary",
            "description": "fields.description",
            "type": "fields.issuetype.name",
            "priority": "fields.priority.name",
            "status": "fields.status.name",
            "assignee": "fields.assignee.displayName",
            "reporter": "fields.reporter.displayName",
            "labels": "fields.labels[]",
        },
    },
}


def _seed_config(
    checkout: Path,
    project_key: str,
    project_name: str,
    source_remote: str,
    source_branch: str,
    identity: dict[str, str],
) -> None:
    default_path = checkout / "global" / "default-composition.json"
    if not default_path.exists():
        default_sets = [{"id": key, "version": value["version"]} for key, value in STANDARD_FIELD_SETS.items()]
        _write_json(default_path, {"schema_version": SCHEMA_VERSION, "field_sets": default_sets})
    else:
        try:
            default_doc = json.loads(default_path.read_text(encoding="utf-8"))
            default_sets = default_doc["field_sets"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ProjectError("global_config_invalid", f"Cannot use canonical global defaults: {exc}") from exc
    for key, value in STANDARD_FIELD_SETS.items():
        path = checkout / "global" / "field-sets" / key / f"{value['version']}.json"
        if not path.exists():
            _write_json(path, {"schema_version": SCHEMA_VERSION, **value})

    root = checkout / "projects" / project_key
    if (root / "project.json").exists():
        raise ProjectError(
            "already_initialized",
            f"TicketBunker project {project_key} already exists in the canonical configuration. "
            "Run 'tb setup' to connect this checkout.",
        )
    _write_json(
        root / "project.json",
        {
            "schema_version": SCHEMA_VERSION,
            "key": project_key,
            "name": project_name,
            "source_remote": source_remote,
            "base_branch": source_branch,
            "created_by": identity,
            "created_at": _now(),
        },
    )
    _write_json(
        root / "composition.json",
        {"schema_version": SCHEMA_VERSION, "field_sets": default_sets, "project_fields": []},
    )
    _write_json(
        root / "mappings.json",
        {
            "schema_version": SCHEMA_VERSION,
            "providers": {
                provider: {
                    **{key: value for key, value in settings.items() if key != "fields"},
                    "fields": dict(settings["fields"]),
                }
                for provider, settings in STANDARD_INTEGRATIONS.items()
            },
        },
    )


def _seed_data(checkout: Path, project_key: str) -> None:
    attributes = checkout / ".gitattributes"
    if not attributes.exists():
        attributes.write_text(
            "**/ledger.jsonl merge=union\n"
            "**/inferences/inferences.jsonl merge=union\n"
            "**/reports/master.md merge=ours\n",
            encoding="utf-8",
        )
    ignore = checkout / ".gitignore"
    if not ignore.exists():
        ignore.write_text(
            "*.session.json\n"
            "literal_pi_session.json\n"
            "**/sessions/*.form.md\n"
            "**/sessions/*.transcript.txt\n"
            "**/observations/patches/\n"
            "*.draft.md\n",
            encoding="utf-8",
        )
    keep = checkout / "projects" / project_key / "tickets" / ".keep"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.touch(exist_ok=True)


def _ensure_source_ignore(source_root: Path) -> None:
    ignore = source_root / ".gitignore"
    text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    lines = text.splitlines()
    if "/.tb/" not in lines and ".tb/" not in lines:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n# TicketBunker companion checkout\n/.tb/\n"
        ignore.write_text(text, encoding="utf-8")


def _legacy_ticket_dirs(legacy_root: Path) -> list[Path]:
    return sorted(
        child
        for child in legacy_root.iterdir()
        if child.is_dir() and child.name not in {"config", "data"} and (child / "ticket.json").exists()
    )


def _import_legacy_tickets(legacy_root: Path, destination: Path) -> list[str]:
    imported = []
    for child in _legacy_ticket_dirs(legacy_root):
        try:
            ticket = json.loads((child / "ticket.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProjectError("legacy_ticket_invalid", f"Cannot read {child / 'ticket.json'}: {exc}") from exc
        ticket_id = str(ticket.get("id") or child.name.removesuffix("_bunker")).strip()
        if not ticket_id or "/" in ticket_id or "\\" in ticket_id:
            raise ProjectError("legacy_ticket_invalid", f"Legacy ticket {child.name!r} has an unsafe id.")
        target = destination / ticket_id
        if target.exists():
            raise ProjectError("legacy_ticket_conflict", f"Companion data already contains ticket {ticket_id}.")
        shutil.copytree(child, target)
        imported.append(ticket_id)
    return imported


def initialize(
    source_root: Path,
    payload: dict[str, Any],
    *,
    legacy_root: Path | None = None,
) -> dict[str, Any]:
    source_root = Path(source_root).resolve()
    project_key = str(payload.get("project") or "").strip().upper()
    remote = str(payload.get("bunker_repo") or "").strip()
    config_branch = str(payload.get("config_branch") or DEFAULT_CONFIG_BRANCH).strip()
    data_branch = str(payload.get("data_branch") or DEFAULT_DATA_BRANCH).strip()
    if not PROJECT_KEY_RE.fullmatch(project_key):
        raise ProjectError("project_key_invalid", "Project key must contain uppercase letters and numbers, starting with a letter.")
    if not remote:
        raise ProjectError("bunker_repo_required", "Enter the companion Git repository URL.")
    legacy_config = source_root / LOCAL_DIR / "config.json"
    if load_locator(source_root) is not None or (legacy_config.exists() and legacy_root is None):
        raise ProjectError(
            "already_initialized",
            "This repository is already initialized for TicketBunker. Run 'tb setup' to connect this checkout.",
        )

    ledger = _init_ledger(source_root)
    _require_existing_remote(source_root, remote, ledger)
    identity = _identity(source_root, ledger)
    config_path = config_checkout(source_root)
    data_path = data_checkout(source_root)
    local_root(source_root).mkdir(parents=True, exist_ok=True)

    config_existed = _checkout_branch(source_root, remote, config_branch, config_path, identity, ledger)
    if config_existed and (config_path / "projects" / project_key / "project.json").exists():
        raise ProjectError(
            "already_initialized",
            f"TicketBunker project {project_key} already exists in the canonical configuration. Run 'tb setup'.",
        )
    data_existed = _checkout_branch(source_root, remote, data_branch, data_path, identity, ledger)

    _seed_data(data_path, project_key)
    imported = _import_legacy_tickets(legacy_root, tickets_dir(source_root, project_key)) if legacy_root else []
    for ticket_id in imported:
        git(
            data_path,
            ["add", "-f", "--", str((tickets_dir(source_root, project_key) / ticket_id).relative_to(data_path))],
            ledger,
            "legacy_import",
        )
    _commit_and_push(data_path, data_branch, f"Initialize {project_key} TicketBunker data", ledger)
    _seed_config(
        config_path,
        project_key,
        str(payload.get("project_name") or project_key),
        _source_remote(source_root, ledger),
        str(payload.get("base_branch") or _source_branch(source_root, ledger)),
        identity,
    )
    _commit_and_push(config_path, config_branch, f"Initialize {project_key} TicketBunker configuration", ledger)

    locator = {
        "schema_version": SCHEMA_VERSION,
        "project": project_key,
        "bunker_repository": remote,
        "config_branch": config_branch,
        "data_branch": data_branch,
    }
    _write_json(locator_path(source_root), locator)
    _ensure_source_ignore(source_root)
    ledger.event(
        "project_initialized",
        project=project_key,
        remote=remote,
        config_branch=config_branch,
        data_branch=data_branch,
        config_branch_preexisting=config_existed,
        data_branch_preexisting=data_existed,
    )
    result = {
        "project": project_key,
        "bunker_repository": remote,
        "config_branch": config_branch,
        "data_branch": data_branch,
        "locator": str(locator_path(source_root)),
        "created_by": identity,
    }
    if imported:
        result["imported_tickets"] = imported
    return result


def migrate_legacy(source_root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Move a repository-embedded database into canonical companion branches.

    A complete backup is made below ``.git`` first. The tracked legacy paths
    are removed only after both companion branches accept the imported data.
    """
    source_root = Path(source_root).resolve()
    if load_locator(source_root) is not None:
        raise ProjectError("already_initialized", "This repository already uses companion storage. Run 'tb setup'.")
    legacy_root = local_root(source_root)
    tickets = _legacy_ticket_dirs(legacy_root) if legacy_root.is_dir() else []
    if not (legacy_root / "config.json").exists() and not tickets:
        raise ProjectError("legacy_database_missing", "No embedded TicketBunker database was found to migrate.")
    if (legacy_root / "config").exists() or (legacy_root / "data").exists():
        raise ProjectError("legacy_path_conflict", "Legacy .tb already contains reserved config or data paths.")

    legacy_entries = sorted(legacy_root.iterdir())
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = source_root / ".git" / "ticketbunker-backups" / stamp
    suffix = 1
    while backup.exists():
        backup = backup.with_name(f"{stamp}-{suffix}")
        suffix += 1
    shutil.copytree(legacy_root, backup)

    migration_payload = dict(payload)
    if not migration_payload.get("project_name") and (legacy_root / "config.json").exists():
        try:
            old_config = json.loads((legacy_root / "config.json").read_text(encoding="utf-8"))
            migration_payload["project_name"] = old_config.get("project")
        except (OSError, ValueError):
            pass
    result = initialize(source_root, migration_payload, legacy_root=legacy_root)

    removed = []
    for path in legacy_entries:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        removed.append(str(path.relative_to(source_root)))
    _init_ledger(source_root).event(
        "legacy_database_migrated",
        project=result["project"],
        imported_tickets=result.get("imported_tickets", []),
        backup=str(backup),
        removed=removed,
    )
    return {**result, "backup": str(backup), "removed": removed}


def setup(source_root: Path) -> dict[str, Any]:
    source_root = Path(source_root).resolve()
    locator = load_locator(source_root)
    if locator is None:
        raise ProjectError("not_initialized", "No .ticketbunker.json was found. Run 'tb init' as the project owner first.")
    project_key = str(locator.get("project") or "")
    remote = str(locator.get("bunker_repository") or "")
    config_branch = str(locator.get("config_branch") or DEFAULT_CONFIG_BRANCH)
    data_branch = str(locator.get("data_branch") or DEFAULT_DATA_BRANCH)
    if not project_key or not remote:
        raise ProjectError("locator_invalid", "The TicketBunker locator is missing its project or companion repository.")

    ledger = _init_ledger(source_root)
    identity = _identity(source_root, ledger)
    for branch, target in ((config_branch, config_checkout(source_root)), (data_branch, data_checkout(source_root))):
        if (target / ".git").exists():
            git(target, ["fetch", "--prune", "origin"], ledger, "companion_refresh")
            git(target, ["pull", "--ff-only", "origin", branch], ledger, "companion_refresh")
        else:
            if not _branch_exists(source_root, remote, branch, ledger):
                raise ProjectError("remote_branch_missing", f"The companion repository has no {branch!r} branch.")
            _checkout_branch(source_root, remote, branch, target, identity, ledger)

    if not (project_config_dir(source_root, project_key) / "project.json").exists():
        raise ProjectError("project_missing", f"The companion configuration does not contain project {project_key}.")
    _ensure_source_ignore(source_root)
    ledger.event("project_setup", project=project_key, remote=remote)
    return {
        "project": project_key,
        "bunker_repository": remote,
        "config_branch": config_branch,
        "data_branch": data_branch,
    }


def publish_data(source_root: Path, message: str) -> dict[str, Any]:
    """Commit, reconcile, and publish the local companion data checkout."""
    source_root = Path(source_root).resolve()
    locator = load_locator(source_root)
    if locator is None:
        return {"published": False}
    checkout = data_checkout(source_root)
    if not (checkout / ".git").exists():
        raise ProjectError("data_checkout_missing", "Local companion data is missing. Run 'tb setup' and retry.")
    branch = str(locator.get("data_branch") or DEFAULT_DATA_BRANCH)
    ledger = Ledger(source_root / ".git" / "ticketbunker-data-sync.jsonl")
    git(checkout, ["add", "-A"], ledger, "data_publish")
    if git(checkout, ["status", "--porcelain"], ledger, "data_publish").strip():
        git(checkout, ["commit", "-m", message], ledger, "data_publish")
    git(checkout, ["fetch", "--prune", "origin"], ledger, "data_publish")
    try:
        local_head = git(checkout, ["rev-parse", "HEAD"], ledger, "data_publish").strip()
        remote_head = git(checkout, ["rev-parse", f"origin/{branch}"], ledger, "data_publish").strip()
        if local_head != remote_head:
            git(checkout, ["pull", "--rebase", "origin", branch], ledger, "data_publish")
            local_head = git(checkout, ["rev-parse", "HEAD"], ledger, "data_publish").strip()
            remote_head = git(checkout, ["rev-parse", f"origin/{branch}"], ledger, "data_publish").strip()
        if local_head != remote_head:
            git(checkout, ["push", "origin", f"HEAD:refs/heads/{branch}"], ledger, "data_publish")
            return {"published": True, "revision": local_head}
        return {"published": False, "revision": local_head}
    except GitError as exc:
        raise ProjectError(
            "data_publish_rejected",
            "TicketBunker kept the local data commit, but could not reconcile it with the companion remote. "
            "Resolve the data checkout and retry with any tb command.\n"
            f"Git said: {exc.stderr.strip() or 'publication rejected'}",
        ) from exc


@commands.register("project.initialize")
def _command_initialize(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    return initialize(context.project_root, payload)


@commands.register("project.setup")
def _command_setup(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    return setup(context.project_root)


@commands.register("project.migrate")
def _command_migrate(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    return migrate_legacy(context.project_root, payload)
