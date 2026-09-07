"""Machine-local desktop application state and typed commands.

The desktop shell is intentionally thin. Bunker onboarding, repository
attachment, legacy import, and the local profile live behind the same command
dispatcher used by every other TicketBunker transport.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from . import (
    agent_runtime,
    bunkerdb,
    commands,
    config,
    credentials,
    platform_services,
    project,
    projectconfig,
    release_updates,
    remote,
    truth,
)
from urllib.parse import urlencode


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
        "trackers": projectconfig.credential_state(),
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
    bunker_id = str(payload.get("bunker_id") or "")
    proposal = _bunker_result(
        bunkerdb.propose_project,
        bunker_id,
        key=str(payload.get("key") or ""),
        name=str(payload.get("name") or ""),
        parent_id=str(payload.get("parent_id") or "") or None,
        description=str(payload.get("description") or ""),
    )
    accepted = _publish_changes(bunker_id, proposal) if proposal["mode"] == "remote" else None
    bunker = _bunker_result(
        bunkerdb.apply_project_change, bunker_id, proposal, accepted=accepted
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
    bunker_id = str(payload.get("bunker_id") or "")
    proposal = _bunker_result(
        bunkerdb.propose_configuration,
        bunker_id,
        project_id=str(payload.get("project_id") or "") or None,
        kind=str(payload.get("kind") or ""),
        expected_revision=str(payload.get("expected_revision") or ""),
        content=content,
    )
    accepted = _publish_changes(bunker_id, proposal) if proposal["mode"] == "remote" else None
    return _bunker_result(
        bunkerdb.apply_configuration_change, bunker_id, proposal, accepted=accepted
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
            if (value["id"] == payload["repository_id"] if payload.get("repository_id")
                else bool(value.get("local_path")))
        ),
        None,
    )
    if payload.get("repository_id") and repository is None:
        raise commands.CommandRejected("repository_missing", "That repository is no longer attached to this Project. Reload the ticket and choose another.")
    detail["selected_repository_id"] = str((repository or {}).get("id") or "")
    if repository is not None and repository.get("local_path"):
        from . import bunkerruntime

        bunker_id = str(payload.get("bunker_id") or "")
        ticket_id = str(detail["ticket"].get("id") or "")
        try:
            bunkerruntime.materialize(bunker_id, ticket_id, repository_id=str(repository["id"]))
            detail["truth"] = truth.state(
                Path(str(repository["local_path"])),
                str(detail["project"].get("key") or ""),
                ticket_id,
                bunker_id,
            )
        except (OSError, ValueError, bunkerdb.BunkerDatabaseError):
            detail["truth"] = {}
    else:
        detail["truth"] = {}
    from . import bunkerruntime as _runtime

    try:
        detail["checks"] = _runtime.effective_checks(
            str(payload.get("bunker_id") or ""),
            str(detail["project"].get("id") or ""),
            str(detail["ticket"].get("id") or ""),
            str((repository or {}).get("local_path") or ""),
        )
    except bunkerdb.BunkerDatabaseError:
        detail["checks"] = {}
    if repository is not None and repository.get("local_path"):
        root = _runtime.ticket_dir(str(payload.get("bunker_id") or ""), str(detail["ticket"]["id"]), str(repository["id"]))
        detail["check_results"] = _runtime.check_results(root,
            [row for row in detail.get("branches", []) if row.get("repository_id") == repository["id"]], detail["checks"])
    return detail


@commands.register("application.ticket.checks.set")
def command_ticket_checks_set(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Set or clear one ticket's own build/test commands in its Project's checks document."""
    bunker_id = str(payload.get("bunker_id") or "")
    ticket_id = str(payload.get("ticket_id") or "").strip()
    if not ticket_id:
        raise commands.CommandRejected("ticket_required", "Choose a ticket first.")
    detail = _bunker_result(bunkerdb.get_ticket, bunker_id, ticket_id)
    project_id = str(detail["project"].get("id") or "")
    from . import bunkerruntime as _runtime
    repository = _bunker_result(_runtime.select_repository, detail,
                               repository_id=str(payload.get("repository_id") or ""))
    configuration = _bunker_result(bunkerdb.get_configuration, bunker_id, project_id=project_id or None)
    document = (configuration.get("documents") or {}).get("checks") or {}
    content = dict(document.get("content") or {"build": "", "test": "", "timeout_seconds": bunkerdb.CHECK_TIMEOUT_DEFAULT})
    tickets = dict(content.get("tickets") or {})
    build = str(payload.get("build") or "").strip()
    test = str(payload.get("test") or "").strip()
    if build or test:
        tickets[ticket_id] = {"build": build, "test": test}
    else:
        tickets.pop(ticket_id, None)
    content["tickets"] = tickets
    proposal = _bunker_result(
        bunkerdb.propose_configuration,
        bunker_id,
        project_id=project_id or None,
        kind="checks",
        expected_revision=str(document.get("revision") or ""),
        content=content,
    )
    accepted = _publish_changes(bunker_id, proposal) if proposal["mode"] == "remote" else None
    _bunker_result(bunkerdb.apply_configuration_change, bunker_id, proposal, accepted=accepted)
    return {"checks": _runtime.effective_checks(bunker_id, project_id, ticket_id, str((repository or {}).get("local_path") or ""))}


