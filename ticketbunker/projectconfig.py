"""Composable project configuration and publication commands."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from . import commands, config, handlers, project, types
from .adapters.gitrun import GitError, git
from .ledger import Ledger


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise commands.CommandRejected("config_invalid", f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise commands.CommandRejected("config_invalid", f"{path} must contain one JSON object")
    return value


def _ledger(source_root: Path) -> Ledger:
    return Ledger(Path(source_root) / ".git" / "ticketbunker-config.jsonl")


def _locator(source_root: Path) -> dict[str, Any]:
    value = project.load_locator(source_root)
    if value is None:
        raise commands.CommandRejected("not_initialized", "This repository is not initialized. Run 'tb init' first.")
    return value


def _paths(source_root: Path) -> tuple[dict[str, Any], Path, Path, str]:
    locator = _locator(source_root)
    key = str(locator.get("project") or "")
    root = project.project_config_dir(source_root, key)
    return locator, root / "composition.json", root / "mappings.json", key


def revision(source_root: Path) -> str:
    checkout = project.config_checkout(source_root)
    ledger = _ledger(source_root)
    return git(checkout, ["rev-parse", "HEAD"], ledger, "config_read").strip()


def _field_set_path(checkout: Path, set_id: str, version: int) -> Path:
    versioned = checkout / "global" / "field-sets" / set_id / f"{version}.json"
    if versioned.exists():
        return versioned
    # Read compatibility for companion repositories created by the first
    # preview, before field-set versions became immutable paths.
    return checkout / "global" / "field-sets" / f"{set_id}.json"


def _resolve_composition_fields(checkout: Path, composition: dict[str, Any]) -> list[dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    order: list[str] = []
    for item in composition.get("field_sets", []):
        set_path = _field_set_path(checkout, str(item.get("id") or ""), int(item.get("version") or 0))
        field_set = _read_json(set_path)
        if field_set.get("version") != item.get("version"):
            raise commands.CommandRejected(
                "field_set_version_missing",
                f"Field set {item.get('id')} version {item.get('version')} is not available.",
            )
        for value in field_set.get("fields", []):
            if not isinstance(value, dict) or not isinstance(value.get("key"), str):
                continue
            key = value["key"]
            if key in fields and fields[key] != value:
                raise commands.CommandRejected("field_collision", f"Field sets define incompatible versions of {key!r}.")
            if key not in order:
                order.append(key)
            fields[key] = dict(value)
            sources[key] = str(item.get("id") or "global")
    return [{**fields[key], "source": sources[key]} for key in order]


def resolved_fields(source_root: Path) -> list[dict[str, Any]]:
    _locator_value, composition_path, _mapping_path, _key = _paths(source_root)
    composition = _read_json(composition_path)
    checkout = project.config_checkout(source_root)
    resolved = _resolve_composition_fields(checkout, composition)
    fields = {field["key"]: field for field in resolved}
    order = [field["key"] for field in resolved]
    for value in composition.get("project_fields", []):
        key = value["key"]
        if key not in order:
            order.append(key)
        fields[key] = {**value, "source": "project"}
    for key, value in composition.get("field_overrides", {}).items():
        if key not in fields:
            raise commands.CommandRejected("field_override_missing", f"Cannot override unknown field {key!r}.")
        fields[key] = {**value, "source": fields[key].get("source", "project"), "overridden": True}
    removed = set(composition.get("removed_fields", []))
    return [fields[key] for key in order if key in fields and key not in removed]


def global_default_fields(source_root: Path) -> list[dict[str, Any]]:
    checkout = project.config_checkout(source_root)
    composition = _read_json(checkout / "global" / "default-composition.json")
    return _resolve_composition_fields(checkout, composition)


def identity_state(source_root: Path) -> dict[str, str]:
    ledger = _ledger(source_root)
    name = git(source_root, ["config", "--get", "user.name"], ledger, "identity_read", check=False).strip()
    email = git(source_root, ["config", "--get", "user.email"], ledger, "identity_read", check=False).strip()
    branch_user, source = config.get_user()
    return {"name": name, "email": email, "branch_user": branch_user or "", "branch_user_source": source}


def credential_file() -> Path:
    configured = config.load().get("credentials_file")
    if configured:
        return Path(str(configured)).expanduser()
    return Path.home() / ".config" / "ticketbunker" / "credentials.env"


def _credential_variable(provider: str) -> str:
    builtins = {
        "github": "GITHUB_ACCESS_TOKEN",
        "gitlab": "GITLAB_ACCESS_TOKEN",
        "jira": "JIRA_API_TOKEN",
    }
    if provider in builtins:
        return builtins[provider]
    safe = re.sub(r"[^A-Z0-9]+", "_", provider.upper()).strip("_")
    return f"TICKETBUNKER_{safe}_TOKEN"


def credential_state(providers: tuple[str, ...] = ("github", "gitlab", "jira")) -> dict[str, bool]:
    path = credential_file()
    names = set()
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                names.add(line.split("=", 1)[0].strip())
    return {
        provider: _credential_variable(provider) in names
        or bool(os.environ.get(_credential_variable(provider)))
        for provider in providers
    }


def credential_value(provider: str) -> str | None:
    """Read one local credential without exposing it through config state."""
    variable = _credential_variable(provider)
    if os.environ.get(variable):
        return os.environ[variable]
    path = credential_file()
    if not path.exists():
        return None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == variable:
            selected = value.strip()
            if len(selected) >= 2 and selected[0] == selected[-1] and selected[0] in {"'", '"'}:
                selected = selected[1:-1]
            return selected or None
    return None


def state(source_root: Path) -> dict[str, Any]:
    locator, composition_path, mappings_path, key = _paths(source_root)
    project_doc = _read_json(project.project_config_dir(source_root, key) / "project.json")
    mappings = _read_json(mappings_path)
    providers = tuple(str(value) for value in mappings.get("providers", {}))
    return {
        "project": project_doc,
        "locator": locator,
        "revision": revision(source_root),
        "identity": identity_state(source_root),
        "credentials_file": str(credential_file()),
        "credentials": credential_state(providers),
        "composition": _read_json(composition_path),
        "fields": resolved_fields(source_root),
        "global_fields": global_default_fields(source_root),
        "mappings": mappings,
    }


def _publish(
    source_root: Path,
    base_revision: str | None,
    message: str,
    mutate: Callable[[Path, Ledger, str], None],
) -> str:
    locator = _locator(source_root)
    branch = str(locator.get("config_branch") or project.DEFAULT_CONFIG_BRANCH)
    checkout = project.config_checkout(source_root)
    ledger = _ledger(source_root)
    dirty = git(checkout, ["status", "--porcelain"], ledger, "config_preflight").strip()
    if dirty:
        raise commands.CommandRejected(
            "config_checkout_dirty",
            "The local configuration checkout has unpublished changes. Finish or discard that draft before editing again.",
        )
    git(checkout, ["fetch", "--prune", "origin"], ledger, "config_preflight")
    head = git(checkout, ["rev-parse", "HEAD"], ledger, "config_preflight").strip()
    remote_head = git(checkout, ["rev-parse", f"origin/{branch}"], ledger, "config_preflight").strip()
    if head != remote_head:
        git(checkout, ["pull", "--ff-only", "origin", branch], ledger, "config_preflight")
        head = git(checkout, ["rev-parse", "HEAD"], ledger, "config_preflight").strip()
    if base_revision and base_revision != head:
        raise commands.CommandRejected(
            "config_stale",
            "Configuration changed remotely. Reload the editor and review the latest revision before publishing.",
        )
    key = str(locator.get("project") or "")
    mutate(project.project_config_dir(source_root, key), ledger, key)
    git(checkout, ["add", "-A"], ledger, "config_publish")
    if not git(checkout, ["status", "--porcelain"], ledger, "config_publish").strip():
        return head
    git(checkout, ["commit", "-m", message], ledger, "config_publish")
    try:
        git(checkout, ["push", "origin", f"HEAD:refs/heads/{branch}"], ledger, "config_publish")
    except GitError as exc:
        raise commands.CommandRejected(
            "config_publish_rejected",
            "The configuration is valid, but the Git remote rejected publication. Your local commit was preserved.\n"
            f"Git said: {exc.stderr.strip() or 'push rejected'}",
        ) from exc
    return git(checkout, ["rev-parse", "HEAD"], ledger, "config_publish").strip()


def _normalize_field(payload: dict[str, Any]) -> dict[str, Any]:
    field = {
        "key": str(payload.get("key") or "").strip(),
        "label": str(payload.get("label") or "").strip(),
        "type": str(payload.get("type") or "string").strip(),
        "required": bool(payload.get("required", False)),
    }
    if field["type"] == "enum":
        values = payload.get("values")
        if isinstance(values, str):
            field["values"] = [value.strip() for value in values.split(",") if value.strip()]
        elif values is not None:
            field["values"] = values
    return field


def _stored_field(field: dict[str, Any]) -> dict[str, Any]:
    return {
        key: field[key]
        for key in ("key", "label", "type", "required", "values")
        if key in field
    }


def _publish_global_template(source_root: Path, fields: list[dict[str, Any]], ledger: Ledger) -> None:
    checkout = project.config_checkout(source_root)
    default_path = checkout / "global" / "default-composition.json"
    current = _read_json(default_path)
    version = max(
        (int(item.get("version") or 0) for item in current.get("field_sets", []) if item.get("id") == "global.template"),
        default=0,
    ) + 1
    set_path = checkout / "global" / "field-sets" / "global.template" / f"{version}.json"
    field_set = {
        "schema_version": project.SCHEMA_VERSION,
        "id": "global.template",
        "version": version,
        "fields": [_stored_field(field) for field in fields],
    }
    handlers.intake_config_document("field_set", set_path, field_set, ledger, project="global")
    defaults = {
        "schema_version": project.SCHEMA_VERSION,
        "field_sets": [{"id": "global.template", "version": version}],
    }
    handlers.intake_config_document("default_composition", default_path, defaults, ledger, project="global")


@commands.register("identity.update")
def command_identity_update(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    ledger = _ledger(context.project_root)
    name = str(payload.get("name") or "").strip()
    email = str(payload.get("email") or "").strip()
    branch_user = config.sanitize_user(str(payload.get("branch_user") or ""))
    if not name:
        raise commands.CommandRejected("identity_name_required", "Enter the name Git should use for commits.")
    if not email or "@" not in email:
        raise commands.CommandRejected("identity_email_invalid", "Email needs an @ symbol.")
    if not branch_user:
        raise commands.CommandRejected("identity_branch_user_required", "Enter the identifier used in branch names.")
    git(context.project_root, ["config", "user.name", name], ledger, "identity_update")
    git(context.project_root, ["config", "user.email", email], ledger, "identity_update")
    local = config.load()
    local["user"] = branch_user
    config.save(local)
    return {"identity": identity_state(context.project_root)}


@commands.register("configuration.field.put")
def command_field_put(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    field = _normalize_field(payload)

    def mutate(root: Path, ledger: Ledger, key: str) -> None:
        path = root / "composition.json"
        composition = _read_json(path)
        existing_fields = resolved_fields(context.project_root)
        existing = next((value for value in existing_fields if value["key"] == field["key"]), None)
        project_fields = list(composition.get("project_fields", []))
        index = next((index for index, value in enumerate(project_fields) if value.get("key") == field["key"]), None)
        if index is not None:
            project_fields[index] = field
            composition["project_fields"] = project_fields
        elif existing is not None:
            composition.setdefault("field_overrides", {})[field["key"]] = field
        else:
            project_fields.append(field)
            composition["project_fields"] = project_fields
        composition["removed_fields"] = [value for value in composition.get("removed_fields", []) if value != field["key"]]
        handlers.intake_config_document("project_composition", path, composition, ledger, project=key)

    new_revision = _publish(
        context.project_root,
        payload.get("base_revision"),
        f"Configure {field['key']}",
        mutate,
    )
    return {"revision": new_revision, "fields": resolved_fields(context.project_root)}


@commands.register("configuration.field.remove")
def command_field_remove(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    field_key = str(payload.get("key") or "").strip()
    if not field_key:
        raise commands.CommandRejected("field_key_required", "Choose a field to remove.")

    def mutate(root: Path, ledger: Ledger, key: str) -> None:
        path = root / "composition.json"
        composition = _read_json(path)
        project_fields = list(composition.get("project_fields", []))
        remaining = [value for value in project_fields if value.get("key") != field_key]
        if len(remaining) != len(project_fields):
            composition["project_fields"] = remaining
        else:
            removed = list(composition.get("removed_fields", []))
            if field_key not in removed:
                removed.append(field_key)
            composition["removed_fields"] = removed
        composition.get("field_overrides", {}).pop(field_key, None)
        handlers.intake_config_document("project_composition", path, composition, ledger, project=key)

    new_revision = _publish(
        context.project_root,
        payload.get("base_revision"),
        f"Remove {field_key} from ticket composition",
        mutate,
    )
    return {"revision": new_revision, "fields": resolved_fields(context.project_root)}


@commands.register("configuration.global.field.put")
def command_global_field_put(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    field = _normalize_field(payload)

    def mutate(_root: Path, ledger: Ledger, _key: str) -> None:
        fields = global_default_fields(context.project_root)
        index = next((index for index, value in enumerate(fields) if value.get("key") == field["key"]), None)
        if index is None:
            fields.append(field)
        else:
            fields[index] = field
        _publish_global_template(context.project_root, fields, ledger)

    new_revision = _publish(
        context.project_root,
        payload.get("base_revision"),
        f"Configure global field {field['key']}",
        mutate,
    )
    return {"revision": new_revision, "global_fields": global_default_fields(context.project_root)}


@commands.register("configuration.global.field.remove")
def command_global_field_remove(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    field_key = str(payload.get("key") or "").strip()
    if not field_key:
        raise commands.CommandRejected("field_key_required", "Choose a global field to remove.")

    def mutate(_root: Path, ledger: Ledger, _key: str) -> None:
        fields = global_default_fields(context.project_root)
        remaining = [field for field in fields if field.get("key") != field_key]
        if len(remaining) == len(fields):
            raise commands.CommandRejected("field_key_unknown", f"Global defaults do not contain {field_key!r}.")
        _publish_global_template(context.project_root, remaining, ledger)

    new_revision = _publish(
        context.project_root,
        payload.get("base_revision"),
        f"Remove global field {field_key}",
        mutate,
    )
    return {"revision": new_revision, "global_fields": global_default_fields(context.project_root)}


_INTEGRATION_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")


def _integration_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    identifier = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
    if not _INTEGRATION_ID_RE.fullmatch(identifier):
        raise commands.CommandRejected(
            "integration_id_invalid",
            "Integration identifier must start with a letter and use only lowercase letters, numbers, dots, dashes, or underscores.",
        )
    return identifier


def _mapping_fields(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("fields")
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, list):
        pairs = []
        for row in raw:
            if not isinstance(row, dict):
                raise commands.CommandRejected("mapping_invalid", "Each mapping row must contain two fields.")
            pairs.append((row.get("ours"), row.get("theirs")))
    else:
        raise commands.CommandRejected("mapping_invalid", "Mappings must be a list of field pairs.")

    available = {field["key"] for field in resolved_fields(context.project_root)}
    result: dict[str, str] = {}
    for ours_value, theirs_value in pairs:
        ours = str(ours_value or "").strip()
        theirs = str(theirs_value or "").strip()
        if not ours or not theirs:
            raise commands.CommandRejected(
                "mapping_fields_required",
                "Every mapping row needs both a TicketBunker field and an integration field.",
            )
        if ours not in available:
            raise commands.CommandRejected(
                "mapping_field_unknown",
                f"TicketBunker field {ours!r} is not in this project composition.",
            )
        if ours in result:
            raise commands.CommandRejected(
                "mapping_field_duplicate",
                f"TicketBunker field {ours!r} appears more than once. Keep one row for each field.",
            )
        result[ours] = theirs
    return result


def _configured_integrations(source_root: Path) -> dict[str, Any]:
    providers = _read_json(_paths(source_root)[2]).get("providers", {})
    return providers if isinstance(providers, dict) else {}


@commands.register("configuration.integration.put")
def command_integration_put(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise commands.CommandRejected("integration_name_required", "Enter an integration name.")
    if len(name) > 80:
        raise commands.CommandRejected("integration_name_invalid", "Integration name must be 80 characters or fewer.")
    provider = _integration_id(payload.get("provider") or name)
    if provider in project.STANDARD_INTEGRATIONS:
        raise commands.CommandRejected(
            "integration_builtin",
            f"{project.STANDARD_INTEGRATIONS[provider]['label']} is already available as a preset.",
        )

    def mutate(root: Path, ledger: Ledger, key: str) -> None:
        path = root / "mappings.json"
        mappings = _read_json(path)
        providers = mappings.setdefault("providers", {})
        if provider in providers:
            raise commands.CommandRejected("integration_exists", f"Integration {provider!r} already exists.")
        providers[provider] = {
            "label": name,
            "template": "custom",
            "template_applied": True,
            "fields": {},
        }
        handlers.intake_config_document("project_mappings", path, mappings, ledger, project=key)

    new_revision = _publish(
        context.project_root,
        payload.get("base_revision"),
        f"Add {name} integration",
        mutate,
    )
    return {"revision": new_revision, "mappings": _read_json(_paths(context.project_root)[2])}


@commands.register("configuration.integration.remove")
def command_integration_remove(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    provider = _integration_id(payload.get("provider"))
    if provider in project.STANDARD_INTEGRATIONS:
        raise commands.CommandRejected("integration_builtin", "Preset integrations cannot be removed.")

    def mutate(root: Path, ledger: Ledger, key: str) -> None:
        path = root / "mappings.json"
        mappings = _read_json(path)
        providers = mappings.setdefault("providers", {})
        if provider not in providers:
            raise commands.CommandRejected("integration_unknown", f"Integration {provider!r} does not exist.")
        del providers[provider]
        handlers.intake_config_document("project_mappings", path, mappings, ledger, project=key)

    new_revision = _publish(
        context.project_root,
        payload.get("base_revision"),
        f"Remove {provider} integration",
        mutate,
    )
    return {"revision": new_revision, "mappings": _read_json(_paths(context.project_root)[2])}


@commands.register("configuration.mapping.replace")
def command_mapping_replace(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    provider = _integration_id(payload.get("provider"))
    if provider not in _configured_integrations(context.project_root):
        raise commands.CommandRejected("integration_unknown", f"Integration {provider!r} does not exist.")
    fields = _mapping_fields(context, payload)

    def mutate(root: Path, ledger: Ledger, key: str) -> None:
        path = root / "mappings.json"
        mappings = _read_json(path)
        settings = mappings.setdefault("providers", {})[provider]
        settings["fields"] = fields
        settings["template_applied"] = True
        handlers.intake_config_document("project_mappings", path, mappings, ledger, project=key)

    new_revision = _publish(
        context.project_root,
        payload.get("base_revision"),
        f"Configure {provider} field mappings",
        mutate,
    )
    return {"revision": new_revision, "mappings": _read_json(_paths(context.project_root)[2])}


@commands.register("configuration.mapping.put")
def command_mapping_put(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible one-pair command for CLI/API callers."""
    provider = _integration_id(payload.get("provider"))
    if provider not in _configured_integrations(context.project_root):
        raise commands.CommandRejected("integration_unknown", f"Integration {provider!r} does not exist.")
    fields = _mapping_fields(
        context,
        {"fields": [{"ours": payload.get("ours"), "theirs": payload.get("theirs")}]},
    )

    def mutate(root: Path, ledger: Ledger, key: str) -> None:
        path = root / "mappings.json"
        mappings = _read_json(path)
        settings = mappings.setdefault("providers", {})[provider]
        settings.setdefault("fields", {}).update(fields)
        settings["template_applied"] = True
        handlers.intake_config_document("project_mappings", path, mappings, ledger, project=key)

    ours = next(iter(fields))
    new_revision = _publish(
        context.project_root,
        payload.get("base_revision"),
        f"Map {ours} for {provider}",
        mutate,
    )
    return {"revision": new_revision, "mappings": _read_json(_paths(context.project_root)[2])}


