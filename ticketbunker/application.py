"""Machine-local desktop application state and typed commands.

The desktop shell is intentionally thin. Bunker onboarding, repository
attachment, legacy import, and the local profile live behind the same command
dispatcher used by every other TicketBunker transport.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import (
    agent_runtime,
    bunkerdb,
    commands,
    config,
    platform_services,
    project,
    release_updates,
    remote,
    truth,
)


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
    imported = bunkerdb.imported_companion_sources()
    legacy_projects = [
        {**value, "imported": str(value.get("path") or "") in imported}
        for value in projects
    ]
    agent = settings.get("agent") if isinstance(settings.get("agent"), dict) else {}
    return {
        "platform": platform_services.current().as_dict(),
        "diagnostics": {
            "application": str(
                platform_services.current().data_home()
                / "diagnostics"
                / "app-worker.jsonl"
            )
        },
        "release": release_updates.state(settings),
        "profile": _profile(settings),
        "bunkers": bunkerdb.list_bunkers(),
        # Companion-Git registrations remain visible only as migration input.
        # New desktop onboarding does not use this list as its control plane.
        "legacy_projects": legacy_projects,
        "projects": projects,
        "agent": {
            "enabled": bool(agent.get("enabled", True)),
            "provider": str(agent.get("provider") or ""),
            "model": str(agent.get("model") or ""),
            "configured": bool(agent.get("provider") and agent.get("model")),
            "decided": "agent" in settings,
        },
    }


@commands.register("application.release.check")
def command_release_check(
    _context: commands.CommandContext, _payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        return {"release": release_updates.check()}
    except release_updates.ReleaseCheckError as exc:
        raise commands.CommandRejected("release_check_failed", str(exc)) from exc


def inspect_repository(raw: str) -> dict[str, Any]:
    """Inspect a Git checkout without treating it as Bunker authority."""
    root = _source_root(raw)
    origin = _source_remote(root)
    return {
        "path": str(root),
        "name": root.name,
        "origin": origin,
        "canonical_remote": bunkerdb.canonical_remote(origin),
        "legacy_companion": project.load_locator(root) is not None,
    }


def _bunker_result(operation: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return operation(*args, **kwargs)
    except (bunkerdb.BunkerDatabaseError, remote.RemoteContractError) as exc:
        raise commands.CommandRejected("bunker_action_rejected", str(exc)) from exc


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


@commands.register("application.bunker.create")
def command_bunker_create(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    bunker = _bunker_result(
        bunkerdb.create_bunker,
        str(payload.get("name") or ""),
        project_key=str(payload.get("project_key") or ""),
        project_name=str(payload.get("project_name") or ""),
    )
    return {"bunker": bunker, "bunkers": bunkerdb.list_bunkers()}


@commands.register("application.bunker.project.create")
def command_bunker_project_create(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    bunker = _bunker_result(
        bunkerdb.create_project,
        str(payload.get("bunker_id") or ""),
        key=str(payload.get("key") or ""),
        name=str(payload.get("name") or ""),
        parent_id=str(payload.get("parent_id") or "") or None,
        description=str(payload.get("description") or ""),
    )
    return {"bunker": bunker, "bunkers": bunkerdb.list_bunkers()}


@commands.register("application.bunker.get")
def command_bunker_get(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    bunker = _bunker_result(
        bunkerdb.get_bunker, str(payload.get("bunker_id") or "")
    )
    return {"bunker": bunker}


@commands.register("application.bunker.configuration.get")
def command_bunker_configuration_get(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    return _bunker_result(
        bunkerdb.get_configuration,
        str(payload.get("bunker_id") or ""),
        project_id=str(payload.get("project_id") or "") or None,
    )


@commands.register("application.bunker.configuration.replace")
def command_bunker_configuration_replace(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    content = payload.get("content")
    if not isinstance(content, dict):
        raise commands.CommandRejected(
            "configuration_content_invalid", "Configuration content must be one object."
        )
    return _bunker_result(
        bunkerdb.replace_configuration,
        str(payload.get("bunker_id") or ""),
        project_id=str(payload.get("project_id") or "") or None,
        kind=str(payload.get("kind") or ""),
        expected_revision=str(payload.get("expected_revision") or ""),
        content=content,
    )


@commands.register("application.ticket.list")
def command_ticket_list(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        page = int(payload.get("page") or 1)
        page_size = int(payload.get("page_size") or 25)
    except (TypeError, ValueError) as exc:
        raise commands.CommandRejected(
            "ticket_page_invalid", "Choose a valid ticket page."
        ) from exc
    result = _bunker_result(
        bunkerdb.list_tickets,
        str(payload.get("bunker_id") or ""),
        project_id=str(payload.get("project_id") or "") or None,
        search=str(payload.get("search") or ""),
        status=str(payload.get("status") or "active"),
        assignee=str(payload.get("assignee") or ""),
        reporter=str(payload.get("reporter") or ""),
        label=str(payload.get("label") or ""),
        page=page,
        page_size=page_size,
    )
    return result


@commands.register("application.ticket.get")
def command_ticket_get(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    detail = _bunker_result(
        bunkerdb.get_ticket,
        str(payload.get("bunker_id") or ""),
        str(payload.get("ticket_id") or ""),
    )
    repository = next(
        (
            value
            for value in detail.get("repositories", [])
            if str(value.get("local_path") or "")
        ),
        None,
    )
    if repository is not None and detail["ticket"].get("source_kind") in {
        "legacy-companion",
        "legacy-embedded",
    }:
        try:
            detail["truth"] = truth.state(
                Path(str(repository["local_path"])),
                (
                    str(detail["project"].get("key") or "")
                    if detail["ticket"].get("source_kind") == "legacy-companion"
                    else ""
                ),
                str(detail["ticket"].get("id") or ""),
            )
        except (OSError, ValueError):
            detail["truth"] = {}
    else:
        detail["truth"] = {}
    return detail


@commands.register("application.repository.inspect")
def command_repository_inspect(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        repository = inspect_repository(str(payload.get("path") or ""))
    except commands.CommandRejected:
        raise
    except (OSError, ValueError) as exc:
        raise commands.CommandRejected("repository_invalid", str(exc)) from exc
    return {"repository": repository}


@commands.register("application.bunker.repository.attach")
def command_bunker_repository_attach(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    bunker = _bunker_result(
        bunkerdb.attach_repository,
        str(payload.get("bunker_id") or ""),
        project_id=str(payload.get("project_id") or ""),
        local_path=str(payload.get("path") or ""),
        name=str(payload.get("name") or ""),
        default_branch=str(payload.get("default_branch") or ""),
    )
    return {"bunker": bunker, "bunkers": bunkerdb.list_bunkers()}


@commands.register("application.bunker.remote.configure")
def command_bunker_remote_configure(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    bunker = _bunker_result(
        bunkerdb.configure_remote,
        str(payload.get("bunker_id") or ""),
        server_url=str(payload.get("server_url") or ""),
        remote_bunker_id=str(payload.get("remote_bunker_id") or ""),
        credential_reference=str(payload.get("credential_reference") or ""),
        state=str(payload.get("state") or "pending-auth"),
    )
    return {"bunker": bunker, "bunkers": bunkerdb.list_bunkers()}


@commands.register("application.invitation.inspect")
def command_invitation_inspect(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    invitation = _bunker_result(
        remote.JoinInvitation.parse, str(payload.get("invitation") or "")
    )
    return {
        "invitation": invitation.public_dict(),
        "next": "Authenticate with this server before creating the local replica.",
    }


@commands.register("application.bunker.join")
def command_bunker_join(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    invitation = _bunker_result(
        remote.JoinInvitation.parse, str(payload.get("invitation") or "")
    )
    adapter = remote.HTTPRemoteAdapter(
        invitation.server,
        bearer=str(payload.get("authorization") or ""),
        credential_reference=str(payload.get("credential_reference") or ""),
    )
    enrollment = _bunker_result(adapter.enroll, invitation)
    bunker = _bunker_result(bunkerdb.create_replica, invitation, enrollment)
    return {"bunker": bunker, "bunkers": bunkerdb.list_bunkers()}


@commands.register("application.bunker.import.companion")
def command_bunker_import_companion(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    result = _bunker_result(
        bunkerdb.import_companion,
        str(payload.get("path") or ""),
        bunker_name=str(payload.get("name") or ""),
    )
    return {**result, "bunkers": bunkerdb.list_bunkers()}


__all__ = ["inspect_project", "inspect_repository", "state"]
