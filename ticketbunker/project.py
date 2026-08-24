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
import time
import urllib.parse
from pathlib import Path
from typing import Any

from . import commands, layout, ticketindex
from .adapters.gitrun import GitError, git, git_status
from .ledger import Ledger


LOCATOR_FILE = ".ticketbunker.json"
LOCAL_DIR = ".tb"
DEFAULT_CONFIG_BRANCH = "ticketbunker/config"
DEFAULT_DATA_BRANCH = "ticketbunker/data"
SCHEMA_VERSION = 1
PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*$")
REMOTE_ATTEMPTS = 3
REMOTE_TIMEOUT_SECONDS = 20
_REMOTE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
}
_DNS_FAILURES = (
    "could not resolve host",
    "could not resolve hostname",
    "name or service not known",
    "temporary failure in name resolution",
)


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


def _remote_host(remote: str) -> str:
    """Return a human-facing host for URL and scp-style Git remotes."""
    value = remote.strip()
    if "://" in value:
        return urllib.parse.urlsplit(value).hostname or value
    scp = re.match(r"^(?:[^@/]+@)?([^:/\\]+):", value)
    return scp.group(1) if scp else value


def _is_dns_failure(detail: str) -> bool:
    lowered = detail.casefold()
    return any(marker in lowered for marker in _DNS_FAILURES)


def _remote_git_status(
    repo: Path,
    args: list[str],
    ledger: Ledger,
    stage: str,
) -> tuple[str, int]:
    """Run one remote Git operation with bounded transient-DNS retries."""
    output = ""
    status = 1
    for attempt in range(1, REMOTE_ATTEMPTS + 1):
        output, status = git_status(
            repo,
            args,
            ledger,
            stage,
            env=_REMOTE_ENV,
            timeout=REMOTE_TIMEOUT_SECONDS,
        )
        if status == 0 or not _is_dns_failure(output) or attempt == REMOTE_ATTEMPTS:
            return output, status
        ledger.event("remote_retry", stage=stage, attempt=attempt, reason="dns")
        time.sleep(0.25 * attempt)
    return output, status


def _remote_git(repo: Path, args: list[str], ledger: Ledger, stage: str) -> str:
    output, status = _remote_git_status(repo, args, ledger, stage)
    if status != 0:
        raise GitError(["git", "-C", str(repo), *args], status, output)
    return output


def classify_remote_failure(remote: str, detail: str, *, retry: str = "tb init") -> tuple[str, str, str]:
    """Classify a Git transport failure without exposing a raw command error."""
    lowered = detail.casefold()
    host = _remote_host(remote)
    if _is_dns_failure(detail):
        return (
            "remote_dns_failed",
            f"TicketBunker could not resolve {host} after {REMOTE_ATTEMPTS} automatic attempts.",
            "Restore this machine's DNS or network connection, then run "
            f"'{retry}' again. On WSL, run 'wsl --shutdown' in Windows PowerShell first.",
        )
    if "timed out" in lowered or "operation timed out" in lowered:
        return (
            "remote_timeout",
            f"The connection to {host} timed out after {REMOTE_TIMEOUT_SECONDS} seconds.",
            f"Restore network or VPN access to {host}, then run '{retry}' again.",
        )
    if "permission denied" in lowered or "authentication failed" in lowered:
        return (
            "remote_auth_failed",
            f"Git authentication for {host} was rejected.",
            f"Restore this machine's Git or SSH credentials for {host}, then run '{retry}' again.",
        )
    if "repository not found" in lowered or "does not appear to be a git repository" in lowered:
        return (
            "remote_access_failed",
            f"The companion repository does not exist or this user cannot access it: {remote}",
            f"Check the repository URL and access grant, then run '{retry}' again.",
        )
    last_line = next((line.strip() for line in reversed(detail.splitlines()) if line.strip()), "Git rejected the connection")
    return (
        "remote_unreachable",
        f"TicketBunker could not reach the companion repository. Git reported: {last_line}",
        f"Restore access to {remote}, then run '{retry}' again.",
    )