@commands.register("credentials.update")
def command_credentials_update(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    provider = _integration_id(payload.get("provider"))
    token = str(payload.get("token") or "").strip()
    integrations: dict[str, Any] = dict(project.STANDARD_INTEGRATIONS)
    if project.load_locator(context.project_root) is not None:
        integrations.update(_configured_integrations(context.project_root))
    if provider not in integrations:
        raise commands.CommandRejected("integration_unknown", f"Integration {provider!r} does not exist.")
    if not token:
        raise commands.CommandRejected("credential_required", "Paste the token to save it locally.")
    if "\n" in token or "\r" in token:
        raise commands.CommandRejected("credential_invalid", "A token must be one line.")
    path = credential_file()
    try:
        path.resolve().relative_to(context.project_root.resolve())
    except ValueError:
        pass
    else:
        raise commands.CommandRejected(
            "credential_path_unsafe",
            "The credential file must live outside the source repository.",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    entries: dict[str, str] = {}
    comments: list[str] = []
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip().startswith("#") or "=" not in raw:
                comments.append(raw)
            else:
                key, value = raw.split("=", 1)
                entries[key.strip()] = value
    entries[_credential_variable(provider)] = token
    text = "# TicketBunker credentials. Never commit this file.\n"
    text += "\n".join(f"{key}={value}" for key, value in sorted(entries.items())) + "\n"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return {"credentials_file": str(path), "credentials": credential_state(tuple(integrations))}
