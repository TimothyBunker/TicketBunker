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

from . import layout, platform_services, project as companion, remote


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
) -> list[dict[str, Any]]:
    """Nest Project rows and give each node its ticket count.

    A Project's ``ticket_count`` includes its descendants, matching what the
    ticket catalog shows when that Project is selected (``_project_scope``).
    """
    counts = dict(ticket_counts or {})
    nodes = {
        str(row["id"]): {
            **dict(row),
            "children": [],
            "ticket_count": int(counts.get(str(row["id"]), 0)),
        }
        for row in rows
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
        "projects": _project_tree(projects, ticket_counts),
        "project_count": len(projects),
        "repositories": repositories,
        "repository_count": len(repositories),
        "ticket_count": tickets,
    }


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
    # A remote snapshot is deliberately never a local authorization oracle.
    # The future authenticated sync broker may publish a typed change, but this
    # local database cannot promote itself even when its cached capability list
    # includes config:write.
    return {
        "can_write": False,
        "reason": f"Shared configuration is controlled by {server}; connect and synchronize there to publish changes.",
        "capabilities": capabilities,
    }


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
              AND kind IN ('ticket-fields', 'integrations')
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
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "documents": documents,
        "permission": permission,
        "composition": "pinned" if scope_type == "project" else "defaults-for-new-projects",
        "repositories": repositories,
    }


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


def replace_configuration(
    bunker_id: str,
    *,
    project_id: str | None,
    kind: str,
    expected_revision: str,
    content: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace one complete configuration document with optimistic locking."""
    bunker_id = _id(bunker_id, "Bunker")
    document_kind = str(kind or "").strip()
    if document_kind not in {"ticket-fields", "integrations"}:
        raise BunkerDatabaseError("Choose Ticket fields or Integrations to publish.")
    if not isinstance(content, Mapping):
        raise BunkerDatabaseError("Configuration content must be one object.")
    with _database(bunker_id) as connection:
        scope_type, scope_id, bunker = _config_scope(
            connection, bunker_id, project_id
        )
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
        if current is None:
            raise BunkerDatabaseError("That configuration document is missing. Reopen the editor.")
        current_revision = str(current["revision"])
        if not expected_revision or expected_revision != current_revision:
            raise BunkerDatabaseError(
                "Configuration changed after this editor opened. Reload it and review the latest revision."
            )
        if document_kind == "ticket-fields":
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
        match = re.fullmatch(r"local:(\d+)", current_revision)
        revision = f"local:{int(match.group(1)) + 1}" if match else f"local:{uuid.uuid4()}"
        now = _now()
        changed = connection.execute(
            """
            UPDATE config_documents
            SET revision = ?, content_json = ?, updated_at = ?
            WHERE scope_type = ? AND scope_id = ? AND kind = ? AND revision = ?
            """,
            (
                revision,
                raw,
                now,
                scope_type,
                scope_id,
                document_kind,
                current_revision,
            ),
        ).rowcount
        if changed != 1:
            raise BunkerDatabaseError(
                "Configuration changed while it was publishing. Reload it and retry."
            )
    return get_configuration(bunker_id, project_id=project_id)


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
        "type": str(payload.get("type") or payload.get("issue_type") or "Task"),
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


def _repository_branches(repository: Mapping[str, Any], ticket_id: str) -> list[dict[str, Any]]:
    raw_path = str(repository.get("local_path") or "")
    if not raw_path:
        return []
    root = Path(raw_path)
    current = _git_value(root, "branch", "--show-current")
    raw = _git_value(root, "branch", "--all", "--format=%(refname:short)")
    needle = ticket_id.casefold()
    values: list[dict[str, Any]] = []
    for name in sorted({line.strip() for line in raw.splitlines() if line.strip()}):
        if name.endswith("/HEAD") or needle not in name.casefold():
            continue
        values.append(
            {
                "name": name,
                "current": name == current,
                "remote": name.startswith("remotes/") or name.startswith("origin/"),
                "repository_id": str(repository.get("id") or ""),
                "repository_name": str(repository.get("name") or root.name),
            }
        )
    return values


def _refresh_legacy_ticket(bunker_id: str, ticket_id: str) -> None:
    """Refresh one imported ticket from its attached checkout without networking."""
    with _database(bunker_id) as connection:
        source = connection.execute(
            """
            SELECT t.source_kind, t.source_reference, p.key, rb.id AS repository_id,
                   lr.local_path
            FROM tickets t
            JOIN projects p ON p.id = t.project_id
            JOIN repository_bindings rb ON rb.project_id = p.id
            JOIN local_repositories lr ON lr.binding_id = rb.id
            WHERE t.id = ? LIMIT 1
            """,
            (ticket_id,),
        ).fetchone()
    if source is None or str(source["source_kind"]) not in {
        "legacy-companion",
        "legacy-embedded",
    }:
        return
    root = Path(str(source["local_path"]))
    if str(source["source_kind"]) == "legacy-embedded":
        candidates = [
            path
            for path in layout.bunkers_dir(root).iterdir()
            if path.is_dir() and (path / "ticket.json").is_file()
        ] if layout.bunkers_dir(root).is_dir() else []
        ticket_root = next(
            (
                path
                for path in candidates
                if str(_payload_object((path / "ticket.json").read_text(encoding="utf-8")).get("id") or "")
                == ticket_id
            ),
            layout.bunkers_dir(root) / f"{ticket_id}_bunker",
        )
    else:
        ticket_root = companion.tickets_dir(root, str(source["key"])) / ticket_id
    ticket_path = ticket_root / "ticket.json"
    try:
        payload = json.loads(ticket_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    report_values: list[tuple[str, str]] = []
    master_path = ticket_root / "reports" / "master.md"
    if master_path.is_file():
        try:
            report_values.append(("combined", master_path.read_text(encoding="utf-8")))
        except OSError:
            pass
    branch_root = ticket_root / "reports" / "branches"
    if branch_root.is_dir():
        for path in sorted(branch_root.glob("*.md")):
            try:
                report_values.append((f"branch:{path.stem}", path.read_text(encoding="utf-8")))
            except OSError:
                continue
    now = _now()
    with _database(bunker_id) as connection:
        connection.execute(
            "UPDATE tickets SET payload_json = ?, updated_at = ? WHERE id = ?",
            (
                json.dumps(payload, sort_keys=True),
                str(payload.get("updated") or payload.get("updated_at") or now),
                ticket_id,
            ),
        )
        connection.execute("DELETE FROM reports WHERE ticket_id = ?", (ticket_id,))
        connection.executemany(
            """
            INSERT INTO reports(ticket_id, scope, repository_id, content, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    ticket_id,
                    scope,
                    str(source["repository_id"]) if scope.startswith("branch:") else "",
                    content,
                    now,
                )
                for scope, content in report_values
            ],
        )


