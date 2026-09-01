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
import re
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from . import agent_runtime, project as companion, remote


SCHEMA_VERSION = 1
PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9-]{0,31}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class BunkerDatabaseError(ValueError):
    """A stable local-model rejection that is safe to show to the user."""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def data_root() -> Path:
    return agent_runtime.data_root() / "bunkers"


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
    connection = sqlite3.connect(path)
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
            local_path TEXT NOT NULL UNIQUE,
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
            content TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (ticket_id, scope)
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
        CREATE UNIQUE INDEX IF NOT EXISTS repositories_remote_idx
            ON repository_bindings(bunker_id, canonical_remote)
            WHERE canonical_remote <> '';
        CREATE INDEX IF NOT EXISTS tickets_project_idx ON tickets(project_id, updated_at DESC);
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, _now()),
    )
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


def _project_tree(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    nodes = {str(row["id"]): {**dict(row), "children": []} for row in rows}
    roots: list[dict[str, Any]] = []
    for node in nodes.values():
        parent_id = str(node.get("parent_id") or "")
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)
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
                   rb.default_branch, lr.local_path, lr.last_seen_at
            FROM repository_bindings rb
            LEFT JOIN local_repositories lr ON lr.binding_id = rb.id
            ORDER BY rb.name COLLATE NOCASE
            """
        ).fetchall()
    ]
    tickets = int(connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
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
        "projects": _project_tree(projects),
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
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return ""
    if "://" not in value:
        match = re.fullmatch(r"(?:[^@/:]+@)?([^/:]+):(.+)", value)
        if not match:
            return ""
        host, path = match.group(1), match.group(2)
    else:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return ""
        if parsed.scheme not in {"https", "ssh", "git+ssh"} or not parsed.hostname:
            return ""
        host, path = parsed.hostname, parsed.path
    normalized_path = path.strip("/").removesuffix(".git")
    return f"{host.lower()}/{normalized_path}" if normalized_path else ""


def attach_repository(
    bunker_id: str,
    *,
    project_id: str,
    local_path: str,
    name: str = "",
    default_branch: str = "",
) -> dict[str, Any]:
    bunker_id = _id(bunker_id, "Bunker")
    project_id = _id(project_id, "project")
    root = _git_root(local_path)
    origin = _git_value(root, "remote", "get-url", "origin")
    identity = canonical_remote(origin)
    branch = str(default_branch or _git_value(root, "branch", "--show-current") or "main")
    display_name = _clean_name(name or root.name, "repository")
    now = _now()
    with _database(bunker_id) as connection:
        if connection.execute(
            "SELECT 1 FROM projects WHERE id = ? AND bunker_id = ?",
            (project_id, bunker_id),
        ).fetchone() is None:
            raise BunkerDatabaseError("Choose a project from this Bunker.")
        existing = None
        if identity:
            existing = connection.execute(
                """
                SELECT id, project_id FROM repository_bindings
                WHERE bunker_id = ? AND canonical_remote = ?
                """,
                (bunker_id, identity),
            ).fetchone()
        if existing and str(existing["project_id"]) != project_id:
            raise BunkerDatabaseError(
                "That Git repository is already attached to another project in this Bunker."
            )
        binding_id = str(existing["id"]) if existing else str(uuid.uuid4())
        try:
            if existing:
                connection.execute(
                    """
                    UPDATE repository_bindings
                    SET project_id = ?, name = ?, default_branch = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (project_id, display_name, branch, now, binding_id),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO repository_bindings(
                        id, bunker_id, project_id, name, canonical_remote,
                        default_branch, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (binding_id, bunker_id, project_id, display_name, identity, branch, now, now),
                )
            connection.execute(
                """
                INSERT INTO local_repositories(binding_id, local_path, attached_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(binding_id) DO UPDATE SET
                    local_path = excluded.local_path,
                    last_seen_at = excluded.last_seen_at
                """,
                (binding_id, str(root), now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise BunkerDatabaseError(
                "That local Git folder is already attached to another repository in this Bunker."
            ) from exc
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
                        str(value.get("canonical_remote") or ""),
                        str(value.get("default_branch") or ""),
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
                connection.execute(
                    """
                    INSERT INTO reports(ticket_id, scope, content, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        _id(value.get("ticket_id"), "ticket"),
                        _clean_name(value.get("scope"), "report scope"),
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
    """Copy a companion-Git project into a new local Bunker database.

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
    if locator is None:
        raise BunkerDatabaseError(
            "No legacy TicketBunker locator was found in that Git project."
        )
    key = _project_key(locator.get("project"))
    project_doc_path = companion.project_config_dir(root, key) / "project.json"
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
    attach_repository(
        bunker_id,
        project_id=project_id,
        local_path=str(root),
        default_branch=str(locator.get("base_branch") or ""),
    )
    ticket_root = companion.tickets_dir(root, key)
    imported_tickets: list[str] = []
    imported_reports = 0
    config_count = 0
    digest = hashlib.sha256(
        json.dumps(locator, sort_keys=True).encode("utf-8")
    )
    now = _now()
    with _database(bunker_id) as connection:
        config_root = companion.project_config_dir(root, key)
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
        if ticket_root.is_dir():
            for ticket_dir in sorted(ticket_root.iterdir()):
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
                    ) VALUES (?, ?, ?, 'legacy-companion', ?, ?)
                    """,
                    (
                        ticket_id,
                        project_id,
                        raw,
                        f"legacy-companion:{key}/{ticket_id}",
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
                        INSERT OR REPLACE INTO reports(ticket_id, scope, content, updated_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (ticket_id, scope, content, now),
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
    "get_bunker",
    "import_companion",
    "imported_companion_sources",
    "list_bunkers",
    "registry_path",
]