@commands.register("application.checks.discover")
def command_checks_discover(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Ask the local agent to propose build/test commands for a Project or one ticket."""
    from . import checks as checks_mod

    bunker_id = str(payload.get("bunker_id") or "")
    ticket_id = str(payload.get("ticket_id") or "").strip()
    project_id = str(payload.get("project_id") or "").strip()
    ticket: dict[str, Any] = {}
    if ticket_id:
        detail = _bunker_result(bunkerdb.get_ticket, bunker_id, ticket_id)
        project_id = str(detail["project"].get("id") or "")
        ticket = dict(detail.get("ticket") or {})
        repositories = detail.get("repositories", [])
    else:
        configuration = _bunker_result(bunkerdb.get_configuration, bunker_id, project_id=project_id or None)
        repositories = (configuration.get("repositories") or {}).get("items") or []
    from . import bunkerruntime

    repository = _bunker_result(bunkerruntime.select_repository, {"repositories": repositories},
                               repository_id=str(payload.get("repository_id") or ""))
    root = str((repository or {}).get("local_path") or "")
    if not root:
        raise commands.CommandRejected("repository_required", "Attach a local repository first; the agent reads its files to find the build.")
    try:
        return checks_mod.discover_with_agent(Path(root), ticket=ticket)
    except checks_mod.DiscoveryError as exc:
        raise commands.CommandRejected("checks_discovery_failed", str(exc)) from exc


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
        binding_id=str(payload.get("binding_id") or ""),
    )
    return {"bunker": bunker, "bunkers": bunkerdb.list_bunkers()}


@commands.register("application.bunker.repository.replace")
def command_bunker_repository_replace(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    repositories = payload.get("repositories")
    if not isinstance(repositories, list):
        raise commands.CommandRejected(
            "repositories_invalid", "Project repositories must be a list."
        )
    bunker_id = str(payload.get("bunker_id") or "")
    proposal = _bunker_result(
        bunkerdb.propose_repository_bindings,
        bunker_id,
        project_id=str(payload.get("project_id") or ""),
        expected_revision=str(payload.get("expected_revision") or ""),
        repositories=repositories,
    )
    accepted = _publish_changes(bunker_id, proposal) if proposal["mode"] == "remote" else None
    configuration = _bunker_result(
        bunkerdb.apply_repository_bindings_change, bunker_id, proposal, accepted=accepted
    )
    return {
        "configuration": configuration,
        "bunker": bunkerdb.get_bunker(str(payload.get("bunker_id") or "")),
        "bunkers": bunkerdb.list_bunkers(),
    }


@commands.register("application.bunker.repository.detach")
def command_bunker_repository_detach(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    bunker = _bunker_result(
        bunkerdb.detach_repository,
        str(payload.get("bunker_id") or ""),
        project_id=str(payload.get("project_id") or ""),
        binding_id=str(payload.get("binding_id") or ""),
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
    reference = str(payload.get("credential_reference") or "") or credentials.find(
        invitation.server
    )
    bearer = str(payload.get("authorization") or "") or (
        credentials.resolve(reference) if reference else ""
    )
    adapter = remote.HTTPRemoteAdapter(
        invitation.server, bearer=bearer, credential_reference=reference if bearer else ""
    )
    profile = None if bearer else _service_profile()
    enrollment = _bunker_result(adapter.enroll, invitation, profile)
    if enrollment.issued_credential:
        reference = credentials.store(invitation.server, enrollment.issued_credential)
        enrollment = remote.Enrollment(enrollment.snapshot, reference)
    bunker = _bunker_result(bunkerdb.create_replica, invitation, enrollment)
    return {"bunker": bunker, "bunkers": bunkerdb.list_bunkers()}


def _service_profile() -> dict[str, str]:
    profile = _profile(config.load())
    if not profile["complete"]:
        raise commands.CommandRejected(
            "profile_required",
            "Enter your name and email in Settings before using a TicketBunker server.",
        )
    return {"display_name": profile["name"], "email": profile["email"]}


def _remote_adapter(bunker_id: str) -> tuple[remote.HTTPRemoteAdapter, dict[str, Any]]:
    connection = _bunker_result(bunkerdb.remote_connection, bunker_id)
    reference = str(connection.get("credential_reference") or "")
    bearer = credentials.resolve(reference) if reference else ""
    server = str(connection["server_url"])
    if not bearer:
        raise commands.CommandRejected(
            "service_signin_required",
            f"This computer no longer holds a credential for {server}. Join the Bunker again from a new invitation.",
        )
    return (
        remote.HTTPRemoteAdapter(server, bearer=bearer, credential_reference=reference),
        connection,
    )


def _publish_changes(bunker_id: str, proposal: Mapping[str, Any]) -> dict[str, Any] | None:
    """Send a proposal's typed changes to the service; return its acceptance."""
    changes = list(proposal.get("changes") or ([proposal["change"]] if proposal.get("change") else []))
    if not changes:
        return None
    adapter, connection = _remote_adapter(bunker_id)
    cursor = str(connection.get("sync_cursor") or "")
    acceptance = {"cursor": cursor, "revision": ""}
    for start in range(0, len(changes), remote.MAX_CHANGE_BATCH):
        result = _bunker_result(
            adapter.push,
            str(connection["remote_bunker_id"]),
            cursor,
            tuple(changes[start : start + remote.MAX_CHANGE_BATCH]),
        )
        cursor = str(result.get("cursor") or cursor)
        acceptance = {"cursor": cursor, "revision": str(result.get("revision") or "")}
    return acceptance


@commands.register("credentials.state")
def command_credentials_state(
    _context: commands.CommandContext, _payload: dict[str, Any]
) -> dict[str, Any]:
    """Which tracker tokens exist on this computer. Values are never returned."""
    return {
        "credentials": projectconfig.credential_state(),
        "credentials_file": str(projectconfig.credential_file()),
    }


def _record_connection(
    bunker_id: str, provider: str, account: Mapping[str, str], connected_by: str
) -> dict[str, Any]:
    """Note the linked account on the Bunker when this member may write config."""
    import datetime

    try:
        configuration = bunkerdb.get_configuration(bunker_id)
        if not configuration["permission"]["can_write"]:
            return {"recorded": False, "note": "Only a Bunker owner can record the linked account for everyone."}
        document = configuration["documents"].get("connections") or {}
        providers = dict((document.get("content") or {}).get("providers") or {})
        providers[provider] = {
            "account": str(account.get("login") or ""),
            "name": str(account.get("name") or ""),
            "connected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "connected_by": connected_by,
        }
        proposal = bunkerdb.propose_configuration(
            bunker_id,
            project_id=None,
            kind="connections",
            expected_revision=str(document.get("revision") or ""),
            content={"providers": providers},
        )
        accepted = _publish_changes(bunker_id, proposal) if proposal["mode"] == "remote" else None
        bunkerdb.apply_configuration_change(bunker_id, proposal, accepted=accepted)
    except (bunkerdb.BunkerDatabaseError, remote.RemoteContractError, commands.CommandRejected) as exc:
        return {"recorded": False, "note": str(exc)}
    return {"recorded": True, "note": ""}


@commands.register("application.tracker.connect")
def command_tracker_connect(
    context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Verify a tracker token, keep it on this computer, and note the account."""
    from .integrations import github

    provider = str(payload.get("provider") or "").strip().lower()
    token = str(payload.get("token") or "").strip()
    if provider not in bunkerdb.CONNECTION_PROVIDERS:
        raise commands.CommandRejected("tracker_unknown", "Choose GitHub, GitLab, or Jira.")
    if not token:
        raise commands.CommandRejected("credential_required", "Paste a personal access token first.")
    account = {"login": "", "name": ""}
    if provider == "github":
        try:
            discovered = github.discover(token)
        except github.GitHubError as exc:
            raise commands.CommandRejected(
                "tracker_token_rejected",
                f"GitHub did not accept this token ({exc}). Create a personal access token with repository and Projects read access, then retry.",
            ) from exc
        account = dict(discovered["account"])
    saved = commands.dispatch("credentials.update", {"provider": provider, "token": token}, context)
    result: dict[str, Any] = {
        "connection": {"provider": provider, "account": account["login"], "name": account["name"]},
        "trackers": saved["credentials"],
    }
    bunker_id = str(payload.get("bunker_id") or "")
    if bunker_id and account["login"]:
        profile = _profile(config.load())
        result["connection"].update(
            _record_connection(bunker_id, provider, account, str(profile.get("name") or ""))
        )
        result["bunker"] = _bunker_result(bunkerdb.get_bunker, bunker_id)
    return result


@commands.register("application.tracker.boards")
def command_tracker_boards(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """List the boards this computer's token can mirror, for picking one."""
    from .integrations import github

    provider = str(payload.get("provider") or "github").strip().lower()
    if provider != "github":
        raise commands.CommandRejected(
            "tracker_unsupported", "This version lists GitHub boards. GitLab and Jira are coming."
        )
    token = projectconfig.credential_value("github")
    if not token:
        raise commands.CommandRejected(
            "tracker_token_required", "Connect GitHub with a token first, then choose a board."
        )
    try:
        return github.discover(token)
    except github.GitHubError as exc:
        raise commands.CommandRejected(
            "tracker_unreachable",
            f"GitHub could not list your boards ({exc}). Check the token, then retry.",
        ) from exc


def _apply_document(
    bunker_id: str, project_id: str, kind: str, expected_revision: str, content: Mapping[str, Any]
) -> dict[str, Any]:
    proposal = _bunker_result(
        bunkerdb.propose_configuration,
        bunker_id,
        project_id=project_id,
        kind=kind,
        expected_revision=expected_revision,
        content=content,
    )
    accepted = _publish_changes(bunker_id, proposal) if proposal["mode"] == "remote" else None
    return _bunker_result(bunkerdb.apply_configuration_change, bunker_id, proposal, accepted=accepted)


@commands.register("application.project.link")
def command_project_link(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Link a chosen board: scan it, build the Project's model, record the board."""
    from .integrations import github

    bunker_id = str(payload.get("bunker_id") or "")
    project_id = str(payload.get("project_id") or "")
    board = payload.get("board")
    if not isinstance(board, dict):
        raise commands.CommandRejected("board_required", "Choose a board from the list first.")
    source = {
        "provider": "github",
        "kind": str(board.get("kind") or ""),
        "owner": str(board.get("owner") or ""),
        "number": board.get("number") or 0,
        "repository": str(board.get("repository") or ""),
        "title": str(board.get("title") or ""),
        "url": str(board.get("url") or ""),
        "include_comments": bool(payload.get("include_comments", True)),
    }
    token = projectconfig.credential_value("github")
    if not token:
        raise commands.CommandRejected(
            "tracker_token_required", "Connect GitHub with a token first, then link a board."
        )
    try:
        inspection = github.inspect_board(source, token)
    except (github.GitHubError, ValueError) as exc:
        raise commands.CommandRejected(
            "tracker_unreachable", f"GitHub could not read that board ({exc}). Retry, or pick another board."
        ) from exc
    if inspection.get("title"):
        source["title"] = str(inspection["title"])
    if inspection.get("url"):
        source["url"] = str(inspection["url"])
    configuration = _bunker_result(bunkerdb.get_configuration, bunker_id, project_id=project_id)
    documents = configuration["documents"]
    fields = list((documents.get("ticket-fields", {}).get("content") or {}).get("fields") or [])
    providers = dict((documents.get("integrations", {}).get("content") or {}).get("providers") or {})
    github_settings = dict(providers.get("github") or bunkerdb.DEFAULT_INTEGRATIONS["github"])
    derived = github.derive_model(inspection, fields, dict(github_settings.get("fields") or {}))
    # The model is published before the board is recorded, and fields before the
    # mapping that references them, so every step validates against what exists.
    configuration = _apply_document(
        bunker_id, project_id, "ticket-fields",
        str(documents.get("ticket-fields", {}).get("revision") or ""),
        {"fields": derived["fields"]},
    )
    providers["github"] = {**github_settings, "fields": derived["mapping"]}
    configuration = _apply_document(
        bunker_id, project_id, "integrations",
        str(configuration["documents"].get("integrations", {}).get("revision") or ""),
        {"providers": providers},
    )
    sources, kind, revision = bunkerdb.tracker_sources(configuration["documents"])
    key = (source["kind"], source["owner"], int(source["number"] or 0), source["repository"])
    kept = [
        item for item in sources
        if (str(item.get("kind") or ""), str(item.get("owner") or ""), int(item.get("number") or 0), str(item.get("repository") or "")) != key
    ]
    existing = next((item for item in sources if item not in kept), None)
    if existing and existing.get("id") and existing.get("id") != "legacy":
        source["id"] = str(existing["id"])
    kept.append(source)
    configuration = _apply_document(
        bunker_id, project_id, "tracker-sources",
        revision if kind == "tracker-sources" else "",
        {"sources": kept},
    )
    return {
        "configuration": configuration,
        "bunker": bunkerdb.get_bunker(bunker_id),
        "bunkers": bunkerdb.list_bunkers(),
        "link": {
            "summary": derived["summary"],
            "fields_found": len(inspection.get("fields") or []),
            "labels_found": len(inspection.get("labels") or []),
            "items": int(inspection.get("items") or 0),
            "source": bunkerdb.tracker_summary(source),
            "relinked": existing is not None,
        },
    }


@commands.register("application.project.unlink")
def command_project_unlink(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Unlink one board. Tickets already synced from it stay in the Project."""
    bunker_id = str(payload.get("bunker_id") or "")
    project_id = str(payload.get("project_id") or "")
    source_id = str(payload.get("source_id") or "").strip()
    if not source_id:
        raise commands.CommandRejected("board_required", "Choose a linked board to unlink.")
    configuration = _bunker_result(bunkerdb.get_configuration, bunker_id, project_id=project_id)
    sources, kind, revision = bunkerdb.tracker_sources(configuration["documents"])
    kept = [item for item in sources if str(item.get("id") or "") != source_id]
    if len(kept) == len(sources):
        raise commands.CommandRejected("board_unknown", "That board is not linked to this Project.")
    configuration = _apply_document(
        bunker_id, project_id, "tracker-sources",
        revision if kind == "tracker-sources" else "",
        {"sources": kept},
    )
    return {
        "configuration": configuration,
        "bunker": bunkerdb.get_bunker(bunker_id),
        "bunkers": bunkerdb.list_bunkers(),
    }


def publish_ticket_reports(
    bunker_id: str,
    ticket_id: str,
    repository_id: str,
    branch: str,
    child: str,
    master: str,
    *,
    expected_child_revision: str | None = None,
) -> dict[str, Any]:
    """Record an accepted child report and its master in the Bunker.

    The lint and anchor gates already ran; on a shared Bunker the service
    accepts the report changes before they are written locally.
    """
    proposal = _bunker_result(
        bunkerdb.propose_report_publication,
        bunker_id,
        ticket_id=ticket_id,
        repository_id=repository_id,
        branch=branch,
        child=child,
        master=master,
    )
    if expected_child_revision is not None:
        child_change = next((value for value in proposal["changes"]
                             if value["payload"]["scope"] == f"branch:{branch}"), None)
        if child_change and child_change["expected_revision"] != expected_child_revision:
            raise commands.CommandRejected("report_conflict",
                "This branch report changed in the Bunker while the local draft was being prepared. "
                "The shared report and local draft are preserved. Sync and review the newer report before retrying.")
    accepted = _publish_changes(bunker_id, proposal) if proposal["mode"] == "remote" else None
    result = _bunker_result(bunkerdb.apply_report_publication, bunker_id, proposal, accepted=accepted)
    return {**result, "shared": proposal["mode"] == "remote"}


def _github_target(ticket: Mapping[str, Any]) -> dict[str, Any] | None:
    """The issue a GitHub-sourced ticket came from, or None."""
    if str(ticket.get("source_kind") or "") != "github":
        return None
    metadata = ticket.get("metadata") if isinstance(ticket.get("metadata"), Mapping) else {}
    github_meta = metadata.get("github") if isinstance(metadata.get("github"), Mapping) else {}
    repository = str(github_meta.get("repository") or "")
    number = github_meta.get("issue_number")
    if not repository or not isinstance(number, int):
        return None
    return {"provider": "github", "repository": repository, "number": number, "label": f"{repository}#{number}", "url": str(ticket.get("source_reference") or metadata.get("source_url") or "")}


def _report_for_update(detail: Mapping[str, Any], scope: str, repository_id: str = "") -> dict[str, Any] | None:
    reports = [value for value in detail.get("reports") or [] if str(value.get("scope") or "").startswith("branch:")]
    if repository_id:
        reports = [value for value in reports if value.get("repository_id") == repository_id]
    if scope:
        for value in reports:
            if value.get("scope") == f"branch:{scope}":
                return dict(value)
    reports.sort(key=lambda value: str(value.get("updated_at") or ""), reverse=True)
    return dict(reports[0]) if reports else None


@commands.register("application.ticket.update.draft")
def command_ticket_update_draft(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Draft a tracker comment from a branch report's Standing; nothing is sent."""
    from . import projectconfig, updates
    from .processors import anchorcheck

    bunker_id = str(payload.get("bunker_id") or "")
    ticket_id = str(payload.get("ticket_id") or "")
    scope = str(payload.get("scope") or "").strip()
    detail = _bunker_result(bunkerdb.get_ticket, bunker_id, ticket_id)
    report = _report_for_update(detail, scope, str(payload.get("repository_id") or ""))
    if report is None:
        raise commands.CommandRejected("report_missing", "Synthesize a branch report first; the update is drafted from its Standing.")
    branch = str(report.get("scope") or "").removeprefix("branch:")
    stamp = anchorcheck.verified_stamp(str(report.get("content") or "")) or {}
    composed = updates.compose(str(report.get("content") or ""), ticket_id=ticket_id, branch=branch, commit=str(stamp.get("commit") or ""))
    if not composed["text"]:
        raise commands.CommandRejected("standing_missing", "This report has no Standing yet. Update synthesis, then draft again.")
    target = _github_target(detail.get("ticket") or {})
    can_post = bool(target and projectconfig.credential_value("github"))
    return {**composed, "branch": branch, "target": target, "can_post": can_post}


@commands.register("application.ticket.update.post")
def command_ticket_update_post(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Post the user's edited update as a comment on the ticket's source issue."""
    from . import projectconfig
    from .integrations import github

    bunker_id = str(payload.get("bunker_id") or "")
    ticket_id = str(payload.get("ticket_id") or "")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise commands.CommandRejected("update_empty", "Write or draft the update first.")
    if len(text) > 60000:
        raise commands.CommandRejected("update_too_long", "Keep the update under 60,000 characters.")
    detail = _bunker_result(bunkerdb.get_ticket, bunker_id, ticket_id)
    target = _github_target(detail.get("ticket") or {})
    if target is None:
        raise commands.CommandRejected("update_target_missing", "This ticket did not come from a GitHub issue, so there is nowhere to post. Copy the text instead.")
    token = projectconfig.credential_value("github")
    if not token:
        raise commands.CommandRejected("github_credential_missing", "Connect a GitHub token under Trackers, then post again.")
    try:
        posted = github.post_issue_comment(str(target["repository"]), int(target["number"]), text, token)
    except github.GitHubError as exc:
        raise commands.CommandRejected("github_post_failed", f"GitHub did not accept the comment: {exc}") from exc
    return {"posted": True, "target": target, **posted}


@commands.register("application.project.checks.detect")
def command_project_checks_detect(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Suggest build/test commands from the Project's attached checkout."""
    from . import checks as checks_mod

    bunker_id = str(payload.get("bunker_id") or "")
    project_id = str(payload.get("project_id") or "")
    configuration = _bunker_result(bunkerdb.get_configuration, bunker_id, project_id=project_id or None)
    roots = [
        str(item.get("local_path") or "")
        for item in (configuration.get("repositories") or {}).get("items") or []
        if str(item.get("local_path") or "")
    ]
    suggestions: list[dict[str, Any]] = []
    for root in roots:
        for suggestion in checks_mod.detect(root):
            suggestions.append({**suggestion, "repository": root})
    return {"repositories": roots, "suggestions": suggestions}


@commands.register("application.project.sync")
def command_project_sync(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Pull one Project's tracker board into its tickets."""
    from .integrations import github

    bunker_id = str(payload.get("bunker_id") or "")
    project_id = str(payload.get("project_id") or "")
    configuration = _bunker_result(bunkerdb.get_configuration, bunker_id, project_id=project_id)
    sources, _kind, _revision = bunkerdb.tracker_sources(configuration["documents"])
    if not sources:
        raise commands.CommandRejected(
            "tracker_required", "Link a board in Configure → Boards, then sync."
        )
    unsupported = [item for item in sources if str(item.get("provider") or "") != "github"]
    if unsupported:
        raise commands.CommandRejected(
            "tracker_unsupported",
            "This version syncs GitHub boards. GitLab and Jira sync are coming.",
        )
    token = projectconfig.credential_value("github")
    if not token:
        raise commands.CommandRejected(
            "tracker_token_required",
            "Connect a GitHub token in Settings → Trackers, then sync again.",
        )
    record = _bunker_result(bunkerdb.project_record, bunker_id, project_id)
    integrations = configuration["documents"].get("integrations", {})
    providers = (integrations.get("content") or {}).get("providers") if isinstance(integrations, dict) else {}
    github_settings = providers.get("github") if isinstance(providers, dict) else {}
    mappings = github_settings.get("fields") if isinstance(github_settings, dict) else {}
    tickets: list[dict[str, Any]] = []
    for source in sources:
        include_comments = bool(source.get("include_comments", True))
        try:
            entries = github.collect_entries(source, token, include_comments=include_comments)
        except (github.GitHubError, ValueError) as exc:
            raise commands.CommandRejected(
                "tracker_unreachable",
                f"GitHub sync stopped at {bunkerdb.tracker_summary(source)}: {exc}. Check the token in Settings → Trackers and the board in Configure → Boards, then retry.",
            ) from exc
        try:
            tickets.extend(
                github.build_tickets(
                    entries,
                    mappings if isinstance(mappings, dict) else {},
                    project_key=str(record.get("key") or ""),
                    include_comments=include_comments,
                    bunker_ids=True,
                )
            )
        except ValueError as exc:
            raise commands.CommandRejected(
                "tracker_ticket_invalid", f"GitHub sync stopped at {bunkerdb.tracker_summary(source)}: {exc}."
            ) from exc
    # The same issue may sit on two linked boards; keep one copy by identity.
    unique: dict[str, dict[str, Any]] = {}
    for ticket in tickets:
        metadata = ticket.get("metadata") if isinstance(ticket.get("metadata"), dict) else {}
        identity = str(metadata.get("source_identity") or "") or f"id:{ticket.get('id')}"
        unique.setdefault(identity, ticket)
    tickets = list(unique.values())
    proposal = _bunker_result(
        bunkerdb.propose_ticket_sync,
        bunker_id,
        project_id=project_id,
        tickets=tickets,
        source_kind="github",
    )
    accepted = _publish_changes(bunker_id, proposal) if proposal["mode"] == "remote" else None
    result = _bunker_result(
        bunkerdb.apply_ticket_sync_change, bunker_id, proposal, accepted=accepted
    )
    return {
        "sync": {**result, "provider": "github", "source": bunkerdb.trackers_summary(sources), "boards": len(sources)},
        "bunker": bunkerdb.get_bunker(bunker_id),
        "bunkers": bunkerdb.list_bunkers(),
    }


def _invitation_link(server: str, bunker_id: str, token: str) -> str:
    query = urlencode({"server": server, "bunker": bunker_id, "invite": token})
    return f"{remote.INVITE_SCHEME}://join?{query}"


@commands.register("application.bunker.share")
def command_bunker_share(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Publish a local Bunker to a service and become its owner."""
    bunker_id = str(payload.get("bunker_id") or "")
    server = _bunker_result(remote.normalize_server_url, str(payload.get("server_url") or ""))
    bunker = _bunker_result(bunkerdb.get_bunker, bunker_id)
    if str(bunker.get("mode") or "local") != "local":
        raise commands.CommandRejected(
            "bunker_already_shared", "This Bunker is already shared. Invite members from Members."
        )
    profile = _service_profile()
    setup_token = str(payload.get("setup_token") or "").strip()
    reference = credentials.find(server)
    if setup_token:
        result = _bunker_result(remote.HTTPRemoteAdapter(server).setup, setup_token, profile)
        reference = credentials.store(server, str(result["credential"]))
    elif not reference:
        raise commands.CommandRejected(
            "service_setup_required",
            "Paste the setup token printed by tb serve, or join this server from an invitation first.",
        )
    adapter = remote.HTTPRemoteAdapter(
        server, bearer=credentials.resolve(reference), credential_reference=reference
    )
    publication = _bunker_result(bunkerdb.local_publication, bunker_id)
    snapshot = _bunker_result(adapter.publish, publication)
    shared = _bunker_result(
        bunkerdb.share_bunker,
        bunker_id,
        server_url=server,
        snapshot=snapshot,
        credential_reference=reference,
    )
    return {"bunker": shared, "bunkers": bunkerdb.list_bunkers()}


@commands.register("application.bunker.invitation.create")
def command_bunker_invitation_create(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    bunker_id = str(payload.get("bunker_id") or "")
    adapter, connection = _remote_adapter(bunker_id)
    role = str(payload.get("role") or remote.ROLE_MEMBER)
    try:
        expires_in = int(payload.get("expires_in") or remote.DEFAULT_INVITATION_SECONDS)
    except (TypeError, ValueError) as exc:
        raise commands.CommandRejected("invitation_expiry_invalid", "Choose a valid expiry.") from exc
    invitation = _bunker_result(
        adapter.create_invitation,
        str(connection["remote_bunker_id"]),
        role=role,
        expires_in=expires_in,
    )
    link = _invitation_link(
        str(connection["server_url"]), str(connection["remote_bunker_id"]), str(invitation["token"])
    )
    return {
        "invitation": {
            "id": str(invitation.get("id") or ""),
            "role": str(invitation.get("role") or role),
            "expires_at": str(invitation.get("expires_at") or ""),
            "link": link,
        },
        "invitations": _bunker_result(adapter.list_invitations, str(connection["remote_bunker_id"])),
    }


@commands.register("application.bunker.invitations")
def command_bunker_invitations(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    adapter, connection = _remote_adapter(str(payload.get("bunker_id") or ""))
    return {
        "invitations": _bunker_result(adapter.list_invitations, str(connection["remote_bunker_id"]))
    }


@commands.register("application.bunker.invitation.revoke")
def command_bunker_invitation_revoke(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    adapter, connection = _remote_adapter(str(payload.get("bunker_id") or ""))
    _bunker_result(
        adapter.revoke_invitation,
        str(connection["remote_bunker_id"]),
        str(payload.get("invitation_id") or ""),
    )
    return {
        "invitations": _bunker_result(adapter.list_invitations, str(connection["remote_bunker_id"]))
    }


@commands.register("application.bunker.sync")
def command_bunker_sync(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Pull the service's current state into this replica."""
    bunker_id = str(payload.get("bunker_id") or "")
    adapter, connection = _remote_adapter(bunker_id)
    snapshot = _bunker_result(adapter.pull, str(connection["remote_bunker_id"]), "")
    applied = _bunker_result(bunkerdb.apply_snapshot, bunker_id, snapshot)
    return {
        "sync": applied,
        "bunker": bunkerdb.get_bunker(bunker_id),
        "bunkers": bunkerdb.list_bunkers(),
    }


@commands.register("application.bunker.members")
def command_bunker_members(
    _context: commands.CommandContext, payload: dict[str, Any]
) -> dict[str, Any]:
    bunker_id = str(payload.get("bunker_id") or "")
    adapter, connection = _remote_adapter(bunker_id)
    members = _bunker_result(adapter.members, str(connection["remote_bunker_id"]))
    bunker = _bunker_result(bunkerdb.get_bunker, bunker_id)
    return {"members": list(members), "sharing": bunker.get("sharing", {})}


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
