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

from . import agent_runtime, commands, config, project


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


def inspect_project(raw: str) -> dict[str, Any]:
    root = _source_root(raw)
    locator = project.load_locator(root)
    embedded = (root / ".tb").is_dir()
    return {
        "path": str(root),
        "name": root.name,
        "initialized": locator is not None or embedded,
        "ready": embedded,
        "project": str((locator or {}).get("project") or ""),
        "repository": str((locator or {}).get("repository") or ""),
    }


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
