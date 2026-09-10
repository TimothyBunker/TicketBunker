"""Device-local Bunker databases and project hierarchy.

One Bunker is one configuration, membership, and synchronization boundary. On
each device it has exactly one SQLite database. Projects and subprojects live
inside that database; Git repositories are attached resources, never the
authority that discovers or authenticates the Bunker.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from . import config, layout, platform_services, project as companion, remote
from .processors import anchorcheck


SCHEMA_VERSION = 3
PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9-]{0,31}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
INTEGRATION_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")

DEFAULT_TICKET_FIELDS: tuple[dict[str, Any], ...] = (
    {"key": "title", "label": "Title", "type": "string", "required": True},
    {"key": "description", "label": "Description", "type": "text", "required": False},
    {"key": "type", "label": "Type", "type": "enum", "required": False, "values": ["Task", "Bug", "Story"]},
    {"key": "status", "label": "Status", "type": "string", "required": True},
    {"key": "priority", "label": "Priority", "type": "string", "required": False},
    {"key": "assignee", "label": "Assignee", "type": "user", "required": False},
    {"key": "reporter", "label": "Reporter", "type": "user", "required": False},
    {"key": "labels", "label": "Labels", "type": "tags", "required": False},
    {"key": "created", "label": "Created", "type": "datetime", "required": False},
    {"key": "updated", "label": "Updated", "type": "datetime", "required": False},
)
DEFAULT_INTEGRATIONS: dict[str, dict[str, Any]] = {
    "github": {
        "label": "GitHub Issues",
        "template": "github",
        "fields": {
            "title": "title",
            "description": "body",
            "status": "state",
            "assignee": "assignees[0].login",
            "reporter": "user.login",
            "labels": "labels[*].name",
            "created": "created_at",
            "updated": "updated_at",
        },
    },
    "gitlab": {
        "label": "GitLab Issues",
        "template": "gitlab",
        "fields": {
            "title": "title",
            "description": "description",
            "status": "state",
            "assignee": "assignees[0].username",
            "reporter": "author.username",
            "labels": "labels",
            "created": "created_at",
            "updated": "updated_at",
        },
    },
    "jira": {
        "label": "Jira Cloud",
        "template": "jira",
        "fields": {
            "title": "fields.summary",
            "description": "fields.description",
            "type": "fields.issuetype.name",
            "status": "fields.status.name",
            "priority": "fields.priority.name",
            "assignee": "fields.assignee.displayName",
            "reporter": "fields.reporter.displayName",
            "labels": "fields.labels",
            "created": "fields.created",
            "updated": "fields.updated",
        },
    },
}


class BunkerDatabaseError(ValueError):
    """A stable local-model rejection that is safe to show to the user."""


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back a context, then release its OS file handle."""

    def __exit__(self, *args: object) -> bool:
        try:
            return bool(super().__exit__(*args))
        finally:
            self.close()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def data_root() -> Path:
    return platform_services.current().data_home() / "bunkers"


def registry_path() -> Path:
    return data_root() / "registry.sqlite3"


def database_path(bunker_id: str) -> Path:
    if not SAFE_ID_RE.fullmatch(str(bunker_id or "")):
        raise BunkerDatabaseError("That Bunker identifier is not valid.")
    return data_root() / bunker_id / "bunker.sqlite3"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    connection = sqlite3.connect(path, factory=_ClosingConnection)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def _init_registry(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bunkers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            database_path TEXT NOT NULL UNIQUE,
            mode TEXT NOT NULL CHECK (mode IN ('local', 'remote')),
            remote_server TEXT NOT NULL DEFAULT '',
            remote_bunker_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            last_opened_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS bunkers_remote_idx
            ON bunkers(remote_server, remote_bunker_id)
            WHERE remote_server <> '' AND remote_bunker_id <> '';
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, _now()),
    )
    connection.commit()


