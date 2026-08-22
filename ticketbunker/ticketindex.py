"""Committed lightweight ticket references for companion catalog queries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from . import layout


SCHEMA_VERSION = 1
DESCRIPTION_LIMIT = 2000


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item).strip()
        for item in value
        if isinstance(item, (str, int, float)) and str(item).strip()
    ]


def summarize(bunker: Path, project_key: str, project_name: str) -> dict[str, Any] | None:
    """Build one bounded catalog entry without reading any report body."""
    paths = layout.paths(bunker)
    data = _read_json(paths["ticket"])
    ticket_id = str(data.get("id") or "")
    if not ticket_id:
        return None

    resolution = _read_json(paths["branch_resolution"])
    branch_names = {
        str(value)
        for field in ("branches", "assigned_missing")
        for value in resolution.get(field, [])
        if isinstance(value, str) and value
    }
    if not branch_names:
        branch_names.update(_strings(data.get("assigned_branches")))
    reports = paths["children"]
    report_count = len(tuple(reports.glob("*.md"))) if reports.is_dir() else 0
    author_per = _read_json(paths["authorship"]).get("per_branch", {})
    development = ""
    if isinstance(author_per, dict):
        development = max(
            (
                str(value.get("recency_of_development") or "")
                for value in author_per.values()
                if isinstance(value, dict)
            ),
            default="",
        )
    raw = paths["ticket"].read_bytes()
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return {
        "id": ticket_id,
        "title": str(data.get("title") or ""),
        "status": str(data.get("status") or ""),
        "type": str(data.get("type") or ""),
        "priority": str(data.get("priority") or ""),
        "assignee": str(data.get("assignee") or ""),
        "reporter": str(data.get("reporter") or ""),
        "labels": _strings(data.get("labels")),
        "updated": str(data.get("updated") or ""),
        "description_excerpt": str(data.get("description") or "")[:DESCRIPTION_LIMIT],
        "branch_count": len(branch_names),
        "report_count": report_count,
        "last_development": development,
        "project": project_key,
        "project_name": project_name,
        "reference": f"projects/{project_key}/tickets/{ticket_id}",
        "source_url": str(metadata.get("source_url") or ""),
        "ticket_digest": hashlib.sha256(raw).hexdigest(),
    }


def read(tickets_scope: Path) -> dict[str, Any] | None:
    value = _read_json(layout.ticket_index(tickets_scope))
    if value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("tickets"), list):
        return None
    return value


def write(
    tickets_scope: Path,
    project_key: str,
    project_name: str,
    tickets: Iterable[dict[str, Any]],
) -> Path:
    path = layout.ticket_index(tickets_scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    selected = sorted(
        (dict(value) for value in tickets if isinstance(value.get("id"), str)),
        key=lambda value: str(value["id"]).casefold(),
    )
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "project": {"key": project_key, "name": project_name},
                "tickets": selected,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def rebuild(tickets_scope: Path, project_key: str, project_name: str) -> Path:
    entries = []
    for bunker in layout.ticket_dirs(tickets_scope):
        summary = summarize(bunker, project_key, project_name)
        if summary is not None:
            entries.append(summary)
    return write(tickets_scope, project_key, project_name, entries)


def update(
    tickets_scope: Path,
    project_key: str,
    project_name: str,
    ticket_ids: Iterable[str],
) -> Path:
    existing = read(tickets_scope) or {"tickets": []}
    entries = {
        str(value.get("id")): dict(value)
        for value in existing.get("tickets", [])
        if isinstance(value, dict) and value.get("id")
    }
    for ticket_id in ticket_ids:
        bunker = layout.companion_bunker_path(tickets_scope, ticket_id)
        summary = summarize(bunker, project_key, project_name) if bunker.is_dir() else None
        if summary is None:
            entries.pop(ticket_id, None)
        else:
            entries[ticket_id] = summary
    return write(tickets_scope, project_key, project_name, entries.values())