def probe_remote_heads(
    source_root: Path,
    remote: str,
    branches: list[str],
    ledger: Ledger,
) -> tuple[dict[str, str], str]:
    """Read canonical remote heads through the same retrying transport path."""
    output, status = _remote_git_status(
        source_root,
        ["ls-remote", "--heads", remote, *(f"refs/heads/{branch}" for branch in branches)],
        ledger,
        "doctor_remote",
    )
    if status != 0:
        return {}, output.strip()
    heads: dict[str, str] = {}
    for line in output.splitlines():
        sha, separator, ref = line.partition("\t")
        if separator and ref.startswith("refs/heads/"):
            heads[ref.removeprefix("refs/heads/")] = sha
    return heads, ""


def _remote_error(
    remote: str,
    detail: str,
    *,
    retry: str = "tb init",
    fallback_code: str = "remote_unreachable",
    fallback_summary: str = "",
) -> ProjectError:
    code, summary, action = classify_remote_failure(remote, detail, retry=retry)
    if code in {"remote_unreachable", "remote_access_failed"} and fallback_summary:
        code = fallback_code
        summary = fallback_summary
    return ProjectError(code, f"{summary}\nNext: {action}")


def _branch_exists(source_root: Path, remote: str, branch: str, ledger: Ledger) -> bool:
    output, status = _remote_git_status(
        source_root,
        ["ls-remote", "--exit-code", "--heads", remote, f"refs/heads/{branch}"],
        ledger,
        "companion_probe",
    )
    if status not in {0, 2}:
        raise _remote_error(remote, output)
    return status == 0 and bool(output.strip())


