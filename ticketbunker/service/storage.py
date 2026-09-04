"""Storage for the reference TicketBunker service (DESIGN.md §3.20).

This is the service's own storage boundary and is deliberately separate from
``bunkerdb.py``: a host owns this database, clients only ever see the
versioned contract. Bearer and invitation tokens are stored as SHA-256 digests.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from .. import remote

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
SNAPSHOT_KINDS = ("project", "repository-binding", "config-document", "ticket", "report")
MAX_LIST = 20000


class ServiceError(Exception):
    """A stable, client-renderable service failure."""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "message": self.message, **self.extra}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _after(seconds: int) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=int(seconds))
    ).isoformat(timespec="seconds")


def _expired(value: str) -> bool:
    try:
        moment = datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment <= datetime.datetime.now(datetime.timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _token() -> str:
    return secrets.token_urlsafe(32)


def _identifier(raw: Any, label: str) -> str:
    value = str(raw or "").strip()
    if not ID_RE.fullmatch(value):
        raise ServiceError(400, "bunker_invalid", f"The {label} identifier is not valid.")
    return value


def _name(raw: Any, label: str, limit: int = 120) -> str:
    value = " ".join(str(raw or "").split())
    if not value or len(value) > limit:
        raise ServiceError(400, "bunker_invalid", f"Enter a {label} of {limit} characters or fewer.")
    return value


def _objects(value: Any, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_LIST:
        raise ServiceError(400, "bunker_invalid", f"The {label} list is not valid.")
    if any(not isinstance(item, dict) for item in value):
        raise ServiceError(400, "bunker_invalid", f"Each {label} entry must be an object.")
    return [dict(item) for item in value]


class ServiceStore:
    """SQLite-backed service state with bounded, closing transactions."""

    def __init__(self, path: Path):
        self.path = Path(path)

    # -- connection and schema -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level="DEFERRED")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS service (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        setup_token_hash TEXT NOT NULL DEFAULT '',
                        setup_completed_at TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        email TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS credentials (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        created_at TEXT NOT NULL,
                        last_used_at TEXT NOT NULL DEFAULT '',
                        revoked_at TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE IF NOT EXISTS bunkers (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        owner_user_id TEXT NOT NULL REFERENCES users(id),
                        revision INTEGER NOT NULL DEFAULT 1,
                        change_seq INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS memberships (
                        bunker_id TEXT NOT NULL REFERENCES bunkers(id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        role TEXT NOT NULL,
                        state TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (bunker_id, user_id)
                    );
                    CREATE TABLE IF NOT EXISTS invitations (
                        id TEXT PRIMARY KEY,
                        bunker_id TEXT NOT NULL REFERENCES bunkers(id) ON DELETE CASCADE,
                        token_hash TEXT NOT NULL UNIQUE,
                        role TEXT NOT NULL,
                        created_by TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        redeemed_by TEXT NOT NULL DEFAULT '',
                        redeemed_at TEXT NOT NULL DEFAULT '',
                        revoked_at TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE IF NOT EXISTS records (
                        bunker_id TEXT NOT NULL REFERENCES bunkers(id) ON DELETE CASCADE,
                        kind TEXT NOT NULL,
                        record_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        revision TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (bunker_id, kind, record_id)
                    );
                    CREATE TABLE IF NOT EXISTS changes (
                        bunker_id TEXT NOT NULL REFERENCES bunkers(id) ON DELETE CASCADE,
                        seq INTEGER NOT NULL,
                        key TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        record_id TEXT NOT NULL,
                        revision TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        applied_at TEXT NOT NULL,
                        PRIMARY KEY (bunker_id, seq),
                        UNIQUE (bunker_id, key)
                    );
                    """
                )
                connection.execute(
                    "INSERT OR IGNORE INTO service(singleton, id, created_at) VALUES (1, ?, ?)",
                    (str(uuid.uuid4()), _now()),
                )
        finally:
            connection.close()

    # -- setup and authentication ---------------------------------------------

    def setup_state(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT id, setup_token_hash, setup_completed_at FROM service WHERE singleton = 1"
            ).fetchone()
            owners = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        finally:
            connection.close()
        return {
            "service_id": str(row["id"]) if row else "",
            "setup_completed": bool(row and row["setup_completed_at"]),
            "setup_token_present": bool(row and row["setup_token_hash"]),
            "users": owners,
        }

    def issue_setup_token(self, *, force: bool = False) -> str | None:
        """Create the one-time owner setup token; None when setup already finished."""
        connection = self._connect()
        try:
            with connection:
                row = connection.execute(
                    "SELECT setup_token_hash, setup_completed_at FROM service WHERE singleton = 1"
                ).fetchone()
                if row is None:
                    raise ServiceError(500, "service_uninitialized", "Run initialize() first.")
                if row["setup_completed_at"] and not force:
                    return None
                if row["setup_token_hash"] and not force:
                    # A token was already printed; do not silently rotate it.
                    return None
                token = _token()
                connection.execute(
                    """
                    UPDATE service SET setup_token_hash = ?, setup_completed_at = ''
                    WHERE singleton = 1
                    """,
                    (_digest(token),),
                )
                return token
        finally:
            connection.close()

    def complete_setup(self, setup_token: str, profile: Mapping[str, Any]) -> dict[str, Any]:
        display_name, email = _profile(profile)
        connection = self._connect()
        try:
            with connection:
                row = connection.execute(
                    "SELECT setup_token_hash, setup_completed_at FROM service WHERE singleton = 1"
                ).fetchone()
                if row is None or row["setup_completed_at"]:
                    raise ServiceError(409, "setup_complete", "This server already has an owner.")
                expected = str(row["setup_token_hash"] or "")
                if not expected or not hmac.compare_digest(expected, _digest(str(setup_token or ""))):
                    raise ServiceError(401, "setup_token_invalid", "The setup token is not valid.")
                user_id = str(uuid.uuid4())
                now = _now()
                connection.execute(
                    "INSERT INTO users(id, display_name, email, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, display_name, email, now),
                )
                credential = self._issue_credential(connection, user_id)
                connection.execute(
                    "UPDATE service SET setup_token_hash = '', setup_completed_at = ? WHERE singleton = 1",
                    (now,),
                )
        finally:
            connection.close()
        return {
            "user": {"id": user_id, "display_name": display_name, "email": email},
            "credential": credential,
        }

    def _issue_credential(self, connection: sqlite3.Connection, user_id: str) -> str:
        credential = _token()
        connection.execute(
            "INSERT INTO credentials(token_hash, user_id, created_at) VALUES (?, ?, ?)",
            (_digest(credential), user_id, _now()),
        )
        return credential

    def authenticate(self, bearer: str) -> dict[str, Any] | None:
        value = str(bearer or "").strip()
        if not value or len(value) > 512:
            return None
        connection = self._connect()
        try:
            with connection:
                row = connection.execute(
                    """
                    SELECT c.token_hash, u.id, u.display_name, u.email
                    FROM credentials c JOIN users u ON u.id = c.user_id
                    WHERE c.token_hash = ? AND c.revoked_at = ''
                    """,
                    (_digest(value),),
                ).fetchone()
                if row is None:
                    return None
                connection.execute(
                    "UPDATE credentials SET last_used_at = ? WHERE token_hash = ?",
                    (_now(), row["token_hash"]),
                )
                return {"id": str(row["id"]), "display_name": str(row["display_name"]), "email": str(row["email"])}
        finally:
            connection.close()

    # -- membership -------------------------------------------------------------

    @staticmethod
    def _membership(
        connection: sqlite3.Connection, bunker_id: str, user_id: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT role, state FROM memberships
            WHERE bunker_id = ? AND user_id = ? AND state = 'active'
            """,
            (bunker_id, user_id),
        ).fetchone()
        return dict(row) if row else None

    def _require_member(
        self,
        connection: sqlite3.Connection,
        bunker_id: str,
        user_id: str,
        capability: str = "",
    ) -> dict[str, Any]:
        bunker = connection.execute(
            "SELECT id FROM bunkers WHERE id = ?", (bunker_id,)
        ).fetchone()
        membership = self._membership(connection, bunker_id, user_id) if bunker else None
        if membership is None:
            # Non-members learn nothing about whether the Bunker exists.
            raise ServiceError(404, "bunker_unavailable", "That Bunker is not available to this user.")
        if capability and capability not in remote.capabilities_for(membership["role"]):
            raise ServiceError(403, "capability_required", "This member lacks that capability.")
        return membership

    # -- publication -------------------------------------------------------------

    def publish_bunker(self, user_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        publication = _validated_publication(payload)
        connection = self._connect()
        try:
            with connection:
                requested = publication["bunker"].get("id") or ""
                bunker_id = requested if requested and connection.execute(
                    "SELECT 1 FROM bunkers WHERE id = ?", (requested,)
                ).fetchone() is None else str(uuid.uuid4())
                now = _now()
                connection.execute(
                    """
                    INSERT INTO bunkers(id, name, owner_user_id, revision, change_seq, created_at, updated_at)
                    VALUES (?, ?, ?, 1, 0, ?, ?)
                    """,
                    (bunker_id, publication["bunker"]["name"], user_id, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO memberships(bunker_id, user_id, role, state, created_at, updated_at)
                    VALUES (?, ?, ?, 'active', ?, ?)
                    """,
                    (bunker_id, user_id, remote.ROLE_OWNER, now, now),
                )
                for kind, items in publication["records"].items():
                    for record_id, value in items:
                        if kind == "config-document" and value.get("scope_type") == "bunker":
                            value = {**value, "scope_id": bunker_id}
                            record_id = f"bunker:{bunker_id}:{value['kind']}"
                        connection.execute(
                            """
                            INSERT INTO records(bunker_id, kind, record_id, payload_json, revision, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                bunker_id,
                                kind,
                                record_id,
                                json.dumps(value, sort_keys=True),
                                str(value.get("revision") or "1"),
                                str(value.get("updated_at") or now),
                            ),
                        )
                snapshot = self._snapshot(connection, bunker_id, user_id)
        finally:
            connection.close()
        return snapshot

    # -- snapshots ---------------------------------------------------------------

    def snapshot(self, bunker_id: str, user_id: str, *, after: str = "") -> dict[str, Any]:
        bunker_id = _identifier(bunker_id, "Bunker")
        connection = self._connect()
        try:
            self._require_member(connection, bunker_id, user_id)
            if after:
                return self._changes_after(connection, bunker_id, user_id, after)
            return self._snapshot(connection, bunker_id, user_id)
        finally:
            connection.close()

    def _members(self, connection: sqlite3.Connection, bunker_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT u.id, u.display_name, u.email, m.role, m.state, m.updated_at
            FROM memberships m JOIN users u ON u.id = m.user_id
            WHERE m.bunker_id = ?
            ORDER BY CASE m.role WHEN 'owner' THEN 0 ELSE 1 END, u.display_name COLLATE NOCASE
            """,
            (bunker_id,),
        ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "display_name": str(row["display_name"]),
                "email": str(row["email"]),
                "role": str(row["role"]),
                "state": str(row["state"]),
                "capabilities": list(remote.capabilities_for(row["role"])),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ]

    def _records(self, connection: sqlite3.Connection, bunker_id: str, kind: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT record_id, payload_json, revision, updated_at FROM records
            WHERE bunker_id = ? AND kind = ? ORDER BY record_id
            """,
            (bunker_id, kind),
        ).fetchall()
        values = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except ValueError:
                continue
            if isinstance(payload, dict):
                values.append({**payload, "revision": str(row["revision"]), "updated_at": str(row["updated_at"])})
        return values

    def _snapshot(self, connection: sqlite3.Connection, bunker_id: str, user_id: str) -> dict[str, Any]:
        bunker = connection.execute(
            "SELECT id, name, revision, change_seq FROM bunkers WHERE id = ?", (bunker_id,)
        ).fetchone()
        membership = self._membership(connection, bunker_id, user_id) or {"role": ""}
        return {
            "bunker": {"id": str(bunker["id"]), "name": str(bunker["name"])},
            "projects": self._records(connection, bunker_id, "project"),
            "repository_bindings": self._records(connection, bunker_id, "repository-binding"),
            "config_documents": self._records(connection, bunker_id, "config-document"),
            "tickets": self._records(connection, bunker_id, "ticket"),
            "reports": self._records(connection, bunker_id, "report"),
            "members": self._members(connection, bunker_id),
            "current_member_id": user_id,
            "capabilities": list(remote.capabilities_for(membership["role"])),
            "revision": str(bunker["revision"]),
            "cursor": f"change-{int(bunker['change_seq'])}",
        }

    def _changes_after(
        self, connection: sqlite3.Connection, bunker_id: str, user_id: str, cursor: str
    ) -> dict[str, Any]:
        match = re.fullmatch(r"change-(\d+)", str(cursor or ""))
        if not match:
            raise ServiceError(409, "cursor_invalid", "The sync cursor is not valid.")
        since = int(match.group(1))
        rows = connection.execute(
            """
            SELECT c.seq, c.kind, c.record_id, c.revision, c.applied_at, r.payload_json
            FROM changes c
            LEFT JOIN records r ON r.bunker_id = c.bunker_id AND r.kind = c.kind AND r.record_id = c.record_id
            WHERE c.bunker_id = ? AND c.seq > ? ORDER BY c.seq
            """,
            (bunker_id, since),
        ).fetchall()
        changes = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"])) if row["payload_json"] else None
            except ValueError:
                payload = None
            changes.append(
                {
                    "seq": int(row["seq"]),
                    "kind": str(row["kind"]),
                    "record_id": str(row["record_id"]),
                    "revision": str(row["revision"]),
                    "applied_at": str(row["applied_at"]),
                    "payload": payload,
                }
            )
        bunker = connection.execute(
            "SELECT revision, change_seq FROM bunkers WHERE id = ?", (bunker_id,)
        ).fetchone()
        snapshot = self._snapshot(connection, bunker_id, user_id)
        return {
            **snapshot,
            "changes": changes,
            "revision": str(bunker["revision"]),
            "cursor": f"change-{int(bunker['change_seq'])}",
        }

    # -- invitations -------------------------------------------------------------

    def create_invitation(
        self, bunker_id: str, user_id: str, *, role: str = remote.ROLE_MEMBER, expires_in: int = remote.DEFAULT_INVITATION_SECONDS
    ) -> dict[str, Any]:
        bunker_id = _identifier(bunker_id, "Bunker")
        try:
            selected_role = remote.normalize_role(role)
        except remote.RemoteContractError as exc:
            raise ServiceError(400, "role_invalid", str(exc)) from exc
        try:
            seconds = int(expires_in)
        except (TypeError, ValueError) as exc:
            raise ServiceError(400, "expiry_invalid", "The invitation expiry is not valid.") from exc
        if seconds < 60 or seconds > remote.MAX_INVITATION_SECONDS:
            raise ServiceError(400, "expiry_invalid", "Invitations last between one minute and ninety days.")
        connection = self._connect()
        try:
            with connection:
                self._require_member(connection, bunker_id, user_id, "members:invite")
                token = _token()
                invitation_id = uuid.uuid4().hex
                now = _now()
                expires_at = _after(seconds)
                connection.execute(
                    """
                    INSERT INTO invitations(id, bunker_id, token_hash, role, created_by, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (invitation_id, bunker_id, _digest(token), selected_role, user_id, now, expires_at),
                )
        finally:
            connection.close()
        return {
            "id": invitation_id,
            "bunker_id": bunker_id,
            "role": selected_role,
            "token": token,
            "created_at": now,
            "expires_at": expires_at,
        }

    def list_invitations(self, bunker_id: str, user_id: str) -> list[dict[str, Any]]:
        bunker_id = _identifier(bunker_id, "Bunker")
        connection = self._connect()
        try:
            self._require_member(connection, bunker_id, user_id, "members:invite")
            rows = connection.execute(
                """
                SELECT i.id, i.role, i.created_at, i.expires_at, i.redeemed_at, i.revoked_at,
                       u.display_name AS created_by_name
                FROM invitations i LEFT JOIN users u ON u.id = i.created_by
                WHERE i.bunker_id = ? ORDER BY i.created_at DESC
                """,
                (bunker_id,),
            ).fetchall()
        finally:
            connection.close()
        values = []
        for row in rows:
            if row["redeemed_at"] or row["revoked_at"]:
                state = "redeemed" if row["redeemed_at"] else "revoked"
            elif _expired(str(row["expires_at"])):
                state = "expired"
            else:
                state = "pending"
            values.append(
                {
                    "id": str(row["id"]),
                    "role": str(row["role"]),
                    "state": state,
                    "created_at": str(row["created_at"]),
                    "created_by": str(row["created_by_name"] or ""),
                    "expires_at": str(row["expires_at"]),
                }
            )
        return values

    def revoke_invitation(self, bunker_id: str, user_id: str, invitation_id: str) -> None:
        bunker_id = _identifier(bunker_id, "Bunker")
        invitation_id = _identifier(invitation_id, "invitation")
        connection = self._connect()
        try:
            with connection:
                self._require_member(connection, bunker_id, user_id, "members:invite")
                changed = connection.execute(
                    """
                    UPDATE invitations SET revoked_at = ?
                    WHERE id = ? AND bunker_id = ? AND redeemed_at = '' AND revoked_at = ''
                    """,
                    (_now(), invitation_id, bunker_id),
                ).rowcount
                if not changed:
                    raise ServiceError(404, "invitation_invalid", "That invitation is not pending.")
        finally:
            connection.close()

    def _invitation(
        self, connection: sqlite3.Connection, bunker_id: str, token: str
    ) -> dict[str, Any]:
        value = str(token or "")
        if not value or len(value) > 2048:
            raise ServiceError(404, "invitation_invalid", "That invitation is not valid.")
        row = connection.execute(
            """
            SELECT i.id, i.role, i.expires_at, i.redeemed_at, i.revoked_at, b.name AS bunker_name
            FROM invitations i JOIN bunkers b ON b.id = i.bunker_id
            WHERE i.bunker_id = ? AND i.token_hash = ?
            """,
            (bunker_id, _digest(value)),
        ).fetchone()
        if row is None:
            raise ServiceError(404, "invitation_invalid", "That invitation is not valid.")
        if row["revoked_at"]:
            raise ServiceError(410, "invitation_revoked", "That invitation was revoked.")
        if row["redeemed_at"]:
            raise ServiceError(410, "invitation_used", "That invitation was already used.")
        if _expired(str(row["expires_at"])):
            raise ServiceError(410, "invitation_expired", "That invitation expired.")
        return dict(row)

    def inspect_invitation(self, bunker_id: str, token: str) -> dict[str, Any]:
        bunker_id = _identifier(bunker_id, "Bunker")
        connection = self._connect()
        try:
            row = self._invitation(connection, bunker_id, token)
        finally:
            connection.close()
        return {
            "bunker": {"id": bunker_id, "name": str(row["bunker_name"])},
            "role": str(row["role"]),
            "expires_at": str(row["expires_at"]),
        }

    def redeem_invitation(
        self,
        bunker_id: str,
        token: str,
        *,
        user_id: str = "",
        profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        bunker_id = _identifier(bunker_id, "Bunker")
        credential = ""
        connection = self._connect()
        try:
            with connection:
                invitation = self._invitation(connection, bunker_id, token)
                now = _now()
                if user_id:
                    if self._membership(connection, bunker_id, user_id):
                        # Idempotent for the same authenticated user: no duplicate
                        # membership and the invitation stays available.
                        return {"snapshot": self._snapshot(connection, bunker_id, user_id)}
                    member_id = user_id
                else:
                    if profile is None:
                        raise ServiceError(401, "profile_required", "Sign in or send a profile to join.")
                    display_name, email = _profile(profile)
                    member_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO users(id, display_name, email, created_at) VALUES (?, ?, ?, ?)",
                        (member_id, display_name, email, now),
                    )
                    credential = self._issue_credential(connection, member_id)
                connection.execute(
                    """
                    INSERT INTO memberships(bunker_id, user_id, role, state, created_at, updated_at)
                    VALUES (?, ?, ?, 'active', ?, ?)
                    """,
                    (bunker_id, member_id, str(invitation["role"]), now, now),
                )
                connection.execute(
                    "UPDATE invitations SET redeemed_by = ?, redeemed_at = ? WHERE id = ?",
                    (member_id, now, str(invitation["id"])),
                )
                snapshot = self._snapshot(connection, bunker_id, member_id)
        finally:
            connection.close()
        result: dict[str, Any] = {"snapshot": snapshot}
        if credential:
            result["credential"] = credential
        return result

    def members(self, bunker_id: str, user_id: str) -> list[dict[str, Any]]:
        bunker_id = _identifier(bunker_id, "Bunker")
        connection = self._connect()
        try:
            self._require_member(connection, bunker_id, user_id)
            return self._members(connection, bunker_id)
        finally:
            connection.close()

    # -- typed changes -----------------------------------------------------------

    def apply_changes(
        self, bunker_id: str, user_id: str, cursor: str, changes: Iterable[Mapping[str, Any]]
    ) -> dict[str, Any]:
        bunker_id = _identifier(bunker_id, "Bunker")
        items = list(changes)
        if not items or len(items) > 200:
            raise ServiceError(400, "changes_invalid", "Send between one and two hundred changes.")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_member(connection, bunker_id, user_id, "config:write")
                results = []
                conflicts = []
                for raw in items:
                    if not isinstance(raw, Mapping):
                        raise ServiceError(400, "changes_invalid", "Each change must be an object.")
                    key = _identifier(raw.get("key"), "change key")
                    existing = connection.execute(
                        "SELECT revision FROM changes WHERE bunker_id = ? AND key = ?",
                        (bunker_id, key),
                    ).fetchone()
                    if existing is not None:
                        results.append({"key": key, "ok": True, "revision": str(existing["revision"]), "replayed": True})
                        continue
                    try:
                        revision = self._apply_one(connection, bunker_id, raw)
                    except ServiceError as exc:
                        if exc.code in {"revision_conflict", "project_key_taken"}:
                            conflicts.append({"key": key, "ok": False, "code": exc.code, "message": exc.message})
                            continue
                        raise
                    seq = int(
                        connection.execute(
                            "SELECT change_seq FROM bunkers WHERE id = ?", (bunker_id,)
                        ).fetchone()[0]
                    ) + 1
                    connection.execute(
                        """
                        UPDATE bunkers SET change_seq = ?, revision = revision + 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (seq, _now(), bunker_id),
                    )
                    connection.execute(
                        """
                        INSERT INTO changes(bunker_id, seq, key, kind, record_id, revision, user_id, applied_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (bunker_id, seq, key, str(raw.get("kind")), str(raw.get("record_id")), revision, user_id, _now()),
                    )
                    results.append({"key": key, "ok": True, "revision": revision})
                if conflicts:
                    connection.rollback()
                    codes = {str(item["code"]) for item in conflicts}
                    raise ServiceError(
                        409,
                        conflicts[0]["code"] if len(codes) == 1 else "revision_conflict",
                        "One or more changes conflict with the shared Bunker.",
                        results=conflicts,
                    )
                bunker = connection.execute(
                    "SELECT revision, change_seq FROM bunkers WHERE id = ?", (bunker_id,)
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        finally:
            connection.close()
        return {
            "results": results,
            "revision": str(bunker["revision"]),
            "cursor": f"change-{int(bunker['change_seq'])}",
        }

    def _apply_one(self, connection: sqlite3.Connection, bunker_id: str, raw: Mapping[str, Any]) -> str:
        kind = str(raw.get("kind") or "")
        if kind not in remote.CHANGE_KINDS:
            raise ServiceError(400, "changes_invalid", "That change kind is not supported.")
        record_id = _identifier(raw.get("record_id"), "record")
        expected = str(raw.get("expected_revision") or "")
        revision = str(raw.get("revision") or "").strip()
        if not revision or len(revision) > 128:
            raise ServiceError(400, "changes_invalid", "Each change needs a new revision.")
        payload = raw.get("payload")
        now = _now()
        if kind == "project":
            return self._apply_project(connection, bunker_id, record_id, expected, revision, payload, now)
        if kind == "config-document":
            return self._apply_config_document(connection, bunker_id, record_id, expected, revision, payload, now)
        return self._apply_repository_bindings(connection, bunker_id, record_id, expected, revision, payload, now)

    def _current_revision(
        self, connection: sqlite3.Connection, bunker_id: str, kind: str, record_id: str
    ) -> str:
        row = connection.execute(
            "SELECT revision FROM records WHERE bunker_id = ? AND kind = ? AND record_id = ?",
            (bunker_id, kind, record_id),
        ).fetchone()
        return str(row["revision"]) if row else ""

    def _apply_project(
        self, connection, bunker_id, record_id, expected, revision, payload, now
    ) -> str:
        if not isinstance(payload, Mapping):
            raise ServiceError(400, "changes_invalid", "A project change needs a project object.")
        current = self._current_revision(connection, bunker_id, "project", record_id)
        if current != expected:
            raise ServiceError(409, "revision_conflict", "The project changed elsewhere.")
        value = _validated_project(payload, record_id)
        parent = value.get("parent_id") or ""
        if parent and self._current_revision(connection, bunker_id, "project", parent) == "":
            raise ServiceError(409, "revision_conflict", "The parent project is missing.")
        for existing in self._records(connection, bunker_id, "project"):
            if str(existing.get("id")) != record_id and str(existing.get("key")) == value["key"]:
                raise ServiceError(409, "project_key_taken", "That project key already exists.")
        self._upsert(connection, bunker_id, "project", record_id, {**value, "revision": revision, "updated_at": now}, revision, now)
        return revision

    def _apply_config_document(
        self, connection, bunker_id, record_id, expected, revision, payload, now
    ) -> str:
        if not isinstance(payload, Mapping):
            raise ServiceError(400, "changes_invalid", "A configuration change needs a document object.")
        value = _validated_config_document(payload, {})
        if value["scope_type"] == "bunker":
            value["scope_id"] = bunker_id
        elif self._current_revision(connection, bunker_id, "project", value["scope_id"]) == "":
            raise ServiceError(409, "revision_conflict", "The configured project is missing.")
        expected_id = f"{value['scope_type']}:{value['scope_id']}:{value['kind']}"
        if record_id != expected_id:
            raise ServiceError(400, "changes_invalid", "The configuration record identity does not match its scope.")
        current = self._current_revision(connection, bunker_id, "config-document", record_id)
        if current != expected:
            raise ServiceError(409, "revision_conflict", "The configuration changed elsewhere.")
        self._upsert(connection, bunker_id, "config-document", record_id, {**value, "revision": revision, "updated_at": now}, revision, now)
        return revision

    def _apply_repository_bindings(
        self, connection, bunker_id, record_id, expected, revision, payload, now
    ) -> str:
        if not isinstance(payload, list):
            raise ServiceError(400, "changes_invalid", "A repository change needs the complete list.")
        project_id = record_id
        if self._current_revision(connection, bunker_id, "project", project_id) == "":
            raise ServiceError(409, "revision_conflict", "The configured project is missing.")
        current_rows = [
            row for row in self._records(connection, bunker_id, "repository-binding")
            if str(row.get("project_id")) == project_id
        ]
        if remote.repository_list_revision(current_rows) != expected:
            raise ServiceError(409, "revision_conflict", "The project repositories changed elsewhere.")
        rows = [_validated_binding(item, {project_id}) for item in _objects(payload, "repository")]
        if remote.repository_list_revision(rows) != revision:
            raise ServiceError(400, "changes_invalid", "The repository revision does not match its rows.")
        ids = {row["id"] for row in rows}
        if len(ids) != len(rows):
            raise ServiceError(400, "changes_invalid", "A repository row appears more than once.")
        for existing in current_rows:
            if str(existing.get("id")) not in ids:
                connection.execute(
                    "DELETE FROM records WHERE bunker_id = ? AND kind = 'repository-binding' AND record_id = ?",
                    (bunker_id, str(existing.get("id"))),
                )
        for row in rows:
            self._upsert(connection, bunker_id, "repository-binding", row["id"], {**row, "revision": revision, "updated_at": now}, revision, now)
        return revision

    @staticmethod
    def _upsert(connection, bunker_id, kind, record_id, value, revision, now) -> None:
        connection.execute(
            """
            INSERT INTO records(bunker_id, kind, record_id, payload_json, revision, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(bunker_id, kind, record_id) DO UPDATE SET
                payload_json = excluded.payload_json,
                revision = excluded.revision,
                updated_at = excluded.updated_at
            """,
            (bunker_id, kind, record_id, json.dumps(value, sort_keys=True), revision, now),
        )


# -- validation ------------------------------------------------------------------


def _profile(profile: Mapping[str, Any] | None) -> tuple[str, str]:
    if not isinstance(profile, Mapping):
        raise ServiceError(400, "profile_required", "A display name is required.")
    display_name = " ".join(str(profile.get("display_name") or profile.get("name") or "").split())
    email = str(profile.get("email") or "").strip()
    if not display_name or len(display_name) > 120:
        raise ServiceError(400, "profile_required", "A display name is required.")
    if len(email) > 254 or "\n" in email:
        raise ServiceError(400, "profile_required", "The email address is not valid.")
    return display_name, email


def _validated_project(value: Mapping[str, Any], project_id: str) -> dict[str, Any]:
    key = str(value.get("key") or "").strip()
    if not KEY_RE.fullmatch(key):
        raise ServiceError(400, "bunker_invalid", f"Project key {key!r} is not valid.")
    parent = str(value.get("parent_id") or "").strip()
    try:
        position = int(value.get("position") or 0)
    except (TypeError, ValueError):
        position = 0
    return {
        "id": project_id,
        "parent_id": _identifier(parent, "parent project") if parent else "",
        "key": key,
        "name": _name(value.get("name"), "project name"),
        "description": str(value.get("description") or "")[:2000],
        "position": position,
        "created_at": str(value.get("created_at") or ""),
    }


def _validated_binding(value: Mapping[str, Any], project_ids: set[str]) -> dict[str, Any]:
    project_id = _identifier(value.get("project_id"), "repository project")
    if project_id not in project_ids:
        raise ServiceError(400, "bunker_invalid", "A repository names an unknown project.")
    canonical = str(value.get("canonical_remote") or "").strip()
    if not canonical or len(canonical) > 512 or any(character.isspace() for character in canonical):
        raise ServiceError(400, "bunker_invalid", "A repository needs a canonical remote.")
    return {
        "id": _identifier(value.get("id"), "repository"),
        "project_id": project_id,
        "name": _name(value.get("name"), "repository name"),
        "canonical_remote": canonical,
        "default_branch": str(value.get("default_branch") or "main")[:200],
        "created_at": str(value.get("created_at") or ""),
    }


def _validated_config_document(value: Mapping[str, Any], project_ids: set[str]) -> dict[str, Any]:
    scope_type = str(value.get("scope_type") or "").strip()
    if scope_type not in {"bunker", "project"}:
        raise ServiceError(400, "bunker_invalid", "A configuration document needs a bunker or project scope.")
    scope_id = _identifier(value.get("scope_id"), "configuration scope")
    if scope_type == "project" and project_ids and scope_id not in project_ids:
        raise ServiceError(400, "bunker_invalid", "A configuration document names an unknown project.")
    kind = str(value.get("kind") or "").strip()
    if not kind or len(kind) > 64 or ":" in kind:
        raise ServiceError(400, "bunker_invalid", "A configuration document needs a kind.")
    content = value.get("content")
    if not isinstance(content, Mapping):
        raise ServiceError(400, "bunker_invalid", "A configuration document needs object content.")
    if len(json.dumps(content).encode("utf-8")) > 1024 * 1024:
        raise ServiceError(400, "bunker_invalid", "A configuration document is too large.")
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "kind": kind,
        "revision": str(value.get("revision") or "1")[:128],
        "content": dict(content),
    }


def _validated_publication(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ServiceError(400, "bunker_invalid", "The publication must be an object.")
    bunker_value = payload.get("bunker")
    if not isinstance(bunker_value, Mapping):
        raise ServiceError(400, "bunker_invalid", "The publication needs a bunker object.")
    requested = str(bunker_value.get("id") or "").strip()
    bunker = {
        "id": requested if requested and ID_RE.fullmatch(requested) else "",
        "name": _name(bunker_value.get("name"), "Bunker name"),
    }
    projects: list[tuple[str, dict[str, Any]]] = []
    project_ids: set[str] = set()
    keys: set[str] = set()
    for value in _objects(payload.get("projects"), "project"):
        project_id = _identifier(value.get("id"), "project")
        validated = _validated_project(value, project_id)
        if project_id in project_ids or validated["key"] in keys:
            raise ServiceError(400, "bunker_invalid", "Projects must have unique ids and keys.")
        project_ids.add(project_id)
        keys.add(validated["key"])
        projects.append((project_id, {**validated, "revision": str(value.get("revision") or "1"), "updated_at": str(value.get("updated_at") or "")}))
    for _, value in projects:
        if value["parent_id"] and value["parent_id"] not in project_ids:
            raise ServiceError(400, "bunker_invalid", "A project names a missing parent.")
    bindings: list[tuple[str, dict[str, Any]]] = []
    binding_ids: set[str] = set()
    for value in _objects(payload.get("repository_bindings"), "repository"):
        validated = _validated_binding(value, project_ids)
        if validated["id"] in binding_ids:
            raise ServiceError(400, "bunker_invalid", "Repository ids must be unique.")
        binding_ids.add(validated["id"])
        bindings.append((validated["id"], {**validated, "revision": "1", "updated_at": str(value.get("updated_at") or "")}))
    documents: list[tuple[str, dict[str, Any]]] = []
    for value in _objects(payload.get("config_documents"), "configuration document"):
        validated = _validated_config_document(value, project_ids)
        record_id = f"{validated['scope_type']}:{validated['scope_id']}:{validated['kind']}"
        documents.append((record_id, {**validated, "updated_at": str(value.get("updated_at") or "")}))
    tickets: list[tuple[str, dict[str, Any]]] = []
    ticket_ids: set[str] = set()
    for value in _objects(payload.get("tickets"), "ticket"):
        ticket_id = _identifier(value.get("id"), "ticket")
        project_id = _identifier(value.get("project_id"), "ticket project")
        if project_id not in project_ids:
            raise ServiceError(400, "bunker_invalid", "A ticket names an unknown project.")
        ticket_payload = value.get("payload")
        if not isinstance(ticket_payload, Mapping):
            raise ServiceError(400, "bunker_invalid", "A ticket needs an object payload.")
        ticket_ids.add(ticket_id)
        tickets.append(
            (
                ticket_id,
                {
                    "id": ticket_id,
                    "project_id": project_id,
                    "payload": dict(ticket_payload),
                    "source_kind": str(value.get("source_kind") or "remote")[:64],
                    "source_reference": str(value.get("source_reference") or "")[:512],
                    "revision": "1",
                    "updated_at": str(value.get("updated_at") or ""),
                },
            )
        )
    reports: list[tuple[str, dict[str, Any]]] = []
    for value in _objects(payload.get("reports"), "report"):
        ticket_id = _identifier(value.get("ticket_id"), "report ticket")
        if ticket_id not in ticket_ids:
            raise ServiceError(400, "bunker_invalid", "A report names an unknown ticket.")
        scope = str(value.get("scope") or "").strip()
        if not scope or len(scope) > 300:
            raise ServiceError(400, "bunker_invalid", "A report needs a scope.")
        repository_id = str(value.get("repository_id") or "").strip()
        if repository_id and repository_id not in binding_ids:
            raise ServiceError(400, "bunker_invalid", "A report names an unknown repository.")
        content = value.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > 2 * 1024 * 1024:
            raise ServiceError(400, "bunker_invalid", "A report needs bounded text content.")
        record_id = f"{ticket_id}|{scope}|{repository_id}"
        reports.append(
            (
                record_id,
                {
                    "ticket_id": ticket_id,
                    "scope": scope,
                    "repository_id": repository_id,
                    "content": content,
                    "revision": "1",
                    "updated_at": str(value.get("updated_at") or ""),
                },
            )
        )
    return {
        "bunker": bunker,
        "records": {
            "project": projects,
            "repository-binding": bindings,
            "config-document": documents,
            "ticket": tickets,
            "report": reports,
        },
    }


__all__ = ["ServiceError", "ServiceStore"]
