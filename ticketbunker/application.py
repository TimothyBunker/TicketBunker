"""Machine-local desktop application state and typed commands.

The desktop shell is intentionally thin.  Project discovery and the local
profile live behind the same command dispatcher used by every other
TicketBunker transport.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import agent_runtime, commands, config, project


_REMOTE_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_REMOTE_HOST_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$"
)


def _git_value(key: str) -> str:
    process = subprocess.run(
        ["git", "config", "--global", "--get", key],
        capture_output=True,
        text=True,
    )
    return process.stdout.strip() if process.returncode == 0 else ""


def _profile(settings: dict[str, Any]) -> dict[str, Any]:
    stored = settings.get("profile") if isinstance(settings.get("profile"), dict) else {}
    name = str(stored.get("name") or _git_value("user.name"))
    email = str(stored.get("email") or _git_value("user.email"))
    branch_user = str(settings.get("user") or config.sanitize_user(name))
    return {
        "name": name,
        "email": email,
        "branch_user": branch_user,
        "complete": bool(name and email and branch_user),
    }


def _source_root(raw: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise commands.CommandRejected("project_path_required", "Choose a Git project folder.")
    selected = Path(raw).expanduser()
    if not selected.is_dir():
        raise commands.CommandRejected(
            "project_path_missing", f"The selected project folder does not exist: {selected}"
        )
    process = subprocess.run(
        ["git", "-C", str(selected), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if process.returncode != 0 or not process.stdout.strip():
        raise commands.CommandRejected(
            "project_not_git", "Choose a folder inside a Git working tree."
        )
    return Path(process.stdout.strip()).resolve()


def _source_remote(root: Path) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    return process.stdout.strip() if process.returncode == 0 else ""


def _parsed_remote(remote: str) -> dict[str, str] | None:
    """Split common hosted Git remotes without treating local paths as hosts."""
    value = remote.strip()
    if not value or value.startswith(("/", "./", "../", "~")):
        return None
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return None
    transport = ""
    host = ""
    path = ""
    scp = (
        re.fullmatch(r"(?:[^@/:]+@)?([^/:]+):(.+)", value)
        if "://" not in value
        else None
    )
    if scp:
        transport, host, path = "ssh", scp.group(1), scp.group(2)
    else:
        parsed = urlsplit(value)
        if parsed.scheme in {"ssh", "git+ssh"} and parsed.hostname:
            transport, host, path = "ssh", parsed.hostname, parsed.path
        elif parsed.scheme == "https" and parsed.hostname:
            transport, host, path = "https", parsed.hostname, parsed.path
    pieces = [part for part in path.strip("/").split("/") if part]
    if not transport or not host or len(pieces) < 2:
        return None
    repository = pieces[-1].removesuffix(".git")
    namespace = "/".join(pieces[:-1])
    if not repository or not namespace:
        return None
    return {
        "transport": transport,
        "host": host,
        "namespace": namespace,
        "repository": repository,
    }


def _storage_suggestion(root: Path) -> dict[str, str]:
    parsed = _parsed_remote(_source_remote(root)) or {}
    source_repository = str(parsed.get("repository") or root.name)
    repository = (
        source_repository
        if source_repository.lower().endswith("-ticketbunker")
        else f"{source_repository}-ticketbunker"
    )
    return {
        "backend": "git",
        "transport": str(parsed.get("transport") or "ssh"),
        "host": str(parsed.get("host") or "github.com"),
        "namespace": str(parsed.get("namespace") or ""),
        "repository": repository,
    }


def _storage_remote(value: Any) -> str:
    if not isinstance(value, dict):
        raise commands.CommandRejected(
            "storage_required", "Choose where TicketBunker should keep shared data."
        )
    backend = str(value.get("backend") or "git").strip().lower()
    transport = str(value.get("transport") or "").strip().lower()
    host = str(value.get("host") or "").strip()
    namespace = str(value.get("namespace") or "").strip().strip("/")
    repository = str(value.get("repository") or "").strip().removesuffix(".git")
    if backend != "git":
        raise commands.CommandRejected(
            "storage_backend_invalid", "This version connects shared storage through Git."
        )
    if transport not in {"ssh", "https"}:
        raise commands.CommandRejected(
            "storage_transport_invalid", "Choose SSH or HTTPS access."
        )
    if not _REMOTE_HOST_RE.fullmatch(host):
        raise commands.CommandRejected(
            "storage_host_invalid", "Enter only the Git host, such as github.com."
        )
    namespace_parts = namespace.split("/")
    if not namespace or any(
        not _REMOTE_PART_RE.fullmatch(part) or part in {".", ".."}
        for part in namespace_parts
    ):
        raise commands.CommandRejected(
            "storage_namespace_invalid", "Enter a valid owner or group."
        )
    if not _REMOTE_PART_RE.fullmatch(repository) or repository in {".", ".."}:
        raise commands.CommandRejected(
            "storage_repository_invalid", "Enter a valid repository name."
        )
    path = f"{namespace}/{repository}.git"
    return f"git@{host}:{path}" if transport == "ssh" else f"https://{host}/{path}"


def inspect_project(raw: str) -> dict[str, Any]:
    root = _source_root(raw)
    locator = project.load_locator(root)
    embedded = (root / ".tb").is_dir()
    initialized = locator is not None or embedded
    result: dict[str, Any] = {
        "path": str(root),
        "name": root.name,
        "initialized": initialized,
        "ready": embedded,
        "project": str((locator or {}).get("project") or ""),
        "repository": str((locator or {}).get("bunker_repository") or ""),
    }
    if not initialized:
        result["storage"] = _storage_suggestion(root)
    return result


def state() -> dict[str, Any]:
    settings = config.load()
    configured = settings.get("projects")
    paths = configured if isinstance(configured, list) else []
    projects: list[dict[str, Any]] = []
    for raw in paths:
        if not isinstance(raw, str):
            continue
        try:
            projects.append(inspect_project(raw))
        except commands.CommandRejected:
            projects.append(
                {
                    "path": raw,
                    "name": Path(raw).name or raw,
                    "initialized": False,
                    "ready": False,
                    "missing": True,
                    "project": "",
                    "repository": "",
                }
            )
    agent = settings.get("agent") if isinstance(settings.get("agent"), dict) else {}
    return {
        "profile": _profile(settings),
        "projects": projects,
        "agent": {
            "enabled": bool(agent.get("enabled", True)),
            "provider": str(agent.get("provider") or ""),
            "model": str(agent.get("model") or ""),
            "configured": bool(agent.get("provider") and agent.get("model")),
            "decided": "agent" in settings,
        },
    }


@commands.register("application.state")
def command_state(
    _context: commands.CommandContext, _payload: dict[str, Any]
) -> dict[str, Any]:
    return state()


@commands.register("application.profile.update")
def command_profile_update(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    email = str(payload.get("email") or "").strip()
    branch_user = config.sanitize_user(str(payload.get("branch_user") or ""))
    if not name:
        raise commands.CommandRejected("profile_name_required", "Enter your name.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+", email):
        raise commands.CommandRejected("profile_email_invalid", "Enter a valid email address.")
    if not branch_user:
        raise commands.CommandRejected(
            "profile_branch_user_required", "Enter the name to use in branch prefixes."
        )
    settings = config.load()
    settings["profile"] = {"name": name, "email": email}
    settings["user"] = branch_user
    config.save(settings)
    return {"profile": _profile(settings)}


@commands.register("application.agent.state")
def command_agent_state(
    context: commands.CommandContext, _payload: dict[str, Any]
) -> dict[str, Any]:
    return {"agent": agent_runtime.state(context.project_root)}


@commands.register("application.agent.prepare")
def command_agent_prepare(
    context: commands.CommandContext, _payload: dict[str, Any]
) -> dict[str, Any]:
    before = config.load()
    prior_agent = before.get("agent") if isinstance(before.get("agent"), dict) else None
    try:
        runtime = agent_runtime.ensure_runtime()
    except agent_runtime.RuntimeInstallError as exc:
        raise commands.CommandRejected("agent_runtime_unavailable", exc.summary) from exc
    after = config.load()
    if prior_agent is None:
        after.pop("agent", None)
        config.save(after)
    elif prior_agent.get("enabled") is False:
        current_agent = after.get("agent") if isinstance(after.get("agent"), dict) else {}
        after["agent"] = {**current_agent, "enabled": False}
        config.save(after)
    return {
        "runtime": runtime.path,
        "extension": str(agent_runtime.auth_extension_path()),
        "agent": agent_runtime.state(context.project_root),
    }


@commands.register("application.agent.configure")
def command_agent_configure(
    context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    if payload.get("enabled") is False:
        agent_runtime.set_core_only()
        return {"agent": agent_runtime.state(context.project_root)}
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    if not provider or not model:
        raise commands.CommandRejected(
            "agent_selection_required", "Choose both a provider and a model."
        )
    prior = config.load().get("agent")
    was_disabled = isinstance(prior, dict) and prior.get("enabled") is False
    try:
        runtime = agent_runtime.ensure_runtime()
    except agent_runtime.RuntimeInstallError as exc:
        raise commands.CommandRejected("agent_runtime_unavailable", exc.summary) from exc
    result = agent_runtime.configure_selection(
        runtime, provider=provider, model=model, repo=context.project_root
    )
    if not result.get("ready") and was_disabled:
        settings = config.load()
        current = settings.get("agent") if isinstance(settings.get("agent"), dict) else {}
        settings["agent"] = {**current, "enabled": False}
        config.save(settings)
    return {"agent": result}


@commands.register("application.project.add")
def command_project_add(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    selected = inspect_project(str(payload.get("path") or ""))
    settings = config.load()
    current = settings.get("projects")
    paths = [value for value in current if isinstance(value, str)] if isinstance(current, list) else []
    if selected["path"] not in paths:
        paths.append(selected["path"])
        settings["projects"] = paths
        config.save(settings)
    return {"project": selected, "projects": state()["projects"]}


@commands.register("application.project.remove")
def command_project_remove(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    selected = str(payload.get("path") or "")
    settings = config.load()
    current = settings.get("projects")
    paths = [value for value in current if isinstance(value, str)] if isinstance(current, list) else []
    settings["projects"] = [value for value in paths if value != selected]
    config.save(settings)
    return {"projects": state()["projects"]}


@commands.register("application.project.connect")
def command_project_connect(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    root = _source_root(str(payload.get("path") or ""))
    connect_payload = {
        key: payload[key]
        for key in ("project", "project_name", "bunker_repo", "base_branch")
        if payload.get(key) not in (None, "")
    }
    if payload.get("storage") is not None:
        connect_payload["bunker_repo"] = _storage_remote(payload.get("storage"))
    result = commands.dispatch(
        "project.connect",
        connect_payload,
        commands.CommandContext(root, transport="desktop"),
    )
    selected = command_project_add(
        commands.CommandContext(root, transport="desktop"), {"path": str(root)}
    )["project"]
    return {"connection": result, "project": selected}


__all__ = ["inspect_project", "state"]