def _require_existing_remote(source_root: Path, remote: str, ledger: Ledger) -> None:
    """Reject an absent/inaccessible companion before creating local state.

    An empty remote is valid: TicketBunker creates its two owned branches, but
    never creates the hosting repository itself.
    """
    output, status = _remote_git_status(
        source_root,
        ["ls-remote", remote],
        ledger,
        "companion_probe",
    )
    if status != 0:
        raise _remote_error(
            remote,
            output,
            fallback_code="bunker_repo_unreachable",
            fallback_summary=(
                "TicketBunker could not connect to that companion repository. "
                "Create the empty repository first or correct its URL. "
                "TicketBunker never creates the repository."
            ),
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
    *,
    sparse: bool = False,
) -> bool:
    """Create a local checkout. Return True when the remote branch existed."""
    exists = _branch_exists(source_root, remote, branch, ledger)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if exists:
        clone_args = ["clone", "--single-branch", "--branch", branch]
        if sparse:
            clone_args.extend(["--filter=blob:none", "--sparse"])
        _remote_git(
            source_root,
            [*clone_args, remote, str(target)],
            ledger,
            "companion_clone",
        )
        if sparse:
            git(
                target,
                [
                    "sparse-checkout",
                    "set",
                    "--no-cone",
                    "/.gitattributes",
                    "/.gitignore",
                    "/projects/*/tickets/index.json",
                ],
                ledger,
                "companion_sparse",
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
        _remote_git(
            checkout,
            ["push", "-u", "origin", f"HEAD:refs/heads/{branch}"],
            ledger,
            "companion_publish",
        )
    except GitError as exc:
        raise _remote_error(
            git(checkout, ["remote", "get-url", "origin"], ledger, "companion_publish", check=False).strip(),
            exc.stderr,
            fallback_code="remote_publish_rejected",
            fallback_summary=(
                f"The configuration was prepared, but the companion remote rejected {branch!r}."
            ),
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
            "Run 'tb init' to connect this checkout.",
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


def _seed_data(checkout: Path, project_key: str, project_name: str | None = None) -> None:
    attributes = checkout / ".gitattributes"
    if not attributes.exists():
        attributes.write_text(
            "**/ledger.jsonl merge=union\n"
            "**/inferences/inferences.jsonl merge=union\n"
            "**/reports/master.md merge=ours\n"
            "**/tickets/index.json merge=ours\n",
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
    if not layout.ticket_index(keep.parent).exists():
        ticketindex.write(keep.parent, project_key, project_name or project_key, ())


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
            "This repository is already initialized for TicketBunker. Run 'tb init' to refresh this checkout.",
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
            f"TicketBunker project {project_key} already exists in the canonical configuration. Run 'tb init'.",
        )
    data_existed = _checkout_branch(source_root, remote, data_branch, data_path, identity, ledger)

    project_name = str(payload.get("project_name") or project_key)
    _seed_data(data_path, project_key, project_name)
    imported = _import_legacy_tickets(legacy_root, tickets_dir(source_root, project_key)) if legacy_root else []
    if imported:
        ticketindex.rebuild(tickets_dir(source_root, project_key), project_key, project_name)
    for ticket_id in imported:
        git(
            data_path,
            ["add", "-f", "--", str((tickets_dir(source_root, project_key) / ticket_id).relative_to(data_path))],
            ledger,
            "legacy_import",
        )
    _commit_and_push(data_path, data_branch, f"Initialize {project_key} TicketBunker data", ledger)
    source_base = str(payload.get("base_branch") or _source_branch(source_root, ledger))
    _seed_config(
        config_path,
        project_key,
        project_name,
        _source_remote(source_root, ledger),
        source_base,
        identity,
    )
    _commit_and_push(config_path, config_branch, f"Initialize {project_key} TicketBunker configuration", ledger)

    locator = {
        "schema_version": SCHEMA_VERSION,
        "project": project_key,
        "bunker_repository": remote,
        "config_branch": config_branch,
        "data_branch": data_branch,
        "base_branch": source_base,
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
        raise ProjectError("already_initialized", "This repository already uses companion storage. Run 'tb init'.")
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
        try:
            if (target / ".git").exists():
                _remote_git(target, ["fetch", "--prune", "origin"], ledger, "companion_refresh")
                _remote_git(target, ["pull", "--ff-only", "origin", branch], ledger, "companion_refresh")
            else:
                if not _branch_exists(source_root, remote, branch, ledger):
                    raise ProjectError("remote_branch_missing", f"The companion repository has no {branch!r} branch.")
                _checkout_branch(
                    source_root,
                    remote,
                    branch,
                    target,
                    identity,
                    ledger,
                    sparse=target == data_checkout(source_root),
                )
        except GitError as exc:
            raise _remote_error(
                remote,
                exc.stderr,
                fallback_code="companion_refresh_failed",
                fallback_summary=(
                    f"TicketBunker could not fast-forward the local {branch!r} checkout. "
                    "Preserve or discard any local companion edits."
                ),
            ) from exc

    current_index = layout.ticket_index(tickets_dir(source_root, project_key))
    if not current_index.exists():
        sparse_value = git(
            data_checkout(source_root),
            ["config", "--bool", "core.sparseCheckout"],
            ledger,
            "companion_sparse",
            check=False,
        ).strip()
        if sparse_value == "true":
            # Compatibility for data branches created before committed indexes.
            try:
                git(
                    data_checkout(source_root),
                    ["sparse-checkout", "disable"],
                    ledger,
                    "companion_sparse",
                )
            except GitError as exc:
                raise ProjectError(
                    "companion_refresh_failed",
                    "TicketBunker could not restore the legacy full data checkout. "
                    "Run 'tb doctor', then retry 'tb init'.\n"
                    f"Git said: {exc.stderr.strip() or 'sparse checkout failed'}",
                ) from exc

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


def _remote_locator(source_root: Path, ledger: Ledger) -> tuple[dict[str, Any], str] | None:
    """Find one canonical locator on source remote branches, if one exists."""
    remotes = {
        value.strip()
        for value in git(source_root, ["remote"], ledger, "locator_discovery", check=False).splitlines()
        if value.strip()
    }
    if "origin" not in remotes:
        return None
    source_remote = git(
        source_root,
        ["remote", "get-url", "origin"],
        ledger,
        "locator_discovery",
        check=False,
    ).strip()
    try:
        _remote_git(source_root, ["fetch", "--prune", "origin"], ledger, "locator_discovery")
    except GitError as exc:
        raise _remote_error(source_remote or "origin", exc.stderr) from exc
    refs = [
        value.strip()
        for value in git(
            source_root,
            ["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"],
            ledger,
            "locator_discovery",
        ).splitlines()
        if value.strip() and value.strip() != "origin/HEAD"
    ]
    candidates: list[tuple[dict[str, Any], str]] = []
    for ref in refs:
        raw, status = git_status(
            source_root,
            ["show", f"{ref}:{LOCATOR_FILE}"],
            ledger,
            "locator_discovery",
        )
        if status != 0:
            continue
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise ProjectError(
                "remote_locator_invalid",
                f"{ref} contains an invalid {LOCATOR_FILE}: {exc}. Repair it, then run 'tb init'.",
            ) from exc
        if isinstance(value, dict) and value.get("project") and value.get("bunker_repository"):
            candidates.append((value, ref))
    if not candidates:
        return None
    signatures = {
        json.dumps(value, sort_keys=True, separators=(",", ":"))
        for value, _ref in candidates
    }
    if len(signatures) > 1:
        branches = ", ".join(ref for _value, ref in candidates)
        raise ProjectError(
            "remote_locator_ambiguous",
            f"Source branches contain different TicketBunker locators ({branches}). "
            "Switch to the intended project branch, then run 'tb init'.",
        )
    value = candidates[0][0]
    preferred = str(value.get("base_branch") or "").strip()
    if not preferred:
        preferred = str(value.get("project") or "").casefold()

    def branch_rank(candidate: tuple[dict[str, Any], str]) -> tuple[int, int, int, str]:
        branch = candidate[1].removeprefix("origin/")
        return (
            0 if branch == preferred else 1,
            0 if branch in {"main", "master", "develop"} else 1,
            branch.count("/") + len(branch),
            branch.casefold(),
        )

    return min(candidates, key=branch_rank)


def _switch_to_locator_branch(source_root: Path, remote_ref: str, ledger: Ledger) -> str:
    branch = remote_ref.removeprefix("origin/")
    dirty = git(
        source_root,
        ["status", "--porcelain", "--untracked-files=all"],
        ledger,
        "locator_discovery",
    ).strip()
    if dirty:
        paths = ", ".join(line[3:] for line in dirty.splitlines()[:8])
        raise ProjectError(
            "source_switch_dirty",
            f"TicketBunker found the project locator on {remote_ref}, but this worktree has changes ({paths}). "
            f"Commit or stash them, switch to {branch}, then run 'tb init'.",
        )
    local = {
        value.strip()
        for value in git(
            source_root,
            ["branch", "--format=%(refname:short)"],
            ledger,
            "locator_discovery",
        ).splitlines()
        if value.strip()
    }
    try:
        if branch in local:
            git(source_root, ["switch", branch], ledger, "locator_discovery")
        else:
            git(
                source_root,
                ["switch", "--track", "-c", branch, remote_ref],
                ledger,
                "locator_discovery",
            )
    except GitError as exc:
        raise ProjectError(
            "source_branch_switch_failed",
            f"TicketBunker found the project locator on {remote_ref}, but Git could not switch to it. "
            f"Switch to {branch} manually, then run 'tb init'.\n"
            f"Git said: {exc.stderr.strip() or 'branch switch failed'}",
        ) from exc
    return branch


def connect(source_root: Path, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Initialize an owner checkout or refresh an existing collaborator checkout."""
    source_root = Path(source_root).resolve()
    supplied = dict(payload or {})
    locator = load_locator(source_root)
    switched_branch = ""
    if locator is None and not str(supplied.get("bunker_repo") or "").strip():
        ledger = _init_ledger(source_root)
        discovered = _remote_locator(source_root, ledger)
        if discovered is None:
            return {"mode": "needs_initialization"}
        _value, remote_ref = discovered
        switched_branch = _switch_to_locator_branch(source_root, remote_ref, ledger)
        locator = load_locator(source_root)
        if locator is None:
            raise ProjectError(
                "remote_locator_missing",
                f"Git switched to {switched_branch}, but {LOCATOR_FILE} is absent. Repair the branch, then run 'tb init'.",
            )
    if locator is None:
        result = {**initialize(source_root, supplied), "mode": "initialized"}
        from . import autoagent

        try:
            result["automation"] = autoagent.install(source_root)
        except (OSError, ValueError) as exc:
            raise ProjectError(
                "agent_hooks_failed",
                f"TicketBunker connected the companion repository but could not install its automatic "
                f"agent hooks: {exc}. Repair the hook path, then run 'tb init' again.",
            ) from exc
        return result
    requested_remote = str(supplied.get("bunker_repo") or "").strip()
    canonical_remote = str(locator.get("bunker_repository") or "").strip()
    if requested_remote and requested_remote != canonical_remote:
        raise ProjectError(
            "locator_conflict",
            "This checkout already has a committed TicketBunker locator. "
            "Run 'tb init' without repository options to use its canonical companion.",
        )
    result = {
        **setup(source_root),
        "mode": "connected",
        "source_branch": switched_branch,
    }
    from . import autoagent

    try:
        result["automation"] = autoagent.install(source_root)
    except (OSError, ValueError) as exc:
        raise ProjectError(
            "agent_hooks_failed",
            f"TicketBunker refreshed the companion repository but could not install its automatic "
            f"agent hooks: {exc}. Repair the hook path, then run 'tb init' again.",
        ) from exc
    return result


def sparse_data_enabled(source_root: Path) -> bool:
    checkout = data_checkout(source_root)
    if not (checkout / ".git").exists():
        return False
    proc = subprocess.run(
        ["git", "-C", str(checkout), "config", "--bool", "core.sparseCheckout"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def include_ticket_path(source_root: Path, project_key: str, ticket_id: str) -> None:
    """Include one ticket path in a sparse checkout, whether new or remote."""
    if not sparse_data_enabled(source_root):
        return
    ledger = _init_ledger(Path(source_root).resolve())
    pattern = f"/projects/{project_key}/tickets/{ticket_id}/"
    try:
        git(
            data_checkout(source_root),
            ["sparse-checkout", "add", "--no-cone", pattern],
            ledger,
            "ticket_materialize",
        )
    except GitError as exc:
        raise ProjectError(
            "ticket_materialize_failed",
            f"TicketBunker could not include {ticket_id} in the local data checkout. "
            "Run 'tb init' and retry.\n"
            f"Git said: {exc.stderr.strip() or 'sparse checkout failed'}",
        ) from exc


def materialize_ticket(source_root: Path, project_key: str, ticket_id: str) -> Path:
    """Hydrate one indexed ticket into a sparse companion data checkout."""
    source_root = Path(source_root).resolve()
    bunker = layout.companion_bunker_path(tickets_dir(source_root, project_key), ticket_id)
    if bunker.is_dir():
        return bunker
    if not sparse_data_enabled(source_root):
        raise ProjectError(
            "ticket_not_materialized",
            f"Companion data does not contain {project_key}/{ticket_id}. Run 'tb init' and retry.",
        )
    include_ticket_path(source_root, project_key, ticket_id)
    if not bunker.is_dir():
        raise ProjectError(
            "ticket_missing",
            f"The companion index references {ticket_id}, but its bunker is absent. Run 'tb doctor'.",
        )
    return bunker


def _project_display_name(source_root: Path, project_key: str) -> str:
    path = project_config_dir(source_root, project_key) / "project.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return project_key
    return str(value.get("name") or project_key) if isinstance(value, dict) else project_key


def _dirty_ticket_ids(status: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    pattern = re.compile(r"projects/([A-Z][A-Z0-9]*)/tickets/([A-Za-z0-9._-]+)(?:/|$)")
    for line in status.splitlines():
        match = pattern.search(line[3:] if len(line) > 3 else line)
        if match and match.group(2) != "index.json":
            found.setdefault(match.group(1), set()).add(match.group(2))
    return found


def _refresh_ticket_indexes(source_root: Path, status: str) -> dict[str, set[str]]:
    dirty = _dirty_ticket_ids(status)
    projects_root = data_projects_dir(source_root)
    if projects_root.is_dir():
        for path in projects_root.iterdir():
            scope = path / "tickets"
            if path.is_dir() and PROJECT_KEY_RE.fullmatch(path.name) and scope.is_dir():
                index = layout.ticket_index(scope)
                if not index.exists():
                    dirty.setdefault(path.name, set()).update(
                        bunker.name for bunker in layout.ticket_dirs(scope)
                    )
    for project_key, ticket_ids in dirty.items():
        scope = tickets_dir(source_root, project_key)
        name = _project_display_name(source_root, project_key)
        if layout.ticket_index(scope).exists():
            ticketindex.update(scope, project_key, name, ticket_ids)
        else:
            ticketindex.rebuild(scope, project_key, name)
    return dirty


def _reconcile_ticket_indexes(source_root: Path, dirty: dict[str, set[str]]) -> None:
    """Reapply local index entries after a rebase selects the remote index.

    Ticket indexes are generated references. Their merge driver deliberately
    avoids line-level JSON conflicts, then this pass adds every locally
    changed ticket back to the reconciled remote index.
    """
    for project_key, ticket_ids in dirty.items():
        scope = tickets_dir(source_root, project_key)
        ticketindex.update(
            scope,
            project_key,
            _project_display_name(source_root, project_key),
            ticket_ids,
        )


def publish_data(source_root: Path, message: str) -> dict[str, Any]:
    """Commit, reconcile, and publish the local companion data checkout."""
    source_root = Path(source_root).resolve()
    locator = load_locator(source_root)
    if locator is None:
        return {"published": False}
    checkout = data_checkout(source_root)
    if not (checkout / ".git").exists():
        raise ProjectError("data_checkout_missing", "Local companion data is missing. Run 'tb init' and retry.")
    branch = str(locator.get("data_branch") or DEFAULT_DATA_BRANCH)
    ledger = Ledger(source_root / ".git" / "ticketbunker-data-sync.jsonl")
    dirty = git(checkout, ["status", "--porcelain"], ledger, "data_publish").strip()
    dirty_tickets = _refresh_ticket_indexes(source_root, dirty)
    git(checkout, ["add", "-A"], ledger, "data_publish")
    if git(checkout, ["status", "--porcelain"], ledger, "data_publish").strip():
        git(checkout, ["commit", "-m", message], ledger, "data_publish")
    try:
        _remote_git(checkout, ["fetch", "--prune", "origin"], ledger, "data_publish")
        local_head = git(checkout, ["rev-parse", "HEAD"], ledger, "data_publish").strip()
        remote_head = git(checkout, ["rev-parse", f"origin/{branch}"], ledger, "data_publish").strip()
        if local_head != remote_head:
            _remote_git(checkout, ["pull", "--rebase", "origin", branch], ledger, "data_publish")
            if dirty_tickets:
                _reconcile_ticket_indexes(source_root, dirty_tickets)
                git(checkout, ["add", "-A"], ledger, "data_publish")
                if git(checkout, ["status", "--porcelain"], ledger, "data_publish").strip():
                    git(checkout, ["commit", "--amend", "--no-edit"], ledger, "data_publish")
            local_head = git(checkout, ["rev-parse", "HEAD"], ledger, "data_publish").strip()
            remote_head = git(checkout, ["rev-parse", f"origin/{branch}"], ledger, "data_publish").strip()
        if local_head != remote_head:
            _remote_git(checkout, ["push", "origin", f"HEAD:refs/heads/{branch}"], ledger, "data_publish")
            return {"published": True, "revision": local_head}
        return {"published": False, "revision": local_head}
    except GitError as exc:
        raise _remote_error(
            str(locator.get("bunker_repository") or "origin"),
            exc.stderr,
            retry="the same TicketBunker command",
            fallback_code="data_publish_rejected",
            fallback_summary=(
                "TicketBunker kept the local data commit, but could not reconcile it with the companion remote. "
                "Resolve the data checkout before retrying."
            ),
        ) from exc


@commands.register("project.initialize")
def _command_initialize(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    return initialize(context.project_root, payload)


@commands.register("project.connect")
def _command_connect(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    return connect(context.project_root, payload)


@commands.register("project.setup")
def _command_setup(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    return connect(context.project_root)


@commands.register("project.migrate")
def _command_migrate(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    return migrate_legacy(context.project_root, payload)