def _init_bunker(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bunker (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('local', 'remote')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS remote_connection (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            server_url TEXT NOT NULL,
            remote_bunker_id TEXT NOT NULL,
            state TEXT NOT NULL,
            credential_reference TEXT NOT NULL DEFAULT '',
            config_revision TEXT NOT NULL DEFAULT '',
            sync_cursor TEXT NOT NULL DEFAULT '',
            last_synced_at TEXT NOT NULL DEFAULT '',
            current_member_id TEXT NOT NULL DEFAULT '',
            capabilities_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS members (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'active',
            capabilities_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            bunker_id TEXT NOT NULL REFERENCES bunker(id) ON DELETE CASCADE,
            parent_id TEXT REFERENCES projects(id) ON DELETE RESTRICT,
            key TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (bunker_id, key)
        );
        CREATE TABLE IF NOT EXISTS repository_bindings (
            id TEXT PRIMARY KEY,
            bunker_id TEXT NOT NULL REFERENCES bunker(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            name TEXT NOT NULL,
            canonical_remote TEXT NOT NULL DEFAULT '',
            default_branch TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS local_repositories (
            binding_id TEXT PRIMARY KEY REFERENCES repository_bindings(id) ON DELETE CASCADE,
            local_path TEXT NOT NULL,
            attached_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS config_documents (
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            revision TEXT NOT NULL,
            content_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (scope_type, scope_id, kind)
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            payload_json TEXT NOT NULL,
            source_kind TEXT NOT NULL DEFAULT 'manual',
            source_reference TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS local_project_sync (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            last_success_at TEXT NOT NULL DEFAULT '',
            last_attempt_at TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            attempt_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reports (
            ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            scope TEXT NOT NULL,
            repository_id TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (ticket_id, scope, repository_id)
        );
        CREATE TABLE IF NOT EXISTS migration_imports (
            id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_digest TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            detail_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS projects_parent_idx ON projects(parent_id, position, name);
        CREATE UNIQUE INDEX IF NOT EXISTS repositories_project_remote_idx
            ON repository_bindings(project_id, canonical_remote)
            WHERE canonical_remote <> '';
        CREATE INDEX IF NOT EXISTS local_repositories_path_idx
            ON local_repositories(local_path);
        CREATE INDEX IF NOT EXISTS tickets_project_idx ON tickets(project_id, updated_at DESC);
        """
    )
    # Version 2 treated one Git checkout as unique across an entire Bunker.
    # Projects are scopes, so one monorepo checkout may legitimately attach to
    # several Projects. Rebuild only the machine-local mapping table; shared
    # repository identities and source files remain untouched.
    table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'local_repositories'"
    ).fetchone()
    if table_sql is not None and "UNIQUE" in str(table_sql["sql"] or "").upper():
        connection.executescript(
            """
            ALTER TABLE local_repositories RENAME TO local_repositories_v2;
            CREATE TABLE local_repositories (
                binding_id TEXT PRIMARY KEY REFERENCES repository_bindings(id) ON DELETE CASCADE,
                local_path TEXT NOT NULL,
                attached_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            INSERT INTO local_repositories(binding_id, local_path, attached_at, last_seen_at)
                SELECT binding_id, local_path, attached_at, last_seen_at
                FROM local_repositories_v2;
            DROP TABLE local_repositories_v2;
            """
        )
    connection.execute("DROP INDEX IF EXISTS repositories_remote_idx")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS repositories_project_remote_idx
        ON repository_bindings(project_id, canonical_remote)
        WHERE canonical_remote <> ''
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS local_repositories_path_idx ON local_repositories(local_path)"
    )
    report_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(reports)").fetchall()
    }
    report_table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'reports'"
    ).fetchone()
    report_sql = "".join(str(report_table["sql"] or "").lower().split())
    if "repository_id" not in report_columns or "primarykey(ticket_id,scope)" in report_sql:
        connection.execute("ALTER TABLE reports RENAME TO reports_v2")
        connection.execute(
            """
            CREATE TABLE reports (
                ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                scope TEXT NOT NULL,
                repository_id TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (ticket_id, scope, repository_id)
            )
            """
        )
        repository_expression = "repository_id" if "repository_id" in report_columns else "''"
        connection.execute(
            f"""
            INSERT INTO reports(ticket_id, scope, repository_id, content, updated_at)
            SELECT ticket_id, scope, {repository_expression}, content, updated_at
            FROM reports_v2
            """
        )
        connection.execute("DROP TABLE reports_v2")
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, _now()),
    )
    bunker_row = connection.execute("SELECT id, mode FROM bunker LIMIT 1").fetchone()
    if bunker_row is not None:
        bunker_id = str(bunker_row["id"])
        if str(bunker_row["mode"]) == "local":
            _ensure_bunker_config(connection, bunker_id)
            for project_row in connection.execute("SELECT id FROM projects").fetchall():
                _copy_project_config(connection, bunker_id, str(project_row["id"]))
    connection.commit()


def _registry() -> sqlite3.Connection:
    connection = _connect(registry_path())
    _init_registry(connection)
    return connection


def _database(bunker_id: str) -> sqlite3.Connection:
    path = database_path(bunker_id)
    if not path.is_file():
        raise BunkerDatabaseError("That Bunker is not available on this device.")
    connection = _connect(path)
    _init_bunker(connection)
    return connection


def _clean_name(raw: Any, label: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise BunkerDatabaseError(f"Enter a {label} name.")
    if len(value) > 120:
        raise BunkerDatabaseError(f"The {label} name must be 120 characters or fewer.")
    return value


def _project_key(raw: Any) -> str:
    value = str(raw or "").strip().upper()
    if not PROJECT_KEY_RE.fullmatch(value):
        raise BunkerDatabaseError(
            "Project keys use uppercase letters, numbers, and hyphens, starting with a letter."
        )
    return value


def _id(raw: Any, label: str) -> str:
    value = str(raw or "").strip()
    if not SAFE_ID_RE.fullmatch(value):
        raise BunkerDatabaseError(f"The {label} identifier is not valid.")
    return value


def _register(
    bunker_id: str,
    name: str,
    path: Path,
    *,
    mode: str,
    remote_server: str = "",
    remote_bunker_id: str = "",
) -> None:
    with _registry() as registry:
        registry.execute(
            """
            INSERT INTO bunkers(
                id, name, database_path, mode, remote_server,
                remote_bunker_id, created_at, last_opened_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bunker_id,
                name,
                str(path),
                mode,
                remote_server,
                remote_bunker_id,
                _now(),
                _now(),
            ),
        )


def _remote_registered(
    server: str, remote_bunker_id: str, *, exclude_bunker_id: str = ""
) -> bool:
    with _registry() as registry:
        row = registry.execute(
            """
            SELECT id FROM bunkers
            WHERE remote_server = ? AND remote_bunker_id = ?
            """,
            (server, remote_bunker_id),
        ).fetchone()
        return row is not None and str(row["id"]) != exclude_bunker_id


def _discard_new_database(path: Path) -> None:
    """Remove only a database created by the currently failing operation."""
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def _seed_config(connection: sqlite3.Connection, bunker_id: str) -> None:
    now = _now()
    defaults = {
        "schema_version": 1,
        "field_sets": [
            {"id": "ticket.standard", "version": 1},
            {"id": "agile.workflow", "version": 1},
            {"id": "ticket.people", "version": 1},
            {"id": "ticket.tags", "version": 1},
        ],
    }
    connection.execute(
        """
        INSERT INTO config_documents(
            scope_type, scope_id, kind, revision, content_json, updated_at
        ) VALUES ('bunker', ?, 'default-composition', 'local:1', ?, ?)
        """,
        (bunker_id, json.dumps(defaults, sort_keys=True), now),
    )
    connection.executemany(
        """
        INSERT INTO config_documents(
            scope_type, scope_id, kind, revision, content_json, updated_at
        ) VALUES ('bunker', ?, ?, 'local:1', ?, ?)
        """,
        (
            (
                bunker_id,
                "ticket-fields",
                json.dumps({"fields": list(DEFAULT_TICKET_FIELDS)}, sort_keys=True),
                now,
            ),
            (
                bunker_id,
                "integrations",
                json.dumps({"providers": DEFAULT_INTEGRATIONS}, sort_keys=True),
                now,
            ),
        ),
    )


def _ensure_bunker_config(connection: sqlite3.Connection, bunker_id: str) -> None:
    """Add standalone configuration documents to databases from preview builds."""
    now = _now()
    values = (
        (
            bunker_id,
            "ticket-fields",
            json.dumps({"fields": list(DEFAULT_TICKET_FIELDS)}, sort_keys=True),
            now,
        ),
        (
            bunker_id,
            "integrations",
            json.dumps({"providers": DEFAULT_INTEGRATIONS}, sort_keys=True),
            now,
        ),
    )
    connection.executemany(
        """
        INSERT OR IGNORE INTO config_documents(
            scope_type, scope_id, kind, revision, content_json, updated_at
        ) VALUES ('bunker', ?, ?, 'local:1', ?, ?)
        """,
        values,
    )


def _copy_project_config(
    connection: sqlite3.Connection, bunker_id: str, project_id: str
) -> None:
    """Pin the current Bunker defaults for one newly created Project."""
    now = _now()
    rows = connection.execute(
        """
        SELECT kind, content_json FROM config_documents
        WHERE scope_type = 'bunker' AND scope_id = ?
          AND kind IN ('ticket-fields', 'integrations')
        """,
        (bunker_id,),
    ).fetchall()
    for row in rows:
        connection.execute(
            """
            INSERT OR IGNORE INTO config_documents(
                scope_type, scope_id, kind, revision, content_json, updated_at
            ) VALUES ('project', ?, ?, 'local:1', ?, ?)
            """,
            (project_id, row["kind"], row["content_json"], now),
        )


def create_bunker(
    name: str,
    *,
    project_key: str,
    project_name: str = "",
) -> dict[str, Any]:
    """Create one local-only Bunker and its first root project."""
    bunker_name = _clean_name(name, "Bunker")
    key = _project_key(project_key)
    root_name = _clean_name(project_name or bunker_name, "project")
    bunker_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    path = database_path(bunker_id)
    now = _now()
    connection = _connect(path)
    try:
        _init_bunker(connection)
        with connection:
            connection.execute(
                "INSERT INTO bunker(id, name, mode, created_at, updated_at) VALUES (?, ?, 'local', ?, ?)",
                (bunker_id, bunker_name, now, now),
            )
            connection.execute(
                """
                INSERT INTO projects(
                    id, bunker_id, parent_id, key, name, created_at, updated_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?)
                """,
                (project_id, bunker_id, key, root_name, now, now),
            )
            _seed_config(connection, bunker_id)
            _copy_project_config(connection, bunker_id, project_id)
        _register(bunker_id, bunker_name, path, mode="local")
    except Exception:
        connection.close()
        _discard_new_database(path)
        raise
    finally:
        connection.close()
    return get_bunker(bunker_id)


def _project_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, parent_id, key, name, description, position, created_at, updated_at
        FROM projects ORDER BY position, name COLLATE NOCASE, key
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _project_tree(
    rows: Iterable[Mapping[str, Any]],
    ticket_counts: Mapping[str, int] | None = None,
    trackers: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Nest Project rows and give each node its ticket count.

    A Project's ``ticket_count`` includes its descendants, matching what the
    ticket catalog shows when that Project is selected (``_project_scope``).
    """
    counts = dict(ticket_counts or {})
    sources = dict(trackers or {})
    nodes = {}
    for row in rows:
        project_id = str(row["id"])
        linked = [dict(item) for item in sources.get(project_id) or []]
        nodes[project_id] = {
            **dict(row),
            "children": [],
            "ticket_count": int(counts.get(project_id, 0)),
            "tracker": linked[0] if linked else None,
            "trackers": linked,
        }
    roots: list[dict[str, Any]] = []
    for node in nodes.values():
        parent_id = str(node.get("parent_id") or "")
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)

    def accumulate(node: dict[str, Any]) -> int:
        node["ticket_count"] += sum(accumulate(child) for child in node["children"])
        return int(node["ticket_count"])

    for root in roots:
        accumulate(root)
    return roots


def _bunker_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT id, name, mode, created_at, updated_at FROM bunker LIMIT 1"
    ).fetchone()
    if row is None:
        raise BunkerDatabaseError("The local Bunker database is incomplete.")
    connection_row = connection.execute(
        """
        SELECT server_url, remote_bunker_id, state, config_revision,
               sync_cursor, last_synced_at, current_member_id, capabilities_json
        FROM remote_connection WHERE singleton = 1
        """
    ).fetchone()
    projects = _project_rows(connection)
    syncs = {str(item["project_id"]): dict(item) for item in connection.execute(
        "SELECT project_id, last_success_at, last_attempt_at, status, error FROM local_project_sync"
    )}
    for project_row in projects:
        project_row["ticket_sync"] = syncs.get(str(project_row["id"]))
    repositories = [
        dict(item)
        for item in connection.execute(
            """
            SELECT rb.id, rb.project_id, rb.name, rb.canonical_remote,
                   rb.default_branch, lr.local_path, lr.attached_at,
                   lr.last_seen_at AS last_fetched_at
            FROM repository_bindings rb
            LEFT JOIN local_repositories lr ON lr.binding_id = rb.id
            ORDER BY rb.name COLLATE NOCASE
            """
        ).fetchall()
    ]
    tickets = int(connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
    ticket_counts = {
        str(item["project_id"]): int(item["total"])
        for item in connection.execute(
            "SELECT project_id, COUNT(*) AS total FROM tickets GROUP BY project_id"
        ).fetchall()
    }
    trackers: dict[str, list[dict[str, Any]]] = {}
    tracker_rows = connection.execute(
        """
        SELECT scope_id, kind, content_json FROM config_documents
        WHERE scope_type = 'project' AND kind IN ('tracker-source', 'tracker-sources')
        """
    ).fetchall()
    for item in tracker_rows:
        if str(item["kind"]) == "tracker-sources":
            raw = _payload_object(item["content_json"]).get("sources")
            trackers[str(item["scope_id"])] = [dict(value) for value in raw if isinstance(value, Mapping)] if isinstance(raw, list) else []
    for item in tracker_rows:
        scope_id = str(item["scope_id"])
        if str(item["kind"]) == "tracker-source" and scope_id not in trackers:
            single = _payload_object(item["content_json"])
            trackers[scope_id] = [{**single, "id": "legacy"}] if single else []
    connections_row = connection.execute(
        """
        SELECT content_json FROM config_documents
        WHERE scope_type = 'bunker' AND kind = 'connections'
        """
    ).fetchone()
    connections = (
        _payload_object(connections_row["content_json"]).get("providers")
        if connections_row is not None
        else {}
    )
    remote_value = dict(connection_row) if connection_row else None
    membership = None
    if remote_value is not None:
        try:
            remote_value["capabilities"] = json.loads(
                str(remote_value.pop("capabilities_json"))
            )
        except (TypeError, ValueError):
            remote_value["capabilities"] = []
        member_id = str(remote_value.get("current_member_id") or "")
        if member_id:
            member_row = connection.execute(
                """
                SELECT id, display_name, email, role, state, capabilities_json
                FROM members WHERE id = ?
                """,
                (member_id,),
            ).fetchone()
            if member_row is not None:
                membership = dict(member_row)
                try:
                    membership["capabilities"] = json.loads(
                        str(membership.pop("capabilities_json"))
                    )
                except (TypeError, ValueError):
                    membership["capabilities"] = []
    return {
        **dict(row),
        "database": str(connection.execute("PRAGMA database_list").fetchone()[2]),
        "remote": remote_value,
        "membership": membership,
        "sharing": _sharing(str(row["mode"]), remote_value, membership),
        "connections": connections if isinstance(connections, dict) else {},
        "projects": _project_tree(projects, ticket_counts, trackers),
        "project_count": len(projects),
        "repositories": repositories,
        "repository_count": len(repositories),
        "repository_registry": _repository_registry(connection, str(row["id"])),
        "ticket_count": tickets,
    }


def _sharing(
    mode: str,
    remote_value: Mapping[str, Any] | None,
    membership: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Display-safe membership facts for the current device user."""
    if mode == "local" or remote_value is None:
        return {
            "state": "local",
            "role": remote.ROLE_OWNER,
            "is_owner": True,
            "can_share": True,
            "can_invite": False,
            "can_manage_members": False,
            "server": "",
        }
    capabilities = set(remote_value.get("capabilities") or [])
    role = str((membership or {}).get("role") or "")
    return {
        "state": str(remote_value.get("state") or ""),
        "role": role,
        "is_owner": role == remote.ROLE_OWNER,
        "can_share": False,
        "can_invite": "members:invite" in capabilities,
        "can_manage_members": "members:manage" in capabilities,
        "server": str(remote_value.get("server_url") or ""),
    }


def remote_connection(bunker_id: str) -> dict[str, Any]:
    """The service relationship, including the credential reference (never a secret)."""
    bunker_id = _id(bunker_id, "Bunker")
    with _database(bunker_id) as connection:
        mode_row = connection.execute("SELECT mode FROM bunker WHERE id = ?", (bunker_id,)).fetchone()
        if mode_row is None:
            raise BunkerDatabaseError("That Bunker is not available on this device.")
        row = connection.execute(
            """
            SELECT server_url, remote_bunker_id, state, credential_reference,
                   config_revision, sync_cursor, current_member_id, capabilities_json
            FROM remote_connection WHERE singleton = 1
            """
        ).fetchone()
    if str(mode_row["mode"]) != "remote" or row is None:
        raise BunkerDatabaseError("This Bunker is local. Share it before using service actions.")
    try:
        capabilities = json.loads(str(row["capabilities_json"] or "[]"))
    except ValueError:
        capabilities = []
    return {
        "server_url": str(row["server_url"]),
        "remote_bunker_id": str(row["remote_bunker_id"]),
        "state": str(row["state"]),
        "credential_reference": str(row["credential_reference"] or ""),
        "config_revision": str(row["config_revision"] or ""),
        "sync_cursor": str(row["sync_cursor"] or ""),
        "current_member_id": str(row["current_member_id"] or ""),
        "capabilities": [str(item) for item in capabilities if isinstance(item, str)],
    }


def _record_acceptance(connection: sqlite3.Connection, accepted: Mapping[str, Any]) -> None:
    connection.execute(
        """
        UPDATE remote_connection
        SET sync_cursor = COALESCE(NULLIF(?, ''), sync_cursor),
            config_revision = COALESCE(NULLIF(?, ''), config_revision),
            last_synced_at = ?
        WHERE singleton = 1
        """,
        (str(accepted.get("cursor") or ""), str(accepted.get("revision") or ""), _now()),
    )


def _mode(connection: sqlite3.Connection, bunker_id: str) -> str:
    row = connection.execute("SELECT mode FROM bunker WHERE id = ?", (bunker_id,)).fetchone()
    if row is None:
        raise BunkerDatabaseError("That Bunker is not available on this device.")
    return str(row["mode"] or "local")


def _require_acceptance(mode: str, accepted: Mapping[str, Any] | None) -> None:
    if mode == "remote" and not accepted:
        raise BunkerDatabaseError(
            "Shared changes must be accepted by the Bunker's service before they apply here."
        )


def _remote_scope_id(connection: sqlite3.Connection, scope_type: str, scope_id: str, mode: str) -> str:
    """Bunker-scope documents are addressed by the remote Bunker id on the service."""
    if scope_type != "bunker" or mode != "remote":
        return scope_id
    row = connection.execute(
        "SELECT remote_bunker_id FROM remote_connection WHERE singleton = 1"
    ).fetchone()
    return str(row["remote_bunker_id"]) if row else scope_id


def _next_revision(current_revision: str, mode: str) -> str:
    if mode == "remote":
        return f"shared:{uuid.uuid4().hex[:12]}"
    match = re.fullmatch(r"local:(\d+)", current_revision)
    return f"local:{int(match.group(1)) + 1}" if match else f"local:{uuid.uuid4()}"


def get_bunker(bunker_id: str) -> dict[str, Any]:
    bunker_id = _id(bunker_id, "Bunker")
    with _database(bunker_id) as connection:
        value = _bunker_summary(connection)
    with _registry() as registry:
        registry.execute(
            "UPDATE bunkers SET last_opened_at = ? WHERE id = ?", (_now(), bunker_id)
        )
    return value


def list_bunkers() -> list[dict[str, Any]]:
    with _registry() as registry:
        rows = registry.execute(
            """
            SELECT id, name, database_path, mode, remote_server,
                   remote_bunker_id, created_at, last_opened_at
            FROM bunkers ORDER BY last_opened_at DESC, name COLLATE NOCASE
            """
        ).fetchall()
    values: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        path = Path(str(item["database_path"]))
        if not path.is_file():
            values.append({**item, "available": False, "projects": [], "repositories": []})
            continue
        try:
            with _connect(path) as connection:
                _init_bunker(connection)
                values.append({**_bunker_summary(connection), "available": True})
        except (sqlite3.Error, BunkerDatabaseError):
            values.append({**item, "available": False, "projects": [], "repositories": []})
    return values


def _config_scope(
    connection: sqlite3.Connection,
    bunker_id: str,
    project_id: str | None,
) -> tuple[str, str, dict[str, Any]]:
    bunker_row = connection.execute(
        "SELECT id, mode FROM bunker WHERE id = ?", (bunker_id,)
    ).fetchone()
    if bunker_row is None:
        raise BunkerDatabaseError("That Bunker is not available on this device.")
    if project_id:
        selected = _id(project_id, "project")
        if connection.execute(
            "SELECT 1 FROM projects WHERE id = ? AND bunker_id = ?",
            (selected, bunker_id),
        ).fetchone() is None:
            raise BunkerDatabaseError("Choose a project from this Bunker.")
        return "project", selected, dict(bunker_row)
    return "bunker", bunker_id, dict(bunker_row)


def _config_permission(
    connection: sqlite3.Connection, bunker: Mapping[str, Any]
) -> dict[str, Any]:
    if str(bunker.get("mode") or "local") == "local":
        return {
            "can_write": True,
            "reason": "This local Bunker is controlled on this device.",
            "capabilities": ["config:read", "config:write"],
        }
    row = connection.execute(
        """
        SELECT state, server_url, capabilities_json
        FROM remote_connection WHERE singleton = 1
        """
    ).fetchone()
    capabilities: list[str] = []
    if row is not None:
        try:
            value = json.loads(str(row["capabilities_json"] or "[]"))
            if isinstance(value, list):
                capabilities = [str(item) for item in value if isinstance(item, str)]
        except ValueError:
            pass
    server = str(row["server_url"] or "the remote service") if row else "the remote service"
    state = str(row["state"] or "") if row else ""
    # A cached capability only decides what the editor offers. Every shared
    # write is still sent to the service first and applied locally only after
    # the service accepts it (apply_*_change requires that acceptance).
    if state == "ready" and "config:write" in capabilities:
        return {
            "can_write": True,
            "reason": f"Changes are saved on {server} before they apply here.",
            "capabilities": capabilities,
        }
    if state != "ready":
        reason = f"Shared configuration is controlled by {server}; reconnect to it before saving changes."
    else:
        reason = f"Shared configuration is controlled by {server}; only a Bunker owner can change it."
    return {"can_write": False, "reason": reason, "capabilities": capabilities}


def get_configuration(
    bunker_id: str, *, project_id: str | None = None
) -> dict[str, Any]:
    """Return one pinned Bunker-default or Project configuration snapshot."""
    bunker_id = _id(bunker_id, "Bunker")
    with _database(bunker_id) as connection:
        scope_type, scope_id, bunker = _config_scope(
            connection, bunker_id, project_id
        )
        rows = connection.execute(
            """
            SELECT kind, revision, content_json, updated_at
            FROM config_documents
            WHERE scope_type = ? AND scope_id = ?
              AND kind IN ('ticket-fields', 'integrations', 'tracker-source', 'tracker-sources', 'connections', 'checks', 'repository-registry')
            ORDER BY kind
            """,
            (scope_type, scope_id),
        ).fetchall()
        documents: dict[str, Any] = {}
        for row in rows:
            content = _payload_object(row["content_json"])
            documents[str(row["kind"])] = {
                "kind": str(row["kind"]),
                "revision": str(row["revision"]),
                "updated_at": str(row["updated_at"]),
                "content": content,
            }
        permission = _config_permission(connection, bunker)
        repositories = (
            _project_repository_configuration(connection, scope_id)
            if scope_type == "project"
            else {"revision": "", "items": []}
        )
        registry = _repository_registry(connection, bunker_id)
        if scope_type == "bunker":
            repositories = registry
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "documents": documents,
        "permission": permission,
        "composition": "pinned" if scope_type == "project" else "defaults-for-new-projects",
        "repositories": repositories,
        "repository_registry": registry,
    }


def _repository_registry(connection: sqlite3.Connection, bunker_id: str) -> dict[str, Any]:
    from .repository_registry import from_bindings
    row = connection.execute("SELECT revision, content_json FROM config_documents WHERE scope_type='bunker' AND scope_id=? AND kind='repository-registry'", (bunker_id,)).fetchone()
    content = json.loads(row["content_json"]) if row else from_bindings(_repository_rows(connection))
    return {"revision": str(row["revision"]) if row else "", "items": [
        {**value, "id": value["canonical_remote"], "project_id": "", "local_path": None}
        for value in content.get("items", [])]}


_FIELD_TYPES = {
    "string",
    "text",
    "number",
    "boolean",
    "date",
    "datetime",
    "user",
    "enum",
    "tags",
    "url",
}


def _validated_ticket_fields(content: Mapping[str, Any]) -> dict[str, Any]:
    raw = content.get("fields")
    if not isinstance(raw, list):
        raise BunkerDatabaseError("Ticket fields must be a list.")
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, Mapping):
            raise BunkerDatabaseError("Each ticket field must be an object.")
        key = str(value.get("key") or "").strip().lower()
        label = str(value.get("label") or "").strip()
        field_type = str(value.get("type") or "string").strip().lower()
        if not FIELD_KEY_RE.fullmatch(key):
            raise BunkerDatabaseError(
                "Field keys start with a letter and use lowercase letters, numbers, dots, dashes, or underscores."
            )
        if key in seen:
            raise BunkerDatabaseError(f"Ticket field {key!r} appears more than once.")
        if not label or len(label) > 80:
            raise BunkerDatabaseError("Each ticket field needs a label of 80 characters or fewer.")
        if field_type not in _FIELD_TYPES:
            raise BunkerDatabaseError(f"Ticket field {key!r} has an unsupported type.")
        field: dict[str, Any] = {
            "key": key,
            "label": label,
            "type": field_type,
            "required": bool(value.get("required", False)),
        }
        if field_type == "enum":
            values = value.get("values")
            if not isinstance(values, list):
                raise BunkerDatabaseError(f"Enum field {key!r} needs a list of values.")
            selected = [str(item).strip() for item in values if str(item).strip()]
            if len(selected) != len(set(selected)):
                raise BunkerDatabaseError(f"Enum field {key!r} contains duplicate values.")
            field["values"] = selected
        fields.append(field)
        seen.add(key)
    return {"fields": fields}


def _validated_integrations(
    content: Mapping[str, Any], available_fields: set[str]
) -> dict[str, Any]:
    raw = content.get("providers")
    if not isinstance(raw, Mapping):
        raise BunkerDatabaseError("Integrations must be an object keyed by provider.")
    providers: dict[str, Any] = {}
    for raw_id, value in raw.items():
        provider = str(raw_id or "").strip().lower()
        if not INTEGRATION_ID_RE.fullmatch(provider):
            raise BunkerDatabaseError(
                "Integration identifiers start with a letter and use lowercase letters, numbers, dots, dashes, or underscores."
            )
        if not isinstance(value, Mapping):
            raise BunkerDatabaseError(f"Integration {provider!r} must be an object.")
        label = str(value.get("label") or "").strip()
        if not label or len(label) > 80:
            raise BunkerDatabaseError(
                f"Integration {provider!r} needs a label of 80 characters or fewer."
            )
        raw_fields = value.get("fields")
        if not isinstance(raw_fields, Mapping):
            raise BunkerDatabaseError(f"Integration {provider!r} needs a field matrix.")
        fields: dict[str, str] = {}
        for ours_value, theirs_value in raw_fields.items():
            ours = str(ours_value or "").strip().lower()
            theirs = str(theirs_value or "").strip()
            if ours not in available_fields:
                raise BunkerDatabaseError(
                    f"Integration {provider!r} maps unknown TicketBunker field {ours!r}."
                )
            if not theirs or len(theirs) > 256 or "\n" in theirs or "\r" in theirs:
                raise BunkerDatabaseError(
                    f"Integration {provider!r} needs one valid external path for {ours!r}."
                )
            fields[ours] = theirs
        providers[provider] = {
            "label": label,
            "template": str(value.get("template") or "custom")[:80],
            "fields": fields,
        }
    return {"providers": providers}


def propose_configuration(
    bunker_id: str,
    *,
    project_id: str | None,
    kind: str,
    expected_revision: str,
    content: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one complete document replacement and return the typed change.

    Nothing is written. Local Bunkers apply the proposal immediately; shared
    Bunkers send ``proposal["change"]`` to their service first.
    """
    bunker_id = _id(bunker_id, "Bunker")
    document_kind = str(kind or "").strip()
    if document_kind not in {"ticket-fields", "integrations", "tracker-source", "tracker-sources", "connections", "checks", "repository-registry"}:
        raise BunkerDatabaseError("Choose Ticket fields, Integrations, Boards, Connections, Repositories, or Checks to save.")
    if not isinstance(content, Mapping):
        raise BunkerDatabaseError("Configuration content must be one object.")
    with _database(bunker_id) as connection:
        scope_type, scope_id, bunker = _config_scope(
            connection, bunker_id, project_id
        )
        if document_kind in {"tracker-source", "tracker-sources"} and scope_type != "project":
            raise BunkerDatabaseError("Boards belong to one Project, not to Bunker defaults.")
        if document_kind in {"connections", "repository-registry"} and scope_type != "bunker":
            raise BunkerDatabaseError("Tracker connections and allowed repositories belong to the Bunker, not to one Project.")
        mode = str(bunker.get("mode") or "local")
        permission = _config_permission(connection, bunker)
        if not permission["can_write"]:
            raise BunkerDatabaseError(str(permission["reason"]))
        current = connection.execute(
            """
            SELECT revision FROM config_documents
            WHERE scope_type = ? AND scope_id = ? AND kind = ?
            """,
            (scope_type, scope_id, document_kind),
        ).fetchone()
        if current is None and document_kind not in CREATABLE_DOCUMENTS | {"repository-registry"}:
            raise BunkerDatabaseError("That configuration document is missing. Reopen the editor.")
        current_revision = str(current["revision"]) if current is not None else ""
        if current is None:
            if str(expected_revision or ""):
                raise BunkerDatabaseError(
                    "Configuration changed after this editor opened. Reload it and review the latest revision."
                )
        elif not expected_revision or expected_revision != current_revision:
            raise BunkerDatabaseError(
                "Configuration changed after this editor opened. Reload it and review the latest revision."
            )
        if document_kind == "repository-registry":
            from .repository_registry import validate
            try:
                normalized = validate(content, _repository_rows(connection))
            except ValueError as exc:
                raise BunkerDatabaseError(str(exc)) from exc
        elif document_kind == "tracker-source":
            normalized = _validated_tracker_source(content)
        elif document_kind == "tracker-sources":
            normalized = _validated_tracker_sources(content)
        elif document_kind == "connections":
            normalized = _validated_connections(content)
        elif document_kind == "checks":
            normalized = _validated_checks(content)
        elif document_kind == "ticket-fields":
            normalized = _validated_ticket_fields(content)
            available = {str(value["key"]) for value in normalized["fields"]}
            integration_row = connection.execute(
                """
                SELECT content_json FROM config_documents
                WHERE scope_type = ? AND scope_id = ? AND kind = 'integrations'
                """,
                (scope_type, scope_id),
            ).fetchone()
            integration_content = (
                _payload_object(integration_row["content_json"])
                if integration_row is not None
                else {}
            )
            providers = integration_content.get("providers")
            used = sorted(
                {
                    str(field)
                    for settings in (
                        providers.values() if isinstance(providers, Mapping) else []
                    )
                    if isinstance(settings, Mapping)
                    for field in (
                        settings.get("fields", {}).keys()
                        if isinstance(settings.get("fields"), Mapping)
                        else []
                    )
                    if str(field) not in available
                }
            )
            if used:
                raise BunkerDatabaseError(
                    "Remove integration mappings for these fields before deleting or renaming them: "
                    + ", ".join(used)
                    + "."
                )
        else:
            field_row = connection.execute(
                """
                SELECT content_json FROM config_documents
                WHERE scope_type = ? AND scope_id = ? AND kind = 'ticket-fields'
                """,
                (scope_type, scope_id),
            ).fetchone()
            fields = (
                _payload_object(field_row["content_json"]).get("fields", [])
                if field_row is not None
                else []
            )
            available = {
                str(value.get("key") or "")
                for value in fields
                if isinstance(value, Mapping)
            }
            normalized = _validated_integrations(content, available)
        raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        if len(raw.encode("utf-8")) > 1024 * 1024:
            raise BunkerDatabaseError("That configuration document is too large.")
        revision = _next_revision(current_revision or "local:0", mode)
        remote_scope = _remote_scope_id(connection, scope_type, scope_id, mode)
    change = remote.change_record(
        key=uuid.uuid4().hex,
        kind="config-document",
        record_id=f"{scope_type}:{remote_scope}:{document_kind}",
        expected_revision=current_revision,
        revision=revision,
        payload={
            "scope_type": scope_type,
            "scope_id": remote_scope,
            "kind": document_kind,
            "content": normalized,
        },
    )
    return {
        "mode": mode,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "project_id": project_id or None,
        "kind": document_kind,
        "content": normalized,
        "expected_revision": current_revision,
        "revision": revision,
        "change": change,
    }


def apply_configuration_change(
    bunker_id: str,
    proposal: Mapping[str, Any],
    *,
    accepted: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a proposed document. Shared Bunkers require service acceptance."""
    bunker_id = _id(bunker_id, "Bunker")
    raw = json.dumps(proposal["content"], sort_keys=True, separators=(",", ":"))
    with _database(bunker_id) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_acceptance(_mode(connection, bunker_id), accepted)
        if proposal.get("kind") == "repository-registry":
            from .repository_registry import validate
            try:
                validate(proposal["content"], _repository_rows(connection))
            except ValueError as exc:
                raise BunkerDatabaseError(str(exc)) from exc
        if not str(proposal.get("expected_revision") or ""):
            try:
                changed = connection.execute(
                    """
                    INSERT INTO config_documents(
                        scope_type, scope_id, kind, revision, content_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(proposal["scope_type"]),
                        str(proposal["scope_id"]),
                        str(proposal["kind"]),
                        str(proposal["revision"]),
                        raw,
                        _now(),
                    ),
                ).rowcount
            except sqlite3.IntegrityError:
                changed = 0
        else:
            changed = connection.execute(
                """
                UPDATE config_documents
                SET revision = ?, content_json = ?, updated_at = ?
                WHERE scope_type = ? AND scope_id = ? AND kind = ? AND revision = ?
                """,
                (
                    str(proposal["revision"]),
                    raw,
                    _now(),
                    str(proposal["scope_type"]),
                    str(proposal["scope_id"]),
                    str(proposal["kind"]),
                    str(proposal["expected_revision"]),
                ),
            ).rowcount
        if not changed:
            raise BunkerDatabaseError(
                "Configuration changed after this editor opened. Reload it and review the latest revision."
            )
        if accepted:
            _record_acceptance(connection, accepted)
    return get_configuration(bunker_id, project_id=proposal.get("project_id") or None)


def replace_configuration(
    bunker_id: str,
    *,
    project_id: str | None,
    kind: str,
    expected_revision: str,
    content: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace one complete configuration document on a local Bunker."""
    proposal = propose_configuration(
        bunker_id,
        project_id=project_id,
        kind=kind,
        expected_revision=expected_revision,
        content=content,
    )
    if proposal["mode"] == "remote":
        raise BunkerDatabaseError(
            "This Bunker is shared. Publish configuration through the application so the service can accept it."
        )
    return apply_configuration_change(bunker_id, proposal)


TRACKER_PROVIDERS = {"github": "GitHub"}
CONNECTION_PROVIDERS = {"github": "GitHub", "gitlab": "GitLab", "jira": "Jira"}
TRACKER_KINDS = {"project": "Project board", "issues": "Repository issues"}
CREATABLE_DOCUMENTS = {"checks", "tracker-source", "tracker-sources", "connections"}
MAX_TRACKER_SOURCES = 20
_GITHUB_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def _validated_tracker_source(content: Mapping[str, Any]) -> dict[str, Any]:
    """One Project's tracker board: which agile source its tickets come from."""
    provider = str(content.get("provider") or "").strip().lower()
    if provider not in TRACKER_PROVIDERS:
        raise BunkerDatabaseError(
            "Choose a supported tracker. This version syncs GitHub; GitLab and Jira follow."
        )
    kind = str(content.get("kind") or "").strip().lower()
    if kind not in TRACKER_KINDS:
        raise BunkerDatabaseError("Choose a Project board or a repository's issues.")
    owner = str(content.get("owner") or "").strip()
    if not _GITHUB_OWNER_RE.fullmatch(owner):
        raise BunkerDatabaseError("Enter the GitHub user or organization that owns the board.")
    number = 0
    repository = ""
    if kind == "project":
        try:
            number = int(content.get("number") or 0)
        except (TypeError, ValueError) as exc:
            raise BunkerDatabaseError("Enter the GitHub Project number, such as 8.") from exc
        if number <= 0:
            raise BunkerDatabaseError("Enter the GitHub Project number, such as 8.")
    else:
        repository = str(content.get("repository") or "").strip()
        if "/" in repository:
            repository = repository.split("/")[-1]
        if not _GITHUB_REPO_RE.fullmatch(repository):
            raise BunkerDatabaseError("Enter the repository name whose issues to sync.")
    title = " ".join(str(content.get("title") or "").split())[:200]
    url = str(content.get("url") or "").strip()[:500]
    if url and not url.startswith("https://"):
        raise BunkerDatabaseError("A board link must start with https://.")
    return {
        "provider": provider,
        "kind": kind,
        "owner": owner,
        "number": number,
        "repository": repository,
        "title": title,
        "url": url,
        "include_comments": bool(content.get("include_comments", True)),
    }


def _validated_tracker_sources(content: Mapping[str, Any]) -> dict[str, Any]:
    """Every board one Project mirrors; each keeps a stable id for unlinking."""
    raw = content.get("sources")
    if not isinstance(raw, list):
        raise BunkerDatabaseError("Linked boards must be a list.")
    if len(raw) > MAX_TRACKER_SOURCES:
        raise BunkerDatabaseError(f"A Project may link at most {MAX_TRACKER_SOURCES} boards.")
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int, str]] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise BunkerDatabaseError("Each linked board must be an object.")
        validated = _validated_tracker_source(item)
        key = (validated["provider"], validated["kind"], validated["owner"], validated["number"], validated["repository"])
        if key in seen:
            raise BunkerDatabaseError(f"{tracker_summary(validated)} is linked twice.")
        seen.add(key)
        raw_id = str(item.get("id") or "").strip()
        validated["id"] = _id(raw_id, "board") if raw_id else str(uuid.uuid4())
        sources.append(validated)
    return {"sources": sources}


def tracker_sources(documents: Mapping[str, Any]) -> tuple[list[dict[str, Any]], str, str]:
    """The linked boards of a configuration snapshot: (sources, kind, revision).

    ``tracker-sources`` is canonical; a single ``tracker-source`` written by
    an earlier version is read as a one-item list until it is rewritten.
    """
    plural = documents.get("tracker-sources")
    if isinstance(plural, Mapping):
        content = plural.get("content") if isinstance(plural.get("content"), Mapping) else {}
        raw = content.get("sources") if isinstance(content, Mapping) else []
        return (
            [dict(item) for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else [],
            "tracker-sources",
            str(plural.get("revision") or ""),
        )
    single = documents.get("tracker-source")
    if isinstance(single, Mapping) and isinstance(single.get("content"), Mapping) and single["content"]:
        source = dict(single["content"])
        source.setdefault("id", "legacy")
        return [source], "tracker-source", str(single.get("revision") or "")
    return [], "tracker-sources", ""


def trackers_summary(sources: Iterable[Mapping[str, Any]]) -> str:
    items = [tracker_summary(source) for source in sources]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{items[0]} +{len(items) - 1} more"


CHECK_COMMAND_MAX = 500
CHECK_TIMEOUT_DEFAULT = 600
CHECK_TIMEOUT_RANGE = (30, 3600)


def _validated_check_commands(content: Mapping[str, Any], label: str = "") -> dict[str, str]:
    commands: dict[str, str] = {}
    for name in ("build", "test"):
        value = content.get(name)
        if value is None or value == "":
            commands[name] = ""
            continue
        if not isinstance(value, str) or "\n" in value:
            raise BunkerDatabaseError(f"The {label}{name} check must be one command line.")
        if len(value) > CHECK_COMMAND_MAX:
            raise BunkerDatabaseError(f"Keep the {label}{name} check under {CHECK_COMMAND_MAX} characters.")
        commands[name] = value.strip()
    return commands


CHECK_TICKET_MAX = 200


def _validated_checks(content: Mapping[str, Any]) -> dict[str, Any]:
    """Build and test commands the pipeline runs in a scratch worktree at the
    branch head before the agent writes. Commands are the team's own, run in
    their own checkout; the document only bounds their shape and duration.

    ``discover`` lets the pipeline fall back to commands detected from the
    repository's manifests when none are configured. ``tickets`` holds
    per-ticket overrides keyed by ticket id, for the ticket whose build is
    narrower or different from the project's.
    """
    normalized: dict[str, Any] = dict(_validated_check_commands(content))
    normalized["discover"] = bool(content.get("discover", False))
    tickets_raw = content.get("tickets") or {}
    if not isinstance(tickets_raw, Mapping):
        raise BunkerDatabaseError("Per-ticket checks must be keyed by ticket id.")
    if len(tickets_raw) > CHECK_TICKET_MAX:
        raise BunkerDatabaseError(f"Keep per-ticket checks under {CHECK_TICKET_MAX} tickets.")
    tickets: dict[str, dict[str, str]] = {}
    for ticket_id, value in tickets_raw.items():
        key = str(ticket_id or "").strip()
        if not key or not isinstance(value, Mapping):
            raise BunkerDatabaseError("Per-ticket checks must be keyed by ticket id.")
        commands = _validated_check_commands(value, label=f"{key} ")
        if commands["build"] or commands["test"]:
            tickets[key] = commands
    normalized["tickets"] = tickets
    timeout = content.get("timeout_seconds", CHECK_TIMEOUT_DEFAULT)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError) as exc:
        raise BunkerDatabaseError("The check timeout must be a whole number of seconds.") from exc
    low, high = CHECK_TIMEOUT_RANGE
    if not low <= timeout <= high:
        raise BunkerDatabaseError(f"Choose a check timeout between {low} and {high} seconds.")
    normalized["timeout_seconds"] = timeout
    return normalized


def _validated_connections(content: Mapping[str, Any]) -> dict[str, Any]:
    """Bunker-level tracker connections: which account each provider is linked as."""
    raw = content.get("providers")
    if not isinstance(raw, Mapping):
        raise BunkerDatabaseError("Connections must map providers to accounts.")
    providers: dict[str, Any] = {}
    for key, value in raw.items():
        provider = str(key or "").strip().lower()
        if provider not in CONNECTION_PROVIDERS:
            raise BunkerDatabaseError(f"Unknown tracker provider {provider!r}.")
        if not isinstance(value, Mapping):
            raise BunkerDatabaseError(f"The {provider} connection must be an object.")
        account = " ".join(str(value.get("account") or "").split())[:100]
        if not account:
            raise BunkerDatabaseError(f"The {provider} connection needs an account.")
        providers[provider] = {
            "account": account,
            "name": " ".join(str(value.get("name") or "").split())[:120],
            "connected_at": str(value.get("connected_at") or "")[:40],
            "connected_by": " ".join(str(value.get("connected_by") or "").split())[:120],
        }
    return {"providers": providers}


def tracker_summary(source: Mapping[str, Any] | None) -> str:
    if not source:
        return ""
    label = TRACKER_PROVIDERS.get(str(source.get("provider") or ""), str(source.get("provider") or ""))
    title = str(source.get("title") or "").strip()
    if title:
        return f"{label} · {title}"
    if str(source.get("kind")) == "project":
        return f"{label} Project #{source.get('number')} · {source.get('owner')}"
    return f"{label} issues · {source.get('owner')}/{source.get('repository')}"


_CLOSED_STATUSES = {
    "closed",
    "complete",
    "completed",
    "done",
    "resolved",
    "cancelled",
    "canceled",
}


def _payload_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _person(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(
            value.get("display_name")
            or value.get("displayName")
            or value.get("name")
            or value.get("login")
            or ""
        )
    return str(value or "")


def _labels(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("labels", payload.get("tags", []))
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    for item in raw:
        label = _person(item).strip()
        if label and label not in values:
            values.append(label)
    return values


def _ticket_reference(row: Mapping[str, Any], project: Mapping[str, Any]) -> dict[str, Any]:
    payload = _payload_object(row.get("payload_json"))
    ticket_id = str(row.get("id") or payload.get("id") or "")
    return {
        "id": ticket_id,
        "title": str(payload.get("title") or payload.get("summary") or ticket_id),
        "status": str(payload.get("status") or payload.get("state") or "open"),
        "type": str(payload.get("type") or payload.get("issue_type") or "task"),
        "priority": str(payload.get("priority") or ""),
        "assignee": _person(payload.get("assignee")),
        "reporter": _person(payload.get("reporter") or payload.get("author")),
        "labels": _labels(payload),
        "updated": str(
            payload.get("updated") or payload.get("updated_at") or row.get("updated_at") or ""
        ),
        "project": {
            "id": str(project.get("id") or ""),
            "key": str(project.get("key") or ""),
            "name": str(project.get("name") or ""),
        },
        "source_kind": str(row.get("source_kind") or "manual"),
        "source_reference": str(row.get("source_reference") or ""),
    }


def _project_scope(
    rows: list[dict[str, Any]], project_id: str | None
) -> tuple[set[str], dict[str, dict[str, Any]]]:
    by_id = {str(row["id"]): row for row in rows}
    if not project_id:
        return set(by_id), by_id
    selected = _id(project_id, "project")
    if selected not in by_id:
        raise BunkerDatabaseError("Choose a project from this Bunker.")
    allowed = {selected}
    changed = True
    while changed:
        changed = False
        for row in rows:
            row_id = str(row["id"])
            if row_id not in allowed and str(row.get("parent_id") or "") in allowed:
                allowed.add(row_id)
                changed = True
    return allowed, by_id


def list_tickets(
    bunker_id: str,
    *,
    project_id: str | None = None,
    search: str = "",
    status: str = "active",
    assignee: str = "",
    reporter: str = "",
    label: str = "",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """Return a bounded provider-neutral page of ticket references."""
    bunker_id = _id(bunker_id, "Bunker")
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    query = str(search or "").strip().casefold()
    status_filter = str(status or "active").strip().casefold()
    assignee_filter = str(assignee or "").strip().casefold()
    reporter_filter = str(reporter or "").strip().casefold()
    label_filter = str(label or "").strip().casefold()
    with _database(bunker_id) as connection:
        project_rows = _project_rows(connection)
        allowed, projects = _project_scope(project_rows, project_id)
        rows = connection.execute(
            """
            SELECT id, project_id, payload_json, source_kind,
                   source_reference, updated_at
            FROM tickets ORDER BY updated_at DESC, id COLLATE NOCASE
            """
        ).fetchall()
    scoped = [
        _ticket_reference(dict(row), projects[str(row["project_id"])])
        for row in rows
        if str(row["project_id"]) in allowed
    ]
    facets = {
        "statuses": sorted({item["status"] for item in scoped if item["status"]}),
        "assignees": sorted({item["assignee"] for item in scoped if item["assignee"]}),
        "reporters": sorted({item["reporter"] for item in scoped if item["reporter"]}),
        "labels": sorted({value for item in scoped for value in item["labels"]}),
    }

    def matches(item: Mapping[str, Any]) -> bool:
        item_status = str(item.get("status") or "").casefold()
        if status_filter == "active" and item_status in _CLOSED_STATUSES:
            return False
        if status_filter not in {"", "active", "all"} and item_status != status_filter:
            return False
        if assignee_filter and str(item.get("assignee") or "").casefold() != assignee_filter:
            return False
        if reporter_filter and str(item.get("reporter") or "").casefold() != reporter_filter:
            return False
        labels = [str(value).casefold() for value in item.get("labels") or []]
        if label_filter and label_filter not in labels:
            return False
        if query:
            project = item.get("project") if isinstance(item.get("project"), Mapping) else {}
            haystack = " ".join(
                [
                    str(item.get("id") or ""),
                    str(item.get("title") or ""),
                    str(item.get("assignee") or ""),
                    str(item.get("reporter") or ""),
                    str(project.get("key") or ""),
                    str(project.get("name") or ""),
                    *[str(value) for value in item.get("labels") or []],
                ]
            ).casefold()
            if query not in haystack:
                return False
        return True

    selected = [item for item in scoped if matches(item)]
    total = len(selected)
    page_count = max(1, math.ceil(total / page_size))
    page = min(page, page_count)
    start = (page - 1) * page_size
    return {
        "tickets": selected[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
        "facets": facets,
    }


def _repository_branches(
    repository: Mapping[str, Any], ticket_id: str, extra_names: Iterable[str] = ()
) -> list[dict[str, Any]]:
    """One entry per branch name that carries the ticket key or is named.

    ``extra_names`` are branches tied to the ticket by a report or an explicit
    assignment even though their names carry no key. Local heads and
    remote-tracking refs for the same name fold into one row that says where
    the branch exists (local, origin, both), which one is checked out, how far
    it is ahead of and behind the Project's base, and who last committed to
    it. The read touches only local refs; it never fetches.
    """
    named = {str(value) for value in extra_names if str(value)}
    raw_path = str(repository.get("local_path") or "")
    if not raw_path:
        return []
    root = Path(raw_path)
    current = _git_value(root, "branch", "--show-current")
    listing = _git_value(
        root,
        "for-each-ref",
        "--format=%(refname:short)%09%(refname)%09%(objectname:short)%09%(committerdate:iso-strict)%09%(authorname)%09%(upstream:short)",
        "refs/heads",
        "refs/remotes",
    )
    from .processors.stages import relate_branches
    repository_id = str(repository.get("id") or "")
    repository_name = str(repository.get("name") or root.name)
    groups: dict[str, dict[str, Any]] = {}
    for line in listing.splitlines():
        # The final line loses its trailing tab to the output strip when the
        # last field (upstream) is empty; pad rather than drop that ref.
        parts = (line.split("\t") + [""] * 6)[:6]
        if not parts[0] or not parts[1]:
            continue
        short, full, sha, date, author, upstream = parts
        if short.endswith("/HEAD"):
            continue
        is_remote = full.startswith("refs/remotes/")
        name = short
        remote_name = ""
        if is_remote:
            remote_name, _, name = short.partition("/")
            if not name:
                continue
        related, _, _ = relate_branches([name], ticket_id, list(named))
        if not related:
            continue
        entry = groups.setdefault(
            name,
            {
                "name": name,
                "current": False,
                "local": False,
                "remote": False,
                "remotes": [],
                "upstream": "",
                "commit": "",
                "last_commit_at": "",
                "last_author": "",
                "ahead": None,
                "behind": None,
                "base": "",
                "repository_id": repository_id,
                "repository_name": repository_name,
            },
        )
        if is_remote:
            entry["remote"] = True
            if remote_name not in entry["remotes"]:
                entry["remotes"].append(remote_name)
            if not entry["local"]:
                entry.update(commit=sha, last_commit_at=date, last_author=author)
        else:
            entry["local"] = True
            entry["current"] = name == current
            entry["upstream"] = upstream
            entry.update(commit=sha, last_commit_at=date, last_author=author)
    base = str(repository.get("default_branch") or "main")
    base_ref = ""
    if _git_value(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}"):
        base_ref = base
    elif _git_value(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{base}"):
        base_ref = f"origin/{base}"
    for entry in groups.values():
        ref = entry["name"] if entry["local"] else f"{entry['remotes'][0]}/{entry['name']}"
        if base_ref and ref != base_ref:
            counts = _git_value(root, "rev-list", "--left-right", "--count", f"{base_ref}...{ref}")
            pieces = counts.split()
            if len(pieces) == 2 and all(piece.isdigit() for piece in pieces):
                entry["behind"] = int(pieces[0])
                entry["ahead"] = int(pieces[1])
                entry["base"] = base_ref
    return sorted(
        groups.values(),
        key=lambda item: (0 if item["current"] else 1, item["last_commit_at"] and -_iso_order(item["last_commit_at"]), item["name"]),
    )


def _iso_order(value: str) -> float:
    try:
        return datetime.datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


def get_ticket(bunker_id: str, ticket_id: str) -> dict[str, Any]:
    """Return one complete local ticket snapshot without network activity."""
    bunker_id = _id(bunker_id, "Bunker")
    ticket_id = _id(ticket_id, "ticket")
    with _database(bunker_id) as connection:
        row = connection.execute(
            """
            SELECT id, project_id, payload_json, source_kind,
                   source_reference, updated_at
            FROM tickets WHERE id = ?
            """,
            (ticket_id,),
        ).fetchone()
        if row is None:
            raise BunkerDatabaseError("That ticket is not available in this Bunker.")
        project_row = connection.execute(
            """
            SELECT id, parent_id, key, name, description, position,
                   created_at, updated_at FROM projects WHERE id = ?
            """,
            (row["project_id"],),
        ).fetchone()
        if project_row is None:
            raise BunkerDatabaseError("The ticket's project is missing.")
        report_rows = connection.execute(
            """
            SELECT scope, repository_id, content, updated_at FROM reports
            WHERE ticket_id = ?
            ORDER BY CASE scope WHEN 'combined' THEN 0 ELSE 1 END, scope, repository_id
            """,
            (ticket_id,),
        ).fetchall()
        repository_rows = connection.execute(
            """
            SELECT rb.id, rb.name, rb.canonical_remote, rb.default_branch,
                   lr.local_path, lr.attached_at,
                   lr.last_seen_at AS last_fetched_at
            FROM repository_bindings rb
            LEFT JOIN local_repositories lr ON lr.binding_id = rb.id
            WHERE rb.project_id = ? ORDER BY rb.name COLLATE NOCASE
            """,
            (row["project_id"],),
        ).fetchall()
    payload = _payload_object(row["payload_json"])
    project = dict(project_row)
    reference = _ticket_reference(dict(row), project)
    reports = [dict(value) for value in report_rows]
    repositories = [dict(value) for value in repository_rows]
    assigned = payload.get("assigned_branches")
    branches: list[dict[str, Any]] = []
    for repository in repositories:
        work = payload.get("branch_assignments") or {}
        if not isinstance(work, dict):
            work = {}
        scoped_names = {str(value["scope"]).removeprefix("branch:") for value in reports
                        if value.get("repository_id") == repository["id"] and value["scope"].startswith("branch:")}
        names = work.get(repository["id"], [])
        scoped_names.update(str(value) for value in (names if isinstance(names, list) else []))
        scoped_names.update(str(value) for value in (assigned if isinstance(assigned, list) else []))
        branches.extend(_repository_branches(repository, ticket_id, scoped_names))
    known = {(str(value["repository_id"]), str(value["name"])) for value in branches}
    repositories_by_id = {str(value["id"]): value for value in repositories}
    for report in reports:
        scope = str(report.get("scope") or "")
        if not scope.startswith("branch:"):
            continue
        name = scope.removeprefix("branch:")
        repository_id = str(report.get("repository_id") or "")
        marker = (repository_id, name)
        if marker not in known:
            repository = repositories_by_id.get(repository_id, {})
            branches.append(
                {
                    "name": name,
                    "current": False,
                    "local": False,
                    "remote": False,
                    "remotes": [],
                    "upstream": "",
                    "commit": "",
                    "last_commit_at": "",
                    "last_author": "",
                    "ahead": None,
                    "behind": None,
                    "base": "",
                    "repository_id": repository_id,
                    "repository_name": str(repository.get("name") or ""),
                }
            )
            known.add(marker)
    return {
        "ticket": {**payload, **reference},
        "project": project,
        "repositories": repositories,
        "branches": branches,
        "reports": reports,
        "comments": payload.get("comments") if isinstance(payload.get("comments"), list) else [],
        "activity": (
            payload.get("activity") if isinstance(payload.get("activity"), list) else []
        ),
    }


def imported_companion_sources() -> set[str]:
    """Return device-local legacy roots already copied into a Bunker."""
    sources: set[str] = set()
    with _registry() as registry:
        rows = registry.execute("SELECT database_path FROM bunkers").fetchall()
    for row in rows:
        path = Path(str(row["database_path"]))
        if not path.is_file():
            continue
        try:
            with _connect(path) as connection:
                _init_bunker(connection)
                imported = connection.execute(
                    """
                    SELECT source_path FROM migration_imports
                    WHERE source_kind = 'companion-git'
                    """
                ).fetchall()
                sources.update(str(value["source_path"]) for value in imported)
        except sqlite3.Error:
            continue
    return sources


def propose_project(
    bunker_id: str,
    *,
    key: str,
    name: str,
    parent_id: str | None = None,
    description: str = "",
    repository_remotes: list[str] | None = None,
) -> dict[str, Any]:
    """Validate a new Project and return the typed changes that create it.

    The Project pins the current Bunker defaults, so the proposal carries the
    copied ticket-field and integration documents alongside the Project row.
    """
    bunker_id = _id(bunker_id, "Bunker")
    project_id = str(uuid.uuid4())
    project_key = _project_key(key)
    project_name = _clean_name(name, "project")
    parent = _id(parent_id, "parent project") if parent_id else None
    with _database(bunker_id) as connection:
        mode = _mode(connection, bunker_id)
        if mode == "remote":
            bunker = connection.execute("SELECT id, mode FROM bunker WHERE id = ?", (bunker_id,)).fetchone()
            permission = _config_permission(connection, dict(bunker))
            if not permission["can_write"]:
                raise BunkerDatabaseError(str(permission["reason"]))
        if parent and connection.execute(
            "SELECT 1 FROM projects WHERE id = ? AND bunker_id = ?",
            (parent, bunker_id),
        ).fetchone() is None:
            raise BunkerDatabaseError("Choose a parent project from this Bunker.")
        if connection.execute(
            "SELECT 1 FROM projects WHERE bunker_id = ? AND key = ?", (bunker_id, project_key)
        ).fetchone() is not None:
            raise BunkerDatabaseError(f"Project key {project_key} already exists in this Bunker.")
        defaults = connection.execute(
            """
            SELECT kind, content_json FROM config_documents
            WHERE scope_type = 'bunker' AND scope_id = ?
              AND kind IN ('ticket-fields', 'integrations')
            ORDER BY kind
            """,
            (bunker_id,),
        ).fetchall()
        registry = _repository_registry(connection, bunker_id)
        repositories = select_registry_rows(registry, [] if repository_remotes is None else repository_remotes)
        repositories = [{**row, "id": str(uuid.uuid4()), "project_id": project_id} for row in repositories]
    project = {
        "id": project_id,
        "parent_id": parent or "",
        "key": project_key,
        "name": project_name,
        "description": str(description or ""),
        "position": 0,
    }
    documents = [
        {"kind": str(row["kind"]), "content": _payload_object(row["content_json"]), "revision": "local:1"}
        for row in defaults
    ]
    changes = [
        remote.change_record(
            key=uuid.uuid4().hex,
            kind="project",
            record_id=project_id,
            expected_revision="",
            revision="1",
            payload=project,
        )
    ]
    for document in documents:
        changes.append(
            remote.change_record(
                key=uuid.uuid4().hex,
                kind="config-document",
                record_id=f"project:{project_id}:{document['kind']}",
                expected_revision="",
                revision=document["revision"],
                payload={
                    "scope_type": "project",
                    "scope_id": project_id,
                    "kind": document["kind"],
                    "content": document["content"],
                },
            )
        )
    if repositories:
        changes.append(remote.change_record(key=uuid.uuid4().hex, kind="repository-bindings", record_id=project_id,
            expected_revision=remote.repository_list_revision([]), revision=remote.repository_list_revision(repositories), payload=repositories))
    return {"mode": mode, "project": project, "documents": documents, "repositories": repositories, "changes": changes}


def select_registry_rows(registry: Mapping[str, Any], remotes: Any) -> list[dict[str, Any]]:
    """Resolve selections to allowed identities; never interpret a local path."""
    if not isinstance(remotes, list) or any(not isinstance(value, str) for value in remotes):
        raise BunkerDatabaseError("Select repositories from the Bunker registry.")
    remotes = [canonical_remote(value) for value in remotes]
    rows = {row["canonical_remote"]: row for row in registry["items"]}
    if len(set(remotes)) != len(remotes) or any(value not in rows for value in remotes):
        raise BunkerDatabaseError("Select repositories from the Bunker registry.")
    return [rows[value] for value in remotes]


def apply_project_change(
    bunker_id: str,
    proposal: Mapping[str, Any],
    *,
    accepted: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a proposed Project. Shared Bunkers require service acceptance."""
    bunker_id = _id(bunker_id, "Bunker")
    project = proposal["project"]
    now = _now()
    try:
        with _database(bunker_id) as connection:
            connection.execute("BEGIN IMMEDIATE")
            _require_acceptance(_mode(connection, bunker_id), accepted)
            select_registry_rows(_repository_registry(connection, bunker_id), [row["canonical_remote"] for row in proposal.get("repositories", [])])
            connection.execute(
                """
                INSERT INTO projects(
                    id, bunker_id, parent_id, key, name, description, position, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(project["id"]),
                    bunker_id,
                    str(project.get("parent_id") or "") or None,
                    str(project["key"]),
                    str(project["name"]),
                    str(project.get("description") or ""),
                    int(project.get("position") or 0),
                    now,
                    now,
                ),
            )
            for document in proposal.get("documents") or []:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO config_documents(
                        scope_type, scope_id, kind, revision, content_json, updated_at
                    ) VALUES ('project', ?, ?, ?, ?, ?)
                    """,
                    (
                        str(project["id"]),
                        str(document["kind"]),
                        str(document.get("revision") or "local:1"),
                        json.dumps(document["content"], sort_keys=True),
                        now,
                    ),
                )
            for repository in proposal.get("repositories", []):
                connection.execute("INSERT INTO repository_bindings(id,bunker_id,project_id,name,canonical_remote,default_branch,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (repository["id"], bunker_id, project["id"], repository["name"], repository["canonical_remote"], repository["default_branch"], now, now))
            if accepted:
                _record_acceptance(connection, accepted)
    except sqlite3.IntegrityError as exc:
        raise BunkerDatabaseError(
            f"Project key {project['key']} already exists in this Bunker."
        ) from exc
    return get_bunker(bunker_id)


def create_project(
    bunker_id: str,
    *,
    key: str,
    name: str,
    parent_id: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    proposal = propose_project(
        bunker_id, key=key, name=name, parent_id=parent_id, description=description
    )
    if proposal["mode"] == "remote":
        raise BunkerDatabaseError(
            "This Bunker is shared. Add projects through the application so the service can accept them."
        )
    return apply_project_change(bunker_id, proposal)


def _git_root(raw: str) -> Path:
    selected = Path(str(raw or "")).expanduser()
    if not selected.is_dir():
        raise BunkerDatabaseError("Choose an existing Git project folder.")
    process = subprocess.run(
        ["git", "-C", str(selected), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        **platform_services.current().headless_process_options(),
    )
    if process.returncode != 0 or not process.stdout.strip():
        raise BunkerDatabaseError("Choose a folder inside a Git working tree.")
    return Path(process.stdout.strip()).resolve()


def _git_value(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        **platform_services.current().headless_process_options(),
    )
    return process.stdout.strip() if process.returncode == 0 else ""


def canonical_remote(raw: str) -> str:
    """Return a credential-free, transport-neutral repository identity."""
    value = str(raw or "").strip()
    if not value or value.startswith(("/", "./", "../", "~")):
        return ""
    if any(ord(character) < 33 for character in value):
        return ""
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return ""
    if "://" not in value:
        match = re.fullmatch(r"(?:[^@/:]+@)?([^/:]+):(.+)", value)
        if match:
            host, path = match.group(1), match.group(2)
        else:
            # Remote snapshots persist the already-normalized identity rather
            # than a clone URL. Revalidate that form at every intake boundary.
            pieces = value.split("/")
            if len(pieces) < 3 or any(not piece for piece in pieces):
                return ""
            host, path = pieces[0], "/".join(pieces[1:])
    else:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return ""
        if parsed.scheme not in {"https", "ssh", "git+ssh"} or not parsed.hostname:
            return ""
        host, path = parsed.hostname, parsed.path
    normalized_path = path.strip("/").removesuffix(".git")
    path_parts = [piece for piece in normalized_path.split("/") if piece]
    return (
        f"{host.lower()}/{normalized_path}"
        if len(path_parts) >= 2 and all(piece not in {".", ".."} for piece in path_parts)
        else ""
    )


def _repository_rows(
    connection: sqlite3.Connection, project_id: str = ""
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT rb.id, rb.project_id, rb.name, rb.canonical_remote,
                   rb.default_branch, lr.local_path, lr.attached_at,
                   lr.last_seen_at AS last_fetched_at
            FROM repository_bindings rb
            LEFT JOIN local_repositories lr ON lr.binding_id = rb.id
            WHERE (? = '' OR rb.project_id = ?)
            ORDER BY rb.name COLLATE NOCASE, rb.canonical_remote, rb.id
            """,
            (project_id, project_id),
        ).fetchall()
    ]


def _repository_revision(rows: Iterable[Mapping[str, Any]]) -> str:
    return remote.repository_list_revision(list(rows))


def _project_repository_configuration(
    connection: sqlite3.Connection, project_id: str
) -> dict[str, Any]:
    rows = _repository_rows(connection, project_id)
    return {"revision": _repository_revision(rows), "items": rows}


def _repository_branch(raw: Any) -> str:
    value = str(raw or "main").strip()
    components = value.split("/")
    if (
        not value
        or len(value) > 200
        or value in {"@", "HEAD"}
        or value.startswith(('/', '.', '-'))
        or value.endswith(('/', '.'))
        or ".." in value
        or "//" in value
        or "@{" in value
        or any(ord(character) < 33 or character in "~^:?*[\\" for character in value)
        or any(component.startswith(".") or component.endswith(".lock") for component in components)
    ):
        raise BunkerDatabaseError("Enter a valid default branch, such as main.")
    return value


def propose_repository_bindings(
    bunker_id: str,
    *,
    project_id: str,
    expected_revision: str,
    repositories: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate one Project's complete shared repository allowlist without writing.

    Local checkout paths are deliberately excluded from this mutation and its
    revision. They belong to the current device, while the binding list is
    shared Bunker configuration.
    """
    bunker_id = _id(bunker_id, "Bunker")
    project_id = _id(project_id, "project")
    if not isinstance(repositories, (list, tuple)):
        raise BunkerDatabaseError("Project repositories must be a list.")
    if len(repositories) > 256:
        raise BunkerDatabaseError("A Project may configure at most 256 repositories.")
    with _database(bunker_id) as connection:
        scope_type, scope_id, bunker = _config_scope(connection, bunker_id, project_id)
        if scope_type != "project" or scope_id != project_id:
            raise BunkerDatabaseError("Choose a project from this Bunker.")
        mode = str(bunker.get("mode") or "local")
        permission = _config_permission(connection, bunker)
        if not permission["can_write"]:
            raise BunkerDatabaseError(str(permission["reason"]))
        current_rows = _repository_rows(connection, project_id)
        registry = _repository_registry(connection, bunker_id)
    current_revision = _repository_revision(current_rows)
    if not expected_revision or expected_revision != current_revision:
        raise BunkerDatabaseError(
            "Project repositories changed after this editor opened. Reload them and review the latest revision."
        )
    current = {str(row["id"]): row for row in current_rows}
    normalized: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_remotes: set[str] = set()
    for raw in repositories:
        if not isinstance(raw, Mapping):
            raise BunkerDatabaseError("Each Project repository must be one row.")
        raw_id = str(raw.get("id") or "").strip()
        repository_id = _id(raw_id, "repository") if raw_id else str(uuid.uuid4())
        if repository_id in seen_ids:
            raise BunkerDatabaseError("A Project repository row appears more than once.")
        if raw_id and repository_id not in current:
            raise BunkerDatabaseError(
                "A Project repository changed after this editor opened. Reload it and retry."
            )
        identity = canonical_remote(str(raw.get("canonical_remote") or ""))
        if registry["revision"] and identity not in {row["canonical_remote"] for row in registry["items"]}:
            raise BunkerDatabaseError("This repository is not allowed by the Bunker. Ask an administrator to add it in Bunker settings.")
        if not identity:
            raise BunkerDatabaseError(
                "Enter an allowed Git remote, such as github.com/owner/repository."
            )
        if identity in seen_remotes:
            raise BunkerDatabaseError(
                f"The allowed remote {identity} appears more than once in this Project."
            )
        existing = current.get(repository_id)
        if (
            existing
            and existing.get("local_path")
            and identity != str(existing.get("canonical_remote") or "")
        ):
            raise BunkerDatabaseError(
                "Disconnect this computer's checkout before changing its allowed remote."
            )
        normalized.append(
            {
                "id": repository_id,
                "name": _clean_name(raw.get("name"), "repository"),
                "canonical_remote": identity,
                "default_branch": _repository_branch(raw.get("default_branch")),
            }
        )
        seen_ids.add(repository_id)
        seen_remotes.add(identity)
    removed = set(current) - seen_ids
    if any(current[value].get("local_path") for value in removed):
        raise BunkerDatabaseError(
            "Disconnect this computer's checkout before removing its allowed repository."
        )
    revision = _repository_revision(normalized)
    change = remote.change_record(
        key=uuid.uuid4().hex,
        kind="repository-bindings",
        record_id=project_id,
        expected_revision=current_revision,
        revision=revision,
        payload=[{**row, "project_id": project_id} for row in normalized],
    )
    return {
        "mode": mode,
        "project_id": project_id,
        "expected_revision": current_revision,
        "revision": revision,
        "rows": normalized,
        "removed": sorted(removed),
        "change": change,
    }


def apply_repository_bindings_change(
    bunker_id: str,
    proposal: Mapping[str, Any],
    *,
    accepted: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a proposed allowlist. Shared Bunkers require service acceptance."""
    bunker_id = _id(bunker_id, "Bunker")
    project_id = _id(proposal.get("project_id"), "project")
    with _database(bunker_id) as connection:
        _require_acceptance(_mode(connection, bunker_id), accepted)
        connection.execute("BEGIN IMMEDIATE")
        registry = _repository_registry(connection, bunker_id)
        if registry["revision"]:
            select_registry_rows(registry, [row["canonical_remote"] for row in proposal["rows"]])
        current_rows = _repository_rows(connection, project_id)
        if _repository_revision(current_rows) != str(proposal.get("expected_revision") or ""):
            raise BunkerDatabaseError(
                "Project repositories changed after this editor opened. Reload them and review the latest revision."
            )
        now = _now()
        for value in proposal["rows"]:
            connection.execute(
                """
                INSERT INTO repository_bindings(
                    id, bunker_id, project_id, name, canonical_remote,
                    default_branch, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    canonical_remote = excluded.canonical_remote,
                    default_branch = excluded.default_branch,
                    updated_at = excluded.updated_at
                """,
                (
                    value["id"], bunker_id, project_id, value["name"],
                    value["canonical_remote"], value["default_branch"], now, now,
                ),
            )
        removed = [str(value) for value in proposal.get("removed") or []]
        if removed:
            placeholders = ",".join("?" for _ in removed)
            connection.execute(
                f"DELETE FROM repository_bindings WHERE project_id = ? AND id IN ({placeholders})",
                (project_id, *removed),
            )
        if accepted:
            _record_acceptance(connection, accepted)
    return get_configuration(bunker_id, project_id=project_id)


def replace_repository_bindings(
    bunker_id: str,
    *,
    project_id: str,
    expected_revision: str,
    repositories: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Replace one Project's shared repository allowlist on a local Bunker."""
    proposal = propose_repository_bindings(
        bunker_id,
        project_id=project_id,
        expected_revision=expected_revision,
        repositories=repositories,
    )
    if proposal["mode"] == "remote":
        raise BunkerDatabaseError(
            "This Bunker is shared. Publish repositories through the application so the service can accept them."
        )
    return apply_repository_bindings_change(bunker_id, proposal)


def _fetch_repository(root: Path) -> None:
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
    }
    try:
        process = subprocess.run(
            ["git", "-C", str(root), "fetch", "--prune", "origin"],
            capture_output=True,
            text=True,
            timeout=90,
            env=environment,
            **platform_services.current().headless_process_options(),
        )
    except subprocess.TimeoutExpired as exc:
        raise BunkerDatabaseError(
            "Fetching origin timed out. Check the network and retry. No repository was connected."
        ) from exc
    if process.returncode != 0:
        raise BunkerDatabaseError(
            "TicketBunker could not fetch origin. Check network access and Git credentials, then retry. No repository was connected."
        )


def attach_repository(
    bunker_id: str,
    *,
    project_id: str,
    local_path: str,
    binding_id: str = "",
    fetch: bool = True,
) -> dict[str, Any]:
    bunker_id = _id(bunker_id, "Bunker")
    project_id = _id(project_id, "project")
    root = _git_root(local_path)
    # Match the configured identity, not a transport URL rewritten by insteadOf.
    # Fetch still uses Git's normal URL rewriting and credentials.
    origin = _git_value(root, "config", "--get", "remote.origin.url")
    identity = canonical_remote(origin)
    selected_binding = _id(binding_id, "repository") if binding_id else ""
    if not identity:
        raise BunkerDatabaseError(
            "This Git checkout needs an origin remote before it can be connected."
        )
    now = _now()
    with _database(bunker_id) as connection:
        if connection.execute(
            "SELECT 1 FROM projects WHERE id = ? AND bunker_id = ?",
            (project_id, bunker_id),
        ).fetchone() is None:
            raise BunkerDatabaseError("Choose a project from this Bunker.")
        candidates = connection.execute(
            """
            SELECT id, canonical_remote FROM repository_bindings
            WHERE bunker_id = ? AND project_id = ?
            """,
            (bunker_id, project_id),
        ).fetchall()
        existing = next(
            (
                row for row in candidates
                if (selected_binding and str(row["id"]) == selected_binding)
                or (not selected_binding and str(row["canonical_remote"]) == identity)
            ),
            None,
        )
        if existing is None:
            raise BunkerDatabaseError(
                f"This Project does not allow {identity}. Ask a Bunker owner to enable this Project under Bunker > Repositories > Projects for that remote."
            )
        if str(existing["canonical_remote"]) != identity:
            raise BunkerDatabaseError(
                f"That folder uses {identity}, but this row allows {existing['canonical_remote']}. Choose the matching checkout."
            )
        if fetch:
            _fetch_repository(root)
        resolved_binding_id = str(existing["id"])
        connection.execute("BEGIN IMMEDIATE")
        current_binding = connection.execute(
            """
            SELECT canonical_remote FROM repository_bindings
            WHERE id = ? AND bunker_id = ? AND project_id = ?
            """,
            (resolved_binding_id, bunker_id, project_id),
        ).fetchone()
        if (
            current_binding is None
            or str(current_binding["canonical_remote"]) != identity
        ):
            raise BunkerDatabaseError(
                "This Project's repository configuration changed while origin was fetching. Reopen Configure and retry."
            )
        connection.execute(
            """
            INSERT INTO local_repositories(binding_id, local_path, attached_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(binding_id) DO UPDATE SET
                local_path = excluded.local_path,
                last_seen_at = excluded.last_seen_at
            """,
            (resolved_binding_id, str(root), now, now),
        )
    return get_bunker(bunker_id)


def detach_repository(
    bunker_id: str, *, project_id: str, binding_id: str
) -> dict[str, Any]:
    """Remove only this device's checkout mapping for a shared repository."""
    bunker_id = _id(bunker_id, "Bunker")
    project_id = _id(project_id, "project")
    binding_id = _id(binding_id, "repository")
    with _database(bunker_id) as connection:
        binding = connection.execute(
            """
            SELECT 1 FROM repository_bindings
            WHERE id = ? AND bunker_id = ? AND project_id = ?
            """,
            (binding_id, bunker_id, project_id),
        ).fetchone()
        if binding is None:
            raise BunkerDatabaseError("That repository is not part of this Project.")
        connection.execute(
            "DELETE FROM local_repositories WHERE binding_id = ?", (binding_id,)
        )
    return get_bunker(bunker_id)


def _attach_import_repository(
    bunker_id: str,
    project_id: str,
    root: Path,
    *,
    default_branch: str,
) -> str:
    """Preserve one legacy checkout without network access or shared claims."""
    origin = _git_value(root, "config", "--get", "remote.origin.url")
    identity = canonical_remote(origin)
    now = _now()
    with _database(bunker_id) as connection:
        binding_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO repository_bindings(
                id, bunker_id, project_id, name, canonical_remote,
                default_branch, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                binding_id,
                bunker_id,
                project_id,
                root.name,
                identity,
                str(default_branch or "main"),
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO local_repositories(binding_id, local_path, attached_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            (binding_id, str(root), now, now),
        )
    return binding_id


def project_record(bunker_id: str, project_id: str) -> dict[str, Any]:
    bunker_id = _id(bunker_id, "Bunker")
    project_id = _id(project_id, "project")
    with _database(bunker_id) as connection:
        row = connection.execute(
            """
            SELECT id, parent_id, key, name, description, position FROM projects
            WHERE id = ? AND bunker_id = ?
            """,
            (project_id, bunker_id),
        ).fetchone()
    if row is None:
        raise BunkerDatabaseError("Choose a project from this Bunker.")
    return dict(row)


def inspect_project_change(bunker_id: str, project_id: str) -> dict[str, Any]:
    project = project_record(bunker_id, project_id)
    configuration = get_configuration(bunker_id, project_id=project_id)
    with _database(bunker_id) as connection:
        tickets = connection.execute("SELECT count(*) FROM tickets WHERE project_id=?", (project_id,)).fetchone()[0]
        children = connection.execute("SELECT count(*) FROM projects WHERE parent_id=?", (project_id,)).fetchone()[0]
    return {"project": project, "revision": remote.project_revision(project), "permission": configuration["permission"],
            "tickets": tickets, "subprojects": children}


def propose_project_edit(bunker_id: str, project_id: str, expected_revision: str, *, name: str = "", delete: bool = False) -> dict[str, Any]:
    current = inspect_project_change(bunker_id, project_id)
    if not current["permission"]["can_write"]:
        raise BunkerDatabaseError(current["permission"]["reason"])
    if current["revision"] != expected_revision:
        raise BunkerDatabaseError("This Project changed. Close this dialog and reopen it before retrying.")
    if delete and (current["tickets"] or current["subprojects"]):
        raise BunkerDatabaseError("This Project contains tickets or subprojects. Only empty Projects can be deleted; its data has not changed.")
    value = {**current["project"], "name": current["project"]["name"] if delete else _clean_name(name, "project")}
    revision = "deleted:" + uuid.uuid4().hex if delete else remote.project_revision(value)
    with _database(bunker_id) as connection:
        mode = _mode(connection, bunker_id)
    return {"mode": mode, "project": value, "delete": delete, "expected_revision": expected_revision,
            "change": remote.change_record(key=uuid.uuid4().hex, kind="project-delete" if delete else "project",
                record_id=project_id, expected_revision=expected_revision, revision=revision, payload=value)}


def _delete_empty_project(connection: sqlite3.Connection, project_id: str) -> None:
    if connection.execute("SELECT 1 FROM tickets WHERE project_id=?", (project_id,)).fetchone() or connection.execute("SELECT 1 FROM projects WHERE parent_id=?", (project_id,)).fetchone():
        raise BunkerDatabaseError("This Project contains tickets or subprojects. No data was deleted.")
    if connection.execute("SELECT 1 FROM reports JOIN repository_bindings ON reports.repository_id=repository_bindings.id WHERE repository_bindings.project_id=?", (project_id,)).fetchone():
        raise BunkerDatabaseError("A report still references this Project's repositories. No data was deleted.")
    connection.execute("DELETE FROM repository_bindings WHERE project_id=?", (project_id,))
    connection.execute("DELETE FROM config_documents WHERE scope_type='project' AND scope_id=?", (project_id,))
    connection.execute("DELETE FROM projects WHERE id=?", (project_id,))


def apply_project_edit(bunker_id: str, proposal: Mapping[str, Any], *, accepted: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = proposal["project"]
    with _database(bunker_id) as connection:
        _require_acceptance(_mode(connection, bunker_id), accepted)
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT * FROM projects WHERE id=?", (value["id"],)).fetchone()
        if current is None or remote.project_revision(dict(current)) != proposal["expected_revision"]:
            raise BunkerDatabaseError("This Project changed. Reopen it before retrying.")
        if proposal["delete"]:
            _delete_empty_project(connection, value["id"])
        else:
            connection.execute("UPDATE projects SET name=?, updated_at=? WHERE id=?", (value["name"], _now(), value["id"]))
        if accepted:
            _record_acceptance(connection, accepted)
    return get_bunker(bunker_id)


def _ticket_revision(raw: str) -> str:
    import hashlib

    return "t:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def propose_ticket_sync(
    bunker_id: str,
    *,
    project_id: str,
    tickets: Iterable[Mapping[str, Any]],
    source_kind: str,
) -> dict[str, Any]:
    """Compare tracker tickets with the Project's copies without writing.

    Each ticket becomes created, updated, or unchanged by comparing canonical
    payload JSON. Shared Bunkers send the created/updated set as ``ticket``
    changes before the same rows are written locally.
    """
    bunker_id = _id(bunker_id, "Bunker")
    project_id = _id(project_id, "project")
    kind = str(source_kind or "manual")[:64]
    items = list(tickets)
    if len(items) > 5000:
        raise BunkerDatabaseError("A sync may carry at most 5000 tickets.")
    with _database(bunker_id) as connection:
        mode = _mode(connection, bunker_id)
        project_row = connection.execute(
            "SELECT key FROM projects WHERE id = ? AND bunker_id = ?", (project_id, bunker_id)
        ).fetchone()
        if project_row is None:
            raise BunkerDatabaseError("Choose a project from this Bunker.")
        project_key = str(project_row["key"])
        # Ids stay stable through the tracker's own identity. A first sync may
        # find its natural id taken by another source (two repositories on one
        # board share issue numbers); that ticket gets the next free number and
        # keeps it on every later sync.
        existing_ids: set[str] = set()
        identity_to_id: dict[str, str] = {}
        id_to_identity: dict[str, str] = {}
        highest = 0
        number_pattern = re.compile(rf"^{re.escape(project_key)}-(\d+)$")
        for row in connection.execute("SELECT id, project_id, payload_json FROM tickets").fetchall():
            existing_id = str(row["id"])
            existing_ids.add(existing_id)
            match = number_pattern.fullmatch(existing_id)
            if match:
                highest = max(highest, int(match.group(1)))
            metadata = _payload_object(row["payload_json"]).get("metadata")
            identity = (
                str(metadata.get("source_identity") or "") if isinstance(metadata, Mapping) else ""
            )
            if identity and str(row["project_id"]) == project_id:
                identity_to_id[identity] = existing_id
                id_to_identity[existing_id] = identity
        rows: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []
        seen: set[str] = set()

        def priority(value: Any) -> int:
            """Known identities first, then deliberate title keys, then numbers."""
            if not isinstance(value, Mapping):
                return 3
            metadata_value = value.get("metadata")
            metadata_value = metadata_value if isinstance(metadata_value, Mapping) else {}
            if str(metadata_value.get("source_identity") or "") in identity_to_id:
                return 0
            return 1 if str(metadata_value.get("id_origin") or "") == "title" else 2

        for value in sorted(items, key=priority):
            if not isinstance(value, Mapping):
                raise BunkerDatabaseError("Each synced ticket must be one object.")
            requested = _id(value.get("id"), "ticket")
            metadata_value = value.get("metadata")
            identity = (
                str(metadata_value.get("source_identity") or "")
                if isinstance(metadata_value, Mapping)
                else ""
            )
            if identity and identity in identity_to_id:
                ticket_id = identity_to_id[identity]
            else:
                claimed_by_other = requested in seen or (
                    requested in existing_ids
                    and identity
                    and id_to_identity.get(requested, "")
                    and id_to_identity.get(requested) != identity
                )
                ticket_id = requested
                if claimed_by_other:
                    candidate = highest + 1
                    while f"{project_key}-{candidate}" in existing_ids or f"{project_key}-{candidate}" in seen:
                        candidate += 1
                    ticket_id = f"{project_key}-{candidate}"
                    highest = candidate
                elif number_pattern.fullmatch(ticket_id):
                    highest = max(highest, int(number_pattern.fullmatch(ticket_id).group(1)))
                if identity:
                    identity_to_id[identity] = ticket_id
                    id_to_identity[ticket_id] = identity
            if ticket_id in seen:
                raise BunkerDatabaseError(
                    f"Two tracker items map to {ticket_id}. Give those issues distinct keys."
                )
            seen.add(ticket_id)
            payload = {key: item for key, item in dict(value).items() if key != "id"}
            payload = {"id": ticket_id, **payload}
            if "key" in payload:
                payload["key"] = ticket_id
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if len(raw.encode("utf-8")) > 2 * 1024 * 1024:
                raise BunkerDatabaseError(f"Ticket {ticket_id} is too large to store.")
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
            reference = str(metadata.get("source_url") or "")[:512]
            existing = connection.execute(
                "SELECT project_id, payload_json FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            if existing is not None and str(existing["project_id"]) != project_id:
                raise BunkerDatabaseError(
                    f"Ticket {ticket_id} already belongs to another Project in this Bunker."
                )
            current_raw = ""
            if existing is not None:
                # Provider snapshots cannot erase TicketBunker's repository-scoped work.
                saved = _payload_object(existing["payload_json"])
                if "branch_assignments" in saved:
                    payload["branch_assignments"] = saved["branch_assignments"]
                    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                current_raw = json.dumps(
                    _payload_object(existing["payload_json"]), sort_keys=True, separators=(",", ":")
                )
            if existing is None:
                action = "created"
            elif current_raw == raw:
                action = "unchanged"
            else:
                action = "updated"
            results.append({"ticket": ticket_id, "action": action})
            if action == "unchanged":
                continue
            rows.append(
                {
                    "id": ticket_id,
                    "project_id": project_id,
                    "payload": payload,
                    "source_kind": kind,
                    "source_reference": reference,
                }
            )
            changes.append(
                remote.change_record(
                    key=uuid.uuid4().hex,
                    kind="ticket",
                    record_id=ticket_id,
                    expected_revision=_ticket_revision(current_raw) if existing is not None else "",
                    revision=_ticket_revision(raw),
                    payload=rows[-1],
                )
            )
    return {
        "mode": mode,
        "project_id": project_id,
        "results": results,
        "rows": rows,
        "changes": changes,
    }


def begin_ticket_sync(bunker_id: str, project_id: str) -> str:
    """Device-local execution receipt, never a shared Project configuration."""
    attempt = uuid.uuid4().hex
    with _database(bunker_id) as connection:
        if not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise BunkerDatabaseError("Choose a Project in this Bunker before syncing tickets.")
        connection.execute(
            """INSERT INTO local_project_sync(project_id,last_attempt_at,status,attempt_id)
               VALUES (?,?,'pending',?) ON CONFLICT(project_id) DO UPDATE SET
               last_attempt_at=excluded.last_attempt_at,status='pending',error='',attempt_id=excluded.attempt_id""",
            (project_id, _now(), attempt),
        )
    return attempt


def fail_ticket_sync(bunker_id: str, project_id: str, attempt: str, message: str) -> None:
    with _database(bunker_id) as connection:
        connection.execute("UPDATE local_project_sync SET status='failed',error=? WHERE project_id=? AND attempt_id=?",
                           (message[:1500], project_id, attempt))


def apply_ticket_sync_change(
    bunker_id: str,
    proposal: Mapping[str, Any],
    *,
    accepted: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the created/updated tickets. Shared Bunkers require acceptance."""
    bunker_id = _id(bunker_id, "Bunker")
    rows = list(proposal.get("rows") or [])
    with _database(bunker_id) as connection:
        if rows:
            _require_acceptance(_mode(connection, bunker_id), accepted)
        connection.execute("BEGIN IMMEDIATE")
        expected = {change["record_id"]: change["expected_revision"]
                    for change in proposal.get("changes", [])}
        for row in rows:
            if row["id"] not in expected:
                raise BunkerDatabaseError("The ticket change has no base revision. Reload and retry.")
            current = connection.execute("SELECT payload_json FROM tickets WHERE id = ?", (row["id"],)).fetchone()
            current_raw = json.dumps(_payload_object(current["payload_json"]), sort_keys=True,
                                     separators=(",", ":")) if current else ""
            revision = _ticket_revision(current_raw) if current else ""
            if revision != expected[row["id"]]:
                raise BunkerDatabaseError("The ticket changed while this action ran. Sync the Bunker and retry.")
        now = _now()
        for row in rows:
            payload = row["payload"]
            updated = str(
                ((payload.get("metadata") or {}).get("source_updated") or "")
                if isinstance(payload.get("metadata"), Mapping)
                else ""
            ) or now
            connection.execute(
                """
                INSERT INTO tickets(id, project_id, payload_json, source_kind, source_reference, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id = excluded.project_id,
                    payload_json = excluded.payload_json,
                    source_kind = excluded.source_kind,
                    source_reference = excluded.source_reference,
                    updated_at = excluded.updated_at
                """,
                (
                    str(row["id"]),
                    str(row["project_id"]),
                    json.dumps(payload, sort_keys=True),
                    str(row["source_kind"]),
                    str(row["source_reference"]),
                    updated,
                ),
            )
        if accepted:
            _record_acceptance(connection, accepted)
        if proposal.get("sync_attempt"):
            connection.execute("UPDATE local_project_sync SET status='current',error='',last_success_at=? WHERE project_id=? AND attempt_id=?",
                               (now, proposal["project_id"], proposal["sync_attempt"]))
    results = list(proposal.get("results") or [])
    return {
        "tickets": results,
        "created": sum(item["action"] == "created" for item in results),
        "updated": sum(item["action"] == "updated" for item in results),
        "unchanged": sum(item["action"] == "unchanged" for item in results),
    }


def propose_branch_assignment(bunker_id: str, ticket_id: str, repository_id: str,
                              branch: str) -> dict[str, Any]:
    """Record work independently of tracker fields, through the ticket change contract."""
    with _database(bunker_id) as connection:
        row = connection.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise BunkerDatabaseError("That ticket is not in this Bunker.")
        binding = connection.execute("SELECT project_id FROM repository_bindings WHERE id = ?",
                                     (repository_id,)).fetchone()
        if binding is None or binding["project_id"] != row["project_id"]:
            raise BunkerDatabaseError("Choose a repository attached to this ticket's Project.")
        payload = _payload_object(row["payload_json"])
        key = str(payload.get("key") or ticket_id)
        if not re.search(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", branch):
            raise BunkerDatabaseError(f"The branch must contain ticket key {key}. Rename it and retry.")
        _repository_branch(branch)
        assignments = dict(payload.get("branch_assignments") or {})
        branches = list(assignments.get(repository_id) or [])
        if branch in branches:
            return {"mode": _mode(connection, bunker_id), "rows": [], "changes": [], "results": []}
        previous = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        assignments[repository_id] = [*branches, branch]
        payload["branch_assignments"] = assignments
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        value = {"id": ticket_id, "project_id": row["project_id"], "payload": payload,
                 "source_kind": row["source_kind"], "source_reference": row["source_reference"]}
        change = remote.change_record(key=uuid.uuid4().hex, kind="ticket", record_id=ticket_id,
                                      expected_revision=_ticket_revision(previous),
                                      revision=_ticket_revision(raw), payload=value)
        return {"mode": _mode(connection, bunker_id), "rows": [value], "changes": [change],
                "results": [{"ticket": ticket_id, "action": "updated"}]}


def find_bunker_ticket(repo_root: Path | str, branch: str) -> dict[str, Any] | None:
    """The one Bunker ticket a checkout branch names, or None when ambiguous."""
    ticket_id = config.extract_ticket_id(str(branch or ""))
    if not ticket_id:
        return None
    try:
        resolved = platform_services.local_path_identity(Path(repo_root))
    except OSError:
        return None
    matches: list[dict[str, Any]] = []
    for bunker in list_bunkers():
        if not bunker.get("available"):
            continue
        for repository in bunker.get("repositories") or []:
            raw = str(repository.get("local_path") or "")
            if not raw or platform_services.local_path_identity(Path(raw)) != resolved:
                continue
            with _database(str(bunker["id"])) as connection:
                row = connection.execute(
                    "SELECT project_id FROM tickets WHERE id = ?", (ticket_id,)
                ).fetchone()
                if row is None:
                    continue
                allowed, _projects = _project_scope(
                    _project_rows(connection), str(repository.get("project_id") or "")
                )
            if str(row["project_id"]) not in allowed:
                continue
            matches.append(
                {
                    "bunker_id": str(bunker["id"]),
                    "ticket_id": ticket_id,
                    "project_id": str(row["project_id"]),
                    "repository_id": str(repository.get("id") or ""),
                }
            )
    return matches[0] if len(matches) == 1 else None


def _report_revision(content: str) -> str:
    import hashlib

    return "r:" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def propose_report_publication(
    bunker_id: str,
    *,
    ticket_id: str,
    repository_id: str,
    branch: str,
    child: str,
    master: str,
) -> dict[str, Any]:
    """The accepted child report plus regenerated master as typed changes."""
    bunker_id = _id(bunker_id, "Bunker")
    ticket_id = _id(ticket_id, "ticket")
    repository_key = _id(repository_id, "repository") if repository_id else ""
    branch_name = str(branch or "").strip()
    if not branch_name:
        raise BunkerDatabaseError("A report belongs to one branch.")
    candidates = [
        {"ticket_id": ticket_id, "scope": f"branch:{branch_name}", "repository_id": repository_key, "content": str(child)},
        {"ticket_id": ticket_id, "scope": "combined", "repository_id": "", "content": str(master)},
    ]
    with _database(bunker_id) as connection:
        mode = _mode(connection, bunker_id)
        if connection.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone() is None:
            raise BunkerDatabaseError("That ticket is not in this Bunker.")
        from .builders.master import from_records

        children = [dict(row) for row in connection.execute(
            "SELECT scope, repository_id, content FROM reports WHERE ticket_id = ? AND scope LIKE 'branch:%'",
            (ticket_id,)).fetchall()
            if not (row["scope"] == f"branch:{branch_name}" and row["repository_id"] == repository_key)]
        children.append(candidates[0])
        candidates[1]["content"] = from_records(ticket_id, children)
        rows: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        for candidate in candidates:
            if not candidate["content"].strip():
                continue
            current = connection.execute(
                "SELECT content FROM reports WHERE ticket_id = ? AND scope = ? AND repository_id = ?",
                (ticket_id, candidate["scope"], candidate["repository_id"]),
            ).fetchone()
            current_text = str(current["content"]) if current is not None else None
            if current_text == candidate["content"]:
                results.append({"scope": candidate["scope"], "action": "unchanged"})
                continue
            results.append({"scope": candidate["scope"], "action": "updated" if current is not None else "created"})
            rows.append(candidate)
            changes.append(
                remote.change_record(
                    key=uuid.uuid4().hex,
                    kind="report",
                    record_id=f"{ticket_id}|{candidate['scope']}|{candidate['repository_id']}",
                    expected_revision=_report_revision(current_text) if current_text is not None else "",
                    revision=_report_revision(candidate["content"]),
                    payload=candidate,
                )
            )
    return {"mode": mode, "ticket_id": ticket_id, "rows": rows, "changes": changes, "results": results}


def apply_report_publication(
    bunker_id: str,
    proposal: Mapping[str, Any],
    *,
    accepted: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write accepted reports. Shared Bunkers require service acceptance."""
    bunker_id = _id(bunker_id, "Bunker")
    rows = list(proposal.get("rows") or [])
    with _database(bunker_id) as connection:
        if rows:
            _require_acceptance(_mode(connection, bunker_id), accepted)
        connection.execute("BEGIN IMMEDIATE")
        expected = {change["record_id"]: change["expected_revision"] for change in proposal.get("changes", [])}
        for row in rows:
            key = f"{row['ticket_id']}|{row['scope']}|{row['repository_id']}"
            current = connection.execute("SELECT content FROM reports WHERE ticket_id = ? AND scope = ? AND repository_id = ?",
                                         (row["ticket_id"], row["scope"], row["repository_id"])).fetchone()
            revision = _report_revision(str(current["content"])) if current else ""
            if key not in expected or expected[key] != revision:
                raise BunkerDatabaseError("Reports changed while this action ran. Sync the Bunker and retry.")
        now = _now()
        for row in rows:
            connection.execute(
                """
                INSERT INTO reports(ticket_id, scope, repository_id, content, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(ticket_id, scope, repository_id) DO UPDATE SET
                    content = excluded.content,
                    updated_at = excluded.updated_at
                """,
                (str(row["ticket_id"]), str(row["scope"]), str(row["repository_id"]), str(row["content"]), now),
            )
        if accepted:
            _record_acceptance(connection, accepted)
    results = list(proposal.get("results") or [])
    return {
        "reports": results,
        "created": sum(item["action"] == "created" for item in results),
        "updated": sum(item["action"] == "updated" for item in results),
        "unchanged": sum(item["action"] == "unchanged" for item in results),
    }


def local_publication(bunker_id: str) -> dict[str, Any]:
    """Everything a service needs to host this Bunker. Device paths never leave."""
    bunker_id = _id(bunker_id, "Bunker")
    with _database(bunker_id) as connection:
        bunker = connection.execute(
            "SELECT id, name, mode FROM bunker WHERE id = ?", (bunker_id,)
        ).fetchone()
        if bunker is None:
            raise BunkerDatabaseError("That Bunker is not available on this device.")
        projects = _project_rows(connection)
        bindings = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, project_id, name, canonical_remote, default_branch, created_at, updated_at
                FROM repository_bindings ORDER BY name COLLATE NOCASE, id
                """
            ).fetchall()
        ]
        documents = [
            {
                "scope_type": str(row["scope_type"]),
                "scope_id": str(row["scope_id"]),
                "kind": str(row["kind"]),
                "revision": str(row["revision"]),
                "content": _payload_object(row["content_json"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in connection.execute(
                "SELECT scope_type, scope_id, kind, revision, content_json, updated_at FROM config_documents"
            ).fetchall()
        ]
        tickets = [
            {
                "id": str(row["id"]),
                "project_id": str(row["project_id"]),
                "payload": _payload_object(row["payload_json"]),
                "source_kind": str(row["source_kind"] or ""),
                "source_reference": str(row["source_reference"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in connection.execute(
                "SELECT id, project_id, payload_json, source_kind, source_reference, updated_at FROM tickets"
            ).fetchall()
        ]
        reports = [
            {
                "ticket_id": str(row["ticket_id"]),
                "scope": str(row["scope"]),
                "repository_id": str(row["repository_id"] or ""),
                "content": str(row["content"] or ""),
                "updated_at": str(row["updated_at"] or ""),
            }
            for row in connection.execute(
                "SELECT ticket_id, scope, repository_id, content, updated_at FROM reports"
            ).fetchall()
        ]
    return {
        "bunker": {"id": str(bunker["id"]), "name": str(bunker["name"])},
        "projects": [
            {
                "id": str(row["id"]),
                "parent_id": str(row.get("parent_id") or ""),
                "key": str(row["key"]),
                "name": str(row["name"]),
                "description": str(row.get("description") or ""),
                "position": int(row.get("position") or 0),
                "created_at": str(row.get("created_at") or ""),
                "updated_at": str(row.get("updated_at") or ""),
            }
            for row in projects
        ],
        "repository_bindings": bindings,
        "config_documents": documents,
        "tickets": tickets,
        "reports": reports,
    }


def _replace_members(connection: sqlite3.Connection, members: Iterable[Mapping[str, Any]]) -> None:
    connection.execute("DELETE FROM members")
    now = _now()
    for value in members:
        member_id = _id(value.get("id"), "member")
        member_capabilities = value.get("capabilities") or []
        if not isinstance(member_capabilities, list) or any(
            not isinstance(item, str) for item in member_capabilities
        ):
            raise BunkerDatabaseError(
                "The remote snapshot contains invalid member capabilities."
            )
        connection.execute(
            """
            INSERT INTO members(
                id, display_name, email, role, state,
                capabilities_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                member_id,
                str(value.get("display_name") or value.get("name") or ""),
                str(value.get("email") or ""),
                str(value.get("role") or ""),
                str(value.get("state") or "active"),
                json.dumps(member_capabilities, sort_keys=True),
                str(value.get("updated_at") or now),
            ),
        )


def apply_snapshot(bunker_id: str, snapshot: remote.RemoteSnapshot, *, expected_cursor: str | None = None) -> dict[str, Any]:
    """Reconcile a replica with the service's authorized snapshot.

    Shared records are upserted by identity; repository bindings the service
    no longer lists are removed along with this device's mapping to them.
    Device paths, runtime artefacts, and the credential reference stay.
    """
    bunker_id = _id(bunker_id, "Bunker")
    counts = {"projects": 0, "repositories": 0, "documents": 0, "tickets": 0, "reports": 0}
    with _database(bunker_id) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if _mode(connection, bunker_id) != "remote":
            raise BunkerDatabaseError("This Bunker is local; there is nothing to sync from.")
        row = connection.execute(
            "SELECT remote_bunker_id, sync_cursor FROM remote_connection WHERE singleton = 1"
        ).fetchone()
        if expected_cursor is not None and (row is None or str(row["sync_cursor"] or "") != expected_cursor):
            raise BunkerDatabaseError("A newer synchronization finished while this response was loading. Its data was kept. Sync again to load current shared state.")
        remote_id = str(row["remote_bunker_id"]) if row else ""
        if str(snapshot.bunker.get("id") or "") != remote_id:
            raise BunkerDatabaseError("The service answered for a different Bunker.")
        now = _now()
        name = str(snapshot.bunker.get("name") or "").strip()
        if name:
            connection.execute("UPDATE bunker SET name = ?, updated_at = ? WHERE id = ?", (name, now, bunker_id))
        # Projects: parents before children.
        pending = [dict(value) for value in snapshot.projects]
        # A full snapshot must prove its own hierarchy. Seeding this set from
        # existing rows would accept cycles between already-known Projects.
        placed: set[str] = set()
        while pending:
            progressed = False
            for value in list(pending):
                project_id = _id(value.get("id"), "project")
                parent = str(value.get("parent_id") or "")
                if parent and parent not in placed:
                    continue
                connection.execute(
                    """
                    INSERT INTO projects(id, bunker_id, parent_id, key, name, description, position, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        parent_id = excluded.parent_id, key = excluded.key, name = excluded.name,
                        description = excluded.description, position = excluded.position,
                        updated_at = excluded.updated_at
                    """,
                    (
                        project_id, bunker_id, parent or None, _project_key(value.get("key")),
                        _clean_name(value.get("name"), "project"), str(value.get("description") or ""),
                        int(value.get("position") or 0), str(value.get("created_at") or now),
                        str(value.get("updated_at") or now),
                    ),
                )
                placed.add(project_id)
                pending.remove(value)
                progressed = True
                counts["projects"] += 1
            if not progressed:
                raise BunkerDatabaseError("The service snapshot contains a missing parent or cycle.")
        listed_bindings: set[str] = set()
        for value in snapshot.repository_bindings:
            binding_id = _id(value.get("id"), "repository binding")
            identity = canonical_remote(str(value.get("canonical_remote") or ""))
            if not identity:
                raise BunkerDatabaseError("The service snapshot contains an invalid repository identity.")
            connection.execute(
                """
                INSERT INTO repository_bindings(id, bunker_id, project_id, name, canonical_remote, default_branch, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id = excluded.project_id, name = excluded.name,
                    canonical_remote = excluded.canonical_remote, default_branch = excluded.default_branch,
                    updated_at = excluded.updated_at
                """,
                (
                    binding_id, bunker_id, _id(value.get("project_id"), "project"),
                    _clean_name(value.get("name"), "repository"), identity,
                    _repository_branch(value.get("default_branch")),
                    str(value.get("created_at") or now), str(value.get("updated_at") or now),
                ),
            )
            listed_bindings.add(binding_id)
            counts["repositories"] += 1
        for item in connection.execute("SELECT id FROM repository_bindings").fetchall():
            if str(item["id"]) not in listed_bindings:
                connection.execute("DELETE FROM repository_bindings WHERE id = ?", (str(item["id"]),))
        for value in snapshot.config_documents:
            scope_type = str(value.get("scope_type") or "bunker")
            scope_id = (
                bunker_id
                if scope_type == "bunker" and str(value.get("scope_id") or remote_id) == remote_id
                else str(value.get("scope_id") or remote_id)
            )
            connection.execute(
                """
                INSERT INTO config_documents(scope_type, scope_id, kind, revision, content_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_type, scope_id, kind) DO UPDATE SET
                    revision = excluded.revision, content_json = excluded.content_json,
                    updated_at = excluded.updated_at
                """,
                (
                    scope_type, scope_id, str(value.get("kind") or "configuration"),
                    str(value.get("revision") or snapshot.revision),
                    json.dumps(value.get("content") or {}, sort_keys=True),
                    str(value.get("updated_at") or now),
                ),
            )
            counts["documents"] += 1
        for value in snapshot.tickets:
            connection.execute(
                """
                INSERT INTO tickets(id, project_id, payload_json, source_kind, source_reference, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_id = excluded.project_id, payload_json = excluded.payload_json,
                    source_kind = excluded.source_kind, source_reference = excluded.source_reference,
                    updated_at = excluded.updated_at
                """,
                (
                    _id(value.get("id"), "ticket"), _id(value.get("project_id"), "project"),
                    json.dumps(value.get("payload") or {}, sort_keys=True),
                    str(value.get("source_kind") or "remote"), str(value.get("source_reference") or ""),
                    str(value.get("updated_at") or now),
                ),
            )
            counts["tickets"] += 1
        for value in snapshot.reports:
            scope = str(value.get("scope") or "").strip()
            if not scope:
                continue
            connection.execute(
                """
                INSERT INTO reports(ticket_id, scope, repository_id, content, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(ticket_id, scope, repository_id) DO UPDATE SET
                    content = excluded.content, updated_at = excluded.updated_at
                """,
                (
                    _id(value.get("ticket_id"), "ticket"), scope,
                    str(value.get("repository_id") or ""), str(value.get("content") or ""),
                    str(value.get("updated_at") or now),
                ),
            )
            counts["reports"] += 1
        listed_projects = {str(value["id"]) for value in snapshot.projects}
        # Only empty-Project deletion is supported. An incomplete snapshot must
        # not silently discard local tickets or their reports.
        missing = {str(row["id"]) for row in connection.execute("SELECT id FROM projects")} - listed_projects
        while missing:
            leaves = [pid for pid in missing if not connection.execute("SELECT 1 FROM projects WHERE parent_id=?", (pid,)).fetchone()]
            if not leaves:
                raise BunkerDatabaseError("The remote Project hierarchy is incomplete. No local Project data was removed.")
            for pid in leaves:
                _delete_empty_project(connection, pid)
                missing.remove(pid)
        _replace_members(connection, snapshot.members)
        connection.execute(
            """
            UPDATE remote_connection
            SET state = 'ready', config_revision = ?, sync_cursor = ?, last_synced_at = ?,
                current_member_id = COALESCE(NULLIF(?, ''), current_member_id),
                capabilities_json = ?
            WHERE singleton = 1
            """,
            (
                snapshot.revision, snapshot.cursor, now, str(snapshot.current_member_id or ""),
                json.dumps(list(snapshot.capabilities), sort_keys=True),
            ),
        )
    return {**counts, "cursor": snapshot.cursor, "revision": snapshot.revision, "synced_at": now}


def share_bunker(
    bunker_id: str,
    *,
    server_url: str,
    snapshot: remote.RemoteSnapshot,
    credential_reference: str,
) -> dict[str, Any]:
    """Turn a local Bunker into the owner's replica of its published copy."""
    bunker_id = _id(bunker_id, "Bunker")
    server = remote.normalize_server_url(server_url)
    remote_id = _id(snapshot.bunker.get("id"), "remote Bunker")
    reference = _id(credential_reference, "credential reference")
    member_id = _id(snapshot.current_member_id, "enrolled member")
    if _remote_registered(server, remote_id, exclude_bunker_id=bunker_id):
        raise BunkerDatabaseError("That remote Bunker is already connected on this device.")
    with _database(bunker_id) as connection:
        if _mode(connection, bunker_id) != "local":
            raise BunkerDatabaseError("This Bunker is already shared.")
        now = _now()
        connection.execute(
            "UPDATE bunker SET mode = 'remote', updated_at = ? WHERE id = ?", (now, bunker_id)
        )
        connection.execute("DELETE FROM remote_connection")
        connection.execute(
            """
            INSERT INTO remote_connection(
                singleton, server_url, remote_bunker_id, state,
                credential_reference, config_revision, sync_cursor, last_synced_at,
                current_member_id, capabilities_json
            ) VALUES (1, ?, ?, 'ready', ?, ?, ?, ?, ?, ?)
            """,
            (
                server,
                remote_id,
                reference,
                snapshot.revision,
                snapshot.cursor,
                now,
                member_id,
                json.dumps(list(snapshot.capabilities), sort_keys=True),
            ),
        )
        _replace_members(connection, snapshot.members)
    with _registry() as registry:
        registry.execute(
            """
            UPDATE bunkers SET mode = 'remote', remote_server = ?, remote_bunker_id = ?
            WHERE id = ?
            """,
            (server, remote_id, bunker_id),
        )
    return get_bunker(bunker_id)


def configure_remote(
    bunker_id: str,
    *,
    server_url: str,
    remote_bunker_id: str,
    credential_reference: str = "",
    state: str = "pending-auth",
) -> dict[str, Any]:
    """Record a service relationship, never raw database credentials."""
    bunker_id = _id(bunker_id, "Bunker")
    server = remote.normalize_server_url(server_url)
    remote_id = _id(remote_bunker_id, "remote Bunker")
    if state not in {"pending-auth", "ready", "offline", "error"}:
        raise BunkerDatabaseError("That remote connection state is not valid.")
    if _remote_registered(server, remote_id, exclude_bunker_id=bunker_id):
        raise BunkerDatabaseError("That remote Bunker is already connected on this device.")
    with _database(bunker_id) as connection:
        connection.execute(
            """
            INSERT INTO remote_connection(
                singleton, server_url, remote_bunker_id, state, credential_reference
            ) VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                server_url = excluded.server_url,
                remote_bunker_id = excluded.remote_bunker_id,
                state = excluded.state,
                credential_reference = excluded.credential_reference
            """,
            (server, remote_id, state, str(credential_reference or "")),
        )
        connection.execute(
            "UPDATE bunker SET mode = 'remote', updated_at = ?", (_now(),)
        )
    try:
        with _registry() as registry:
            registry.execute(
                """
                UPDATE bunkers SET mode = 'remote', remote_server = ?, remote_bunker_id = ?
                WHERE id = ?
                """,
                (server, remote_id, bunker_id),
            )
    except sqlite3.IntegrityError as exc:
        raise BunkerDatabaseError(
            "That remote Bunker is already connected on this device."
        ) from exc
    return get_bunker(bunker_id)


def _insert_remote_projects(
    connection: sqlite3.Connection,
    bunker_id: str,
    projects: tuple[Mapping[str, Any], ...],
) -> None:
    pending = [dict(value) for value in projects]
    inserted: set[str] = set()
    while pending:
        progressed = False
        for value in list(pending):
            project_id = _id(value.get("id"), "project")
            parent = str(value.get("parent_id") or "")
            if parent and parent not in inserted:
                continue
            now = _now()
            connection.execute(
                """
                INSERT INTO projects(
                    id, bunker_id, parent_id, key, name, description,
                    position, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    bunker_id,
                    parent or None,
                    _project_key(value.get("key")),
                    _clean_name(value.get("name"), "project"),
                    str(value.get("description") or ""),
                    int(value.get("position") or 0),
                    str(value.get("created_at") or now),
                    str(value.get("updated_at") or now),
                ),
            )
            inserted.add(project_id)
            pending.remove(value)
            progressed = True
        if not progressed:
            raise BunkerDatabaseError(
                "The remote project hierarchy contains a missing parent or cycle."
            )


def create_replica(
    invitation: remote.JoinInvitation,
    enrollment: remote.Enrollment,
) -> dict[str, Any]:
    """Create a local database only after authenticated enrollment succeeds."""
    snapshot = enrollment.snapshot
    credential_reference = _id(enrollment.credential_reference, "credential reference")
    remote_id = _id(snapshot.bunker.get("id"), "remote Bunker")
    if remote_id != invitation.bunker_id:
        raise BunkerDatabaseError("The remote snapshot belongs to a different Bunker.")
    if _remote_registered(invitation.server, remote_id):
        raise BunkerDatabaseError("That remote Bunker is already connected on this device.")
    member_ids = {_id(value.get("id"), "member") for value in snapshot.members}
    current_member_id = _id(snapshot.current_member_id, "enrolled member")
    if current_member_id not in member_ids:
        raise BunkerDatabaseError(
            "The remote snapshot does not contain the enrolled member."
        )
    local_id = str(uuid.uuid4())
    name = _clean_name(snapshot.bunker.get("name"), "Bunker")
    path = database_path(local_id)
    connection = _connect(path)
    try:
        _init_bunker(connection)
        now = _now()
        with connection:
            connection.execute(
                "INSERT INTO bunker(id, name, mode, created_at, updated_at) VALUES (?, ?, 'remote', ?, ?)",
                (local_id, name, now, now),
            )
            connection.execute(
                """
                INSERT INTO remote_connection(
                    singleton, server_url, remote_bunker_id, state,
                    credential_reference, config_revision, sync_cursor, last_synced_at,
                    current_member_id, capabilities_json
                ) VALUES (1, ?, ?, 'ready', ?, ?, ?, ?, ?, ?)
                """,
                (
                    invitation.server,
                    remote_id,
                    credential_reference,
                    snapshot.revision,
                    snapshot.cursor,
                    now,
                    current_member_id,
                    json.dumps(list(snapshot.capabilities), sort_keys=True),
                ),
            )
            _insert_remote_projects(connection, local_id, snapshot.projects)
            for value in snapshot.repository_bindings:
                identity = canonical_remote(str(value.get("canonical_remote") or ""))
                if not identity:
                    raise BunkerDatabaseError(
                        "The remote snapshot contains an invalid repository identity."
                    )
                connection.execute(
                    """
                    INSERT INTO repository_bindings(
                        id, bunker_id, project_id, name, canonical_remote,
                        default_branch, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _id(value.get("id"), "repository binding"),
                        local_id,
                        _id(value.get("project_id"), "project"),
                        _clean_name(value.get("name"), "repository"),
                        identity,
                        _repository_branch(value.get("default_branch")),
                        str(value.get("created_at") or now),
                        str(value.get("updated_at") or now),
                    ),
                )
            for value in snapshot.config_documents:
                connection.execute(
                    """
                    INSERT INTO config_documents(
                        scope_type, scope_id, kind, revision, content_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(value.get("scope_type") or "bunker"),
                        (
                            local_id
                            if str(value.get("scope_type") or "bunker") == "bunker"
                            and str(value.get("scope_id") or remote_id) == remote_id
                            else str(value.get("scope_id") or remote_id)
                        ),
                        str(value.get("kind") or "configuration"),
                        str(value.get("revision") or snapshot.revision),
                        json.dumps(value.get("content") or {}, sort_keys=True),
                        str(value.get("updated_at") or now),
                    ),
                )
            for value in snapshot.members:
                member_id = _id(value.get("id"), "member")
                member_capabilities = value.get("capabilities") or []
                if not isinstance(member_capabilities, list) or any(
                    not isinstance(item, str) for item in member_capabilities
                ):
                    raise BunkerDatabaseError(
                        "The remote snapshot contains invalid member capabilities."
                    )
                connection.execute(
                    """
                    INSERT INTO members(
                        id, display_name, email, role, state,
                        capabilities_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        member_id,
                        str(value.get("display_name") or value.get("name") or ""),
                        str(value.get("email") or ""),
                        str(value.get("role") or ""),
                        str(value.get("state") or "active"),
                        json.dumps(member_capabilities, sort_keys=True),
                        str(value.get("updated_at") or now),
                    ),
                )
            for value in snapshot.tickets:
                ticket_id = _id(value.get("id"), "ticket")
                connection.execute(
                    """
                    INSERT INTO tickets(
                        id, project_id, payload_json, source_kind,
                        source_reference, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticket_id,
                        _id(value.get("project_id"), "project"),
                        json.dumps(value.get("payload") or value, sort_keys=True),
                        str(value.get("source_kind") or "remote"),
                        str(value.get("source_reference") or ""),
                        str(value.get("updated_at") or now),
                    ),
                )
            for value in snapshot.reports:
                ticket_id = _id(value.get("ticket_id"), "ticket")
                scope = _clean_name(value.get("scope"), "report scope")
                raw_repository_id = str(value.get("repository_id") or "").strip()
                repository_id = (
                    _id(raw_repository_id, "repository") if raw_repository_id else ""
                )
                if scope.startswith("branch:") and not repository_id:
                    raise BunkerDatabaseError(
                        "A remote branch report does not identify its Git repository."
                    )
                if not scope.startswith("branch:") and repository_id:
                    raise BunkerDatabaseError(
                        "Only branch reports may identify a Git repository."
                    )
                if repository_id:
                    related = connection.execute(
                        """
                        SELECT 1
                        FROM repository_bindings rb
                        JOIN tickets t ON t.project_id = rb.project_id
                        WHERE rb.id = ? AND t.id = ?
                        """,
                        (repository_id, ticket_id),
                    ).fetchone()
                    if related is None:
                        raise BunkerDatabaseError(
                            "A remote report names a repository outside its ticket's Project."
                        )
                connection.execute(
                    """
                    INSERT INTO reports(ticket_id, scope, repository_id, content, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        ticket_id,
                        scope,
                        repository_id,
                        str(value.get("content") or ""),
                        str(value.get("updated_at") or now),
                    ),
                )
        _register(
            local_id,
            name,
            path,
            mode="remote",
            remote_server=invitation.server,
            remote_bunker_id=remote_id,
        )
    except (sqlite3.Error, BunkerDatabaseError, ValueError) as exc:
        connection.close()
        _discard_new_database(path)
        if isinstance(exc, BunkerDatabaseError):
            raise
        raise BunkerDatabaseError(
            "The remote snapshot could not be installed safely. No replica was kept."
        ) from exc
    finally:
        connection.close()
    return get_bunker(local_id)


def import_companion(source_root: str, *, bunker_name: str = "") -> dict[str, Any]:
    """Copy a repository-era project into a new local Bunker database.

    The source repository and companion checkouts are read-only throughout the
    import.  Nothing is removed or reconfigured, so the user may verify the new
    Bunker before retiring legacy storage.
    """
    root = _git_root(source_root)
    if str(root) in imported_companion_sources():
        raise BunkerDatabaseError(
            "That companion project has already been imported on this device."
        )
    locator = companion.load_locator(root)
    embedded_roots = (
        [
            path
            for path in sorted(layout.bunkers_dir(root).iterdir())
            if path.is_dir() and (path / "ticket.json").is_file()
        ]
        if layout.bunkers_dir(root).is_dir()
        else []
    )
    if locator is None and not embedded_roots:
        raise BunkerDatabaseError(
            "No repository-era TicketBunker data was found in that Git project."
        )
    if locator is not None:
        key = _project_key(locator.get("project"))
        project_doc_path = companion.project_config_dir(root, key) / "project.json"
        ticket_roots = (
            sorted(companion.tickets_dir(root, key).iterdir())
            if companion.tickets_dir(root, key).is_dir()
            else []
        )
        config_root = companion.project_config_dir(root, key)
        source_kind = "legacy-companion"
        default_branch = str(locator.get("base_branch") or "")
    else:
        first_payload = _payload_object(
            (embedded_roots[0] / "ticket.json").read_text(encoding="utf-8")
        )
        first_id = str(first_payload.get("id") or "TB-1")
        candidate_key = first_id.split("-", 1)[0].upper()
        key = _project_key(candidate_key if PROJECT_KEY_RE.fullmatch(candidate_key) else "TB")
        project_doc_path = layout.bunkers_dir(root) / "config" / "project.json"
        ticket_roots = embedded_roots
        config_root = layout.bunkers_dir(root) / "config"
        source_kind = "legacy-embedded"
        default_branch = _git_value(root, "branch", "--show-current")
    try:
        project_doc = json.loads(project_doc_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        project_doc = {}
    name = _clean_name(
        bunker_name or project_doc.get("name") or f"{key} Bunker", "Bunker"
    )
    created = create_bunker(
        name,
        project_key=key,
        project_name=str(project_doc.get("name") or key),
    )
    bunker_id = str(created["id"])
    project_id = str(created["projects"][0]["id"])
    repository_id = _attach_import_repository(
        bunker_id, project_id, root, default_branch=default_branch
    )
    imported_tickets: list[str] = []
    imported_reports = 0
    config_count = 0
    digest = hashlib.sha256(
        json.dumps(locator or {"embedded": True}, sort_keys=True).encode("utf-8")
    )
    now = _now()
    with _database(bunker_id) as connection:
        if config_root.is_dir():
            for path in sorted(config_root.rglob("*.json")):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                relative = path.relative_to(config_root).as_posix()
                raw = json.dumps(value, sort_keys=True)
                digest.update(relative.encode("utf-8"))
                digest.update(raw.encode("utf-8"))
                connection.execute(
                    """
                    INSERT INTO config_documents(
                        scope_type, scope_id, kind, revision, content_json, updated_at
                    ) VALUES ('project', ?, ?, ?, ?, ?)
                    ON CONFLICT(scope_type, scope_id, kind) DO UPDATE SET
                        revision = excluded.revision,
                        content_json = excluded.content_json,
                        updated_at = excluded.updated_at
                    """,
                    (project_id, f"legacy/{relative}", "legacy-import", raw, now),
                )
                config_count += 1
        if ticket_roots:
            for ticket_dir in ticket_roots:
                ticket_path = ticket_dir / "ticket.json"
                if not ticket_dir.is_dir() or not ticket_path.is_file():
                    continue
                try:
                    payload = json.loads(ticket_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                ticket_id = str(payload.get("id") or ticket_dir.name).strip()
                if not SAFE_ID_RE.fullmatch(ticket_id):
                    continue
                raw = json.dumps(payload, sort_keys=True)
                digest.update(ticket_id.encode("utf-8"))
                digest.update(raw.encode("utf-8"))
                connection.execute(
                    """
                    INSERT OR REPLACE INTO tickets(
                        id, project_id, payload_json, source_kind,
                        source_reference, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticket_id,
                        project_id,
                        raw,
                        source_kind,
                        f"{source_kind}:{key}/{ticket_id}",
                        now,
                    ),
                )
                imported_tickets.append(ticket_id)
                report_root = ticket_dir / "reports"
                report_paths = []
                if (report_root / "master.md").is_file():
                    report_paths.append(("combined", report_root / "master.md"))
                children = report_root / "branches"
                if children.is_dir():
                    report_paths.extend(
                        (f"branch:{path.stem}", path) for path in sorted(children.glob("*.md"))
                    )
                for scope, report_path in report_paths:
                    try:
                        content = report_path.read_text(encoding="utf-8")
                    except OSError:
                        continue
                    if scope.startswith("branch:"):
                        stamp = anchorcheck.verified_stamp(content) or {}
                        branch = str(stamp.get("branch") or "") or report_path.stem.replace("__", "/")
                        scope = f"branch:{branch}"
                    report_relative = report_path.relative_to(ticket_dir).as_posix()
                    digest.update(report_relative.encode("utf-8"))
                    digest.update(content.encode("utf-8"))
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO reports(
                            ticket_id, scope, repository_id, content, updated_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            ticket_id,
                            scope,
                            repository_id if scope.startswith("branch:") else "",
                            content,
                            now,
                        ),
                    )
                    imported_reports += 1
        migration_id = str(uuid.uuid4())
        source_digest = digest.hexdigest()
        detail = {
            "project": key,
            "tickets": imported_tickets,
            "report_count": imported_reports,
            "config_document_count": config_count,
            "source_unchanged": True,
        }
        connection.execute(
            """
            INSERT INTO migration_imports(
                id, source_kind, source_path, source_digest, imported_at, detail_json
            ) VALUES (?, 'companion-git', ?, ?, ?, ?)
            """,
            (
                migration_id,
                str(root),
                source_digest,
                now,
                json.dumps(detail, sort_keys=True),
            ),
        )
    return {
        "bunker": get_bunker(bunker_id),
        "migration": {
            "id": migration_id,
            "source": str(root),
            "source_digest": source_digest,
            "source_unchanged": True,
            "tickets": imported_tickets,
            "report_count": imported_reports,
            "config_document_count": config_count,
        },
    }


__all__ = [
    "BunkerDatabaseError",
    "attach_repository",
    "canonical_remote",
    "configure_remote",
    "create_bunker",
    "create_project",
    "create_replica",
    "data_root",
    "database_path",
    "detach_repository",
    "get_bunker",
    "get_configuration",
    "get_ticket",
    "import_companion",
    "imported_companion_sources",
    "list_bunkers",
    "list_tickets",
    "replace_configuration",
    "replace_repository_bindings",
    "registry_path",
]