def get_ticket(bunker_id: str, ticket_id: str) -> dict[str, Any]:
    """Return one complete local ticket snapshot without network activity."""
    bunker_id = _id(bunker_id, "Bunker")
    ticket_id = _id(ticket_id, "ticket")
    _refresh_legacy_ticket(bunker_id, ticket_id)
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
    branches: list[dict[str, Any]] = []
    for repository in repositories:
        branches.extend(_repository_branches(repository, ticket_id))
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
                    "remote": False,
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


def create_project(
    bunker_id: str,
    *,
    key: str,
    name: str,
    parent_id: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    bunker_id = _id(bunker_id, "Bunker")
    project_id = str(uuid.uuid4())
    project_key = _project_key(key)
    project_name = _clean_name(name, "project")
    parent = _id(parent_id, "parent project") if parent_id else None
    now = _now()
    try:
        with _database(bunker_id) as connection:
            if parent and connection.execute(
                "SELECT 1 FROM projects WHERE id = ? AND bunker_id = ?",
                (parent, bunker_id),
            ).fetchone() is None:
                raise BunkerDatabaseError("Choose a parent project from this Bunker.")
            connection.execute(
                """
                INSERT INTO projects(
                    id, bunker_id, parent_id, key, name, description, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, bunker_id, parent, project_key, project_name, description, now, now),
            )
            _copy_project_config(connection, bunker_id, project_id)
    except sqlite3.IntegrityError as exc:
        raise BunkerDatabaseError(
            f"Project key {project_key} already exists in this Bunker."
        ) from exc
    return get_bunker(bunker_id)


def _git_root(raw: str) -> Path:
    selected = Path(str(raw or "")).expanduser()
    if not selected.is_dir():
        raise BunkerDatabaseError("Choose an existing Git project folder.")
    process = subprocess.run(
        ["git", "-C", str(selected), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if process.returncode != 0 or not process.stdout.strip():
        raise BunkerDatabaseError("Choose a folder inside a Git working tree.")
    return Path(process.stdout.strip()).resolve()


def _git_value(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
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
    connection: sqlite3.Connection, project_id: str
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
            WHERE rb.project_id = ?
            ORDER BY rb.name COLLATE NOCASE, rb.canonical_remote, rb.id
            """,
            (project_id,),
        ).fetchall()
    ]


def _repository_revision(rows: Iterable[Mapping[str, Any]]) -> str:
    shared = [
        {
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "canonical_remote": str(row.get("canonical_remote") or ""),
            "default_branch": str(row.get("default_branch") or ""),
        }
        for row in rows
    ]
    raw = json.dumps(shared, sort_keys=True, separators=(",", ":"))
    return "repositories:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


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


def replace_repository_bindings(
    bunker_id: str,
    *,
    project_id: str,
    expected_revision: str,
    repositories: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Replace one Project's shared repository allowlist.

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
        permission = _config_permission(connection, bunker)
        if not permission["can_write"]:
            raise BunkerDatabaseError(str(permission["reason"]))
        connection.execute("BEGIN IMMEDIATE")
        current_rows = _repository_rows(connection, project_id)
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
        now = _now()
        for value in normalized:
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
        if removed:
            placeholders = ",".join("?" for _ in removed)
            connection.execute(
                f"DELETE FROM repository_bindings WHERE project_id = ? AND id IN ({placeholders})",
                (project_id, *sorted(removed)),
            )
    return get_configuration(bunker_id, project_id=project_id)


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
    origin = _git_value(root, "remote", "get-url", "origin")
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
                f"This Project does not allow {identity}. Ask a Bunker configurator to add it under Project > Configure > Repositories."
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
    origin = _git_value(root, "remote", "get-url", "origin")
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
