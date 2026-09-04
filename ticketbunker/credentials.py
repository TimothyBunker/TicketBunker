"""Machine-local secret references for TicketBunker services (DESIGN.md §3.1).

A Bunker database records only a credential *reference*. The secret behind it
lives here, in a mode-0600 JSON file inside the application data home, and is
handed to the HTTP adapter as an ephemeral bearer. An operating-system vault
backend can later replace this file without changing the reference contract
or any Bunker record.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from . import platform_services

REFERENCE_PREFIX = "local:"


def store_path() -> Path:
    return platform_services.current().data_home() / "credentials.json"


def _load() -> dict[str, dict[str, str]]:
    path = store_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): {str(k): str(v) for k, v in value.items()}
        for key, value in raw.items()
        if isinstance(value, dict)
    }


def _save(values: dict[str, dict[str, str]]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(values, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def store(server: str, secret: str, *, kind: str = "service") -> str:
    """Save one secret and return its reference. The secret is never returned."""
    value = str(secret or "")
    if not value:
        raise ValueError("A credential value is required.")
    values = _load()
    origin = str(server or "").strip()
    # One live credential per server and kind keeps sign-in state unambiguous.
    for reference, entry in list(values.items()):
        if entry.get("server") == origin and entry.get("kind") == kind:
            del values[reference]
    reference = f"{REFERENCE_PREFIX}{uuid.uuid4().hex}"
    values[reference] = {"kind": kind, "server": origin, "secret": value}
    _save(values)
    return reference


def resolve(reference: str) -> str:
    """Return the secret for a reference, or an empty string when unknown."""
    entry = _load().get(str(reference or ""))
    return str(entry.get("secret") or "") if entry else ""


def find(server: str, *, kind: str = "service") -> str:
    """Return the stored reference for a server, or an empty string."""
    origin = str(server or "").strip()
    for reference, entry in _load().items():
        if entry.get("server") == origin and entry.get("kind") == kind:
            return reference
    return ""


def forget(reference: str) -> None:
    values = _load()
    if str(reference or "") in values:
        del values[str(reference)]
        _save(values)


def describe(reference: str) -> dict[str, Any]:
    """Display-safe metadata for a reference; never includes the secret."""
    entry = _load().get(str(reference or ""))
    if not entry:
        return {"reference": str(reference or ""), "present": False}
    return {
        "reference": str(reference),
        "present": True,
        "kind": entry.get("kind", ""),
        "server": entry.get("server", ""),
        "path": str(store_path()),
    }


__all__ = ["describe", "find", "forget", "resolve", "store", "store_path"]
