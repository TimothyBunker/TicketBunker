"""Remote TicketBunker protocol contracts.

TicketBunker does not own a hosted service.  This module defines the boundary
that any company-hosted, self-hosted, or future managed service must implement.
The desktop application connects to a service, never to its database.
"""

from __future__ import annotations

import json
import base64
import hashlib
import tempfile
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


API_VERSION = "v1"
INVITE_SCHEME = "ticketbunker"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
INVITE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# Membership vocabulary shared by every compatible service (DESIGN.md §6).
ROLE_OWNER = "owner"
ROLE_MEMBER = "member"
ROLES = (ROLE_OWNER, ROLE_MEMBER)
ROLE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    ROLE_OWNER: (
        "tickets:read",
        "tickets:write",
        "config:read",
        "config:write",
        "bunker:share",
        "members:invite",
        "members:manage",
    ),
    ROLE_MEMBER: ("tickets:read", "tickets:write", "config:read"),
}
CHANGE_KINDS = ("project", "project-delete", "repository-bindings", "config-document", "ticket", "report")
CHANGE_CAPABILITIES = {
    "project": "config:write",
    "project-delete": "config:write",
    "repository-bindings": "config:write",
    "config-document": "config:write",
    "ticket": "tickets:write",
    "report": "tickets:write",
}
MAX_CHANGE_BATCH = 200
DEFAULT_INVITATION_SECONDS = 7 * 24 * 60 * 60
MAX_INVITATION_SECONDS = 90 * 24 * 60 * 60


def normalize_role(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value not in ROLES:
        raise RemoteContractError("Choose the owner or member role.")
    return value


def capabilities_for(role: Any) -> tuple[str, ...]:
    return ROLE_CAPABILITIES.get(str(role or "").strip().lower(), ())


def repository_list_revision(rows: Any) -> str:
    """Content-derived revision of one Project's shared repository allowlist.

    Clients and services compute the same digest from the same shared fields,
    so a whole-list replacement can be revision-checked without either side
    storing a separate counter. Device paths are never part of it.
    """
    import hashlib

    shared = sorted(
        (
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or ""),
                "canonical_remote": str(row.get("canonical_remote") or ""),
                "default_branch": str(row.get("default_branch") or ""),
            }
            for row in rows
        ),
        key=lambda item: (item["name"].casefold(), item["canonical_remote"], item["id"]),
    )
    raw = json.dumps(shared, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "repositories:" + hashlib.sha256(raw).hexdigest()[:24]


def project_revision(value: Mapping[str, Any]) -> str:
    import hashlib
    content = {key: str(value.get(key) or "") for key in ("id", "parent_id", "key", "name", "description")}
    content["position"] = int(value.get("position") or 0)
    return "project:" + hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:24]


def change_record(
    *,
    key: str,
    kind: str,
    record_id: str,
    expected_revision: str,
    revision: str,
    payload: Mapping[str, Any] | list[Any],
) -> dict[str, Any]:
    """One typed, idempotent, revision-checked change for the service."""
    if kind not in CHANGE_KINDS:
        raise RemoteContractError("That change kind is not part of the service contract.")
    return {
        "key": str(key),
        "kind": kind,
        "record_id": str(record_id),
        "expected_revision": str(expected_revision or ""),
        "revision": str(revision),
        "payload": payload,
    }


# Stable service failure codes render as TicketBunker's own next actions. Raw
# server text never reaches a user.
_CODE_MESSAGES = {
    "project_not_empty": "This Project still has tickets, subprojects, or report dependencies. Only empty Projects can be deleted; no data was changed.",
    "setup_token_invalid": "That setup token is not valid. Copy the token printed by tb serve, or reset it with tb serve --reset-setup-token.",
    "setup_complete": "This server already has an owner. Ask that owner for an invitation instead of using a setup token.",
    "profile_required": "Enter your name and email in Settings before joining or sharing a Bunker.",
    "invitation_invalid": "That invitation is not valid for this server. Ask the Bunker owner for a new one.",
    "invitation_expired": "That invitation expired. Ask the Bunker owner for a new one.",
    "invitation_revoked": "That invitation was revoked. Ask the Bunker owner for a new one.",
    "invitation_used": "That invitation was already used. Ask the Bunker owner for a new one.",
    "already_member": "You already belong to this Bunker on this server.",
    "capability_required": "Only the Bunker owner can do that on this server.",
    "service_admin_required": "Only a service administrator can create a shared Bunker. Ask the server operator to create it or invite you to an existing Bunker.",
    "revision_conflict": "The shared Bunker changed elsewhere. Reload it and retry.",
    "project_key_taken": "That project key already exists in the shared Bunker.",
    "bunker_invalid": "The Bunker could not be published because its content did not validate.",
    "ticket_conflict": "Those tickets changed on the shared Bunker. Sync again.",
    "report_conflict": "That report changed on the shared Bunker. Sync, then synthesize again.",
    "report_invalid": "The report did not pass the service publication gate. Synthesize its branch again; Combined must come from accepted branch reports.",
    "snapshot_expired": "The snapshot download expired. Sync again; your local data has not been replaced.",
    "snapshot_busy": "Previous snapshot downloads are still open. Wait a few minutes and sync again.",
    "idempotency_conflict": "A saved request key was reused for different content. Sync the Bunker and submit a new action.",
    "changes_invalid": "The service did not understand this change. It may be running an older TicketBunker: restart tb serve, then retry.",
}


class RemoteContractError(ValueError):
    """A safe, user-facing remote-contract rejection."""


def normalize_server_url(raw: str, *, allow_local_http: bool = True) -> str:
    """Return a safe API origin without paths, credentials, query, or fragment."""
    value = str(raw or "").strip()
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise RemoteContractError("Enter a valid TicketBunker server address.") from exc
    if not parsed.hostname or parsed.username or parsed.password:
        raise RemoteContractError(
            "Enter only the TicketBunker server address, without credentials."
        )
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (
        allow_local_http and parsed.scheme == "http" and local
    ):
        raise RemoteContractError(
            "TicketBunker servers must use HTTPS. Localhost may use HTTP for development."
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RemoteContractError(
            "Enter the server origin only, such as https://tickets.example.com."
        )
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise RemoteContractError("Enter a valid TicketBunker server port.") from exc
    port = f":{parsed_port}" if parsed_port else ""
    return f"{parsed.scheme}://{host}{port}"


@dataclass(frozen=True)
class JoinInvitation:
    """A short-lived capability used only to begin authenticated enrollment."""

    server: str
    bunker_id: str
    token: str

    @classmethod
    def parse(cls, raw: str) -> "JoinInvitation":
        value = str(raw or "").strip()
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError as exc:
            raise RemoteContractError("That TicketBunker invitation is not valid.") from exc
        if parsed.scheme != INVITE_SCHEME or parsed.netloc != "join":
            raise RemoteContractError(
                "Use a TicketBunker invitation beginning with ticketbunker://join."
            )
        if parsed.path not in {"", "/"} or parsed.fragment:
            raise RemoteContractError("That TicketBunker invitation is not valid.")
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        if set(query) != {"server", "bunker", "invite"} or any(
            len(values) != 1 for values in query.values()
        ):
            raise RemoteContractError("That TicketBunker invitation is not valid.")
        server = normalize_server_url((query.get("server") or [""])[0])
        bunker_id = str((query.get("bunker") or [""])[0]).strip()
        token = str((query.get("invite") or [""])[0]).strip()
        if not INVITE_ID_RE.fullmatch(bunker_id):
            raise RemoteContractError("The invitation does not name a valid Bunker.")
        if not token or len(token) > 2048:
            raise RemoteContractError("The invitation token is missing or invalid.")
        return cls(server=server, bunker_id=bunker_id, token=token)

    def public_dict(self) -> dict[str, str]:
        """Return display-safe data. The bearer token is deliberately omitted."""
        return {"server": self.server, "bunker_id": self.bunker_id}


@dataclass(frozen=True)
class RemoteSnapshot:
    """The authenticated state required before a local replica may be created."""

    bunker: Mapping[str, Any]
    projects: tuple[Mapping[str, Any], ...]
    repository_bindings: tuple[Mapping[str, Any], ...]
    config_documents: tuple[Mapping[str, Any], ...]
    tickets: tuple[Mapping[str, Any], ...] = ()
    reports: tuple[Mapping[str, Any], ...] = ()
    members: tuple[Mapping[str, Any], ...] = ()
    current_member_id: str = ""
    capabilities: tuple[str, ...] = ()
    revision: str = ""
    cursor: str = ""


@dataclass(frozen=True)
class Enrollment:
    """Authenticated enrollment result returned by a remote adapter.

    ``issued_credential`` is set only when the service creates the member's
    credential at redemption (the reference service's invitation-credential
    feature). The application stores it in the machine-local credential store
    and replaces it with a reference before any Bunker database sees it.
    """

    snapshot: RemoteSnapshot
    credential_reference: str
    issued_credential: str = ""


class RemoteAdapter(Protocol):
    """Host-neutral service boundary used by join and synchronization flows."""

    def inspect(self, invitation: JoinInvitation) -> Mapping[str, Any]: ...

    def enroll(
        self, invitation: JoinInvitation, profile: Mapping[str, str] | None = None
    ) -> Enrollment: ...

    def pull(self, bunker_id: str, cursor: str) -> RemoteSnapshot: ...

    def push(
        self, bunker_id: str, cursor: str, changes: tuple[Mapping[str, Any], ...]
    ) -> Mapping[str, Any]: ...


class HTTPRemoteAdapter:
    """Minimal stdlib transport for compatible TicketBunker services.

    Credential creation and storage are intentionally outside this class. A
    trusted native authentication broker supplies an ephemeral bearer value and
    the OS-vault reference from which it came. The server returns only the
    authorized snapshot; it cannot manufacture a reference into the device's
    credential vault.
    """

    def __init__(
        self,
        server: str,
        *,
        bearer: str = "",
        credential_reference: str = "",
        timeout: int = 20,
    ):
        self.server = normalize_server_url(server)
        self.bearer = bearer
        self.credential_reference = credential_reference
        self.timeout = timeout

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        request = urllib.request.Request(
            f"{self.server}/api/{API_VERSION}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            from . import platform_services

            with urllib.request.urlopen(
                request, timeout=self.timeout, context=platform_services.https_context()
            ) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            code = ""
            try:
                body = json.loads(exc.read(64 * 1024).decode("utf-8"))
                if isinstance(body, dict) and isinstance(body.get("code"), str):
                    code = body["code"]
            except (ValueError, OSError, AttributeError):
                code = ""
            if code in _CODE_MESSAGES:
                detail = _CODE_MESSAGES[code]
            elif exc.code == 401:
                detail = "Sign in to this TicketBunker server, then retry."
            elif exc.code == 403:
                detail = "This server did not authorize that Bunker action."
            elif exc.code == 404:
                detail = "That Bunker or invitation is no longer available."
            elif exc.code == 409:
                detail = "The remote Bunker changed. Synchronize and retry."
            elif exc.code == 429:
                detail = "The TicketBunker server is busy. Wait briefly, then retry."
            else:
                detail = "The TicketBunker server could not complete that action."
            error = RemoteContractError(detail)
            error.code = code or f"http_{exc.code}"  # type: ignore[attr-defined]
            raise error from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RemoteContractError(
                f"TicketBunker could not reach {self.server}. Check the address and network, then retry."
            ) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RemoteContractError("The TicketBunker server response was too large.")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RemoteContractError(
                "The TicketBunker server returned an unreadable response."
            ) from exc
        if not isinstance(value, dict):
            raise RemoteContractError(
                "The TicketBunker server returned an invalid response."
            )
        return value

    def capabilities(self) -> Mapping[str, Any]:
        return self._request("GET", "/capabilities")

    def inspect(self, invitation: JoinInvitation) -> Mapping[str, Any]:
        if invitation.server != self.server:
            raise RemoteContractError("The invitation belongs to a different server.")
        return self._request(
            "POST",
            "/invitations/inspect",
            {"bunker_id": invitation.bunker_id, "invite": invitation.token},
        )

    def enroll(
        self, invitation: JoinInvitation, profile: Mapping[str, str] | None = None
    ) -> Enrollment:
        if invitation.server != self.server:
            raise RemoteContractError("The invitation belongs to a different server.")
        body: dict[str, Any] = {
            "bunker_id": invitation.bunker_id,
            "invite": invitation.token,
            "snapshot_transport": "chunks",
        }
        if self.bearer:
            if not self.credential_reference:
                raise RemoteContractError(
                    "Sign in to this TicketBunker server before joining the Bunker."
                )
        else:
            if not profile or not str(profile.get("display_name") or "").strip():
                raise RemoteContractError(
                    "Enter your name and email in Settings before joining a Bunker."
                )
            body["profile"] = _profile_payload(profile)
        value = self._request("POST", "/invitations/redeem", body)
        snapshot_value = value.get("snapshot")
        if not isinstance(snapshot_value, dict) and not value.get("snapshot_bunker_id"):
            raise RemoteContractError(
                "The TicketBunker server did not complete authenticated enrollment."
            )
        issued = str(value.get("credential") or "") if not self.bearer else ""
        if not self.bearer and not issued:
            raise RemoteContractError(
                "This server requires sign-in before joining. Sign in to it, then retry."
            )
        if value.get("snapshot_bunker_id"):
            if issued:
                downloader = HTTPRemoteAdapter(self.server, bearer=issued)
            else:
                downloader = self
            try:
                snapshot_value = downloader._pull_chunks(str(value["snapshot_bunker_id"]))
            except RemoteContractError as exc:
                # The application persists this credential, not the transport.
                # Never expose it in the error text or command response.
                exc.issued_credential = issued
                raise
        return Enrollment(
            _snapshot(snapshot_value), self.credential_reference, issued_credential=issued
        )

    def setup(self, setup_token: str, profile: Mapping[str, str]) -> dict[str, Any]:
        """Claim a fresh reference service with its one-time owner setup token."""
        token = str(setup_token or "").strip()
        if not token:
            raise RemoteContractError(
                "Paste the setup token printed by tb serve to claim this server."
            )
        value = self._request(
            "POST", "/setup", {"setup_token": token, "profile": _profile_payload(profile)}
        )
        credential = str(value.get("credential") or "")
        user = value.get("user")
        if not credential or not isinstance(user, dict):
            raise RemoteContractError("The TicketBunker server did not complete owner setup.")
        return {"credential": credential, "user": user}

    def publish(self, payload: Mapping[str, Any]) -> RemoteSnapshot:
        """Publish a local Bunker; the authenticated user becomes its owner."""
        self._require_signed_in("share a Bunker")
        value = self._request("POST", "/bunkers", {**payload, "snapshot_transport": "chunks"})
        snapshot_value = value.get("snapshot")
        if value.get("snapshot_bunker_id"):
            snapshot_value = self._pull_chunks(str(value["snapshot_bunker_id"]))
        if not isinstance(snapshot_value, dict):
            raise RemoteContractError("The TicketBunker server did not publish the Bunker.")
        return _snapshot(snapshot_value)

    def create_invitation(
        self, bunker_id: str, *, role: str = ROLE_MEMBER, expires_in: int = DEFAULT_INVITATION_SECONDS
    ) -> dict[str, Any]:
        self._require_signed_in("invite members")
        value = self._request(
            "POST",
            f"/bunkers/{urllib.parse.quote(bunker_id, safe='')}/invitations",
            {"role": normalize_role(role), "expires_in": int(expires_in)},
        )
        invitation = value.get("invitation")
        if not isinstance(invitation, dict) or not str(invitation.get("token") or ""):
            raise RemoteContractError("The TicketBunker server did not create an invitation.")
        return dict(invitation)

    def list_invitations(self, bunker_id: str) -> tuple[Mapping[str, Any], ...]:
        self._require_signed_in("view invitations")
        value = self._request(
            "GET", f"/bunkers/{urllib.parse.quote(bunker_id, safe='')}/invitations"
        )
        return _objects(value.get("invitations", []), "invitations")

    def revoke_invitation(self, bunker_id: str, invitation_id: str) -> None:
        self._require_signed_in("revoke invitations")
        if not INVITE_ID_RE.fullmatch(str(invitation_id or "")):
            raise RemoteContractError("Choose an invitation to revoke.")
        self._request(
            "DELETE",
            f"/bunkers/{urllib.parse.quote(bunker_id, safe='')}/invitations/"
            f"{urllib.parse.quote(invitation_id, safe='')}",
        )

    def members(self, bunker_id: str) -> tuple[Mapping[str, Any], ...]:
        self._require_signed_in("view members")
        value = self._request(
            "GET", f"/bunkers/{urllib.parse.quote(bunker_id, safe='')}/members"
        )
        return _objects(value.get("members", []), "members")

    def _require_signed_in(self, action: str) -> None:
        if not self.bearer:
            raise RemoteContractError(
                f"Sign in to {self.server} before you {action}."
            )

    def pull(self, bunker_id: str, cursor: str) -> RemoteSnapshot:
        try:
            return _snapshot(self._pull_chunks(bunker_id))
        except RemoteContractError as exc:
            if getattr(exc, "code", "") not in {"not_found", "http_404"}:
                raise
        # Compatibility with older services; no unsafe fallback after a
        # partial or invalid chunk transfer.
        query = urllib.parse.urlencode({"after": cursor}) if cursor else ""
        suffix = f"?{query}" if query else ""
        value = self._request(
            "GET", f"/bunkers/{urllib.parse.quote(bunker_id, safe='')}/snapshot{suffix}"
        )
        return _snapshot(value)

    def _pull_chunks(self, bunker_id: str) -> Mapping[str, Any]:
        path = f"/bunkers/{urllib.parse.quote(bunker_id, safe='')}/snapshot-transfers"
        opened = self._request("POST", path, {})
        transfer = opened.get("transfer")
        if (not isinstance(transfer, dict) or transfer.get("bunker_id") != bunker_id
                or not re.fullmatch(r"[0-9a-f]{32}", str(transfer.get("id") or ""))
                or type(transfer.get("parts")) is not int or transfer["parts"] < 1
                or not re.fullmatch(r"[0-9a-f]{64}", str(transfer.get("sha256") or ""))):
            raise RemoteContractError("The service returned an invalid snapshot manifest.")
        identity = transfer["id"]
        digest = hashlib.sha256()
        try:
            with tempfile.TemporaryFile() as stream:
                for part in range(transfer["parts"]):
                    value = self._request("GET", f"{path}/{identity}/{part}")
                    if value.get("id") != identity or value.get("part") != part:
                        raise RemoteContractError("Snapshot parts were mixed. Sync again; local data was not changed.")
                    try:
                        raw = base64.b64decode(value["content"], validate=True)
                    except (KeyError, ValueError, TypeError) as exc:
                        raise RemoteContractError("A snapshot part was unreadable. Sync again.") from exc
                    digest.update(raw)
                    stream.write(raw)
                if digest.hexdigest() != transfer["sha256"]:
                    raise RemoteContractError("The snapshot checksum did not match. Local data was not changed.")
                stream.seek(0)
                try:
                    result = json.load(stream)
                except (ValueError, UnicodeError) as exc:
                    raise RemoteContractError("The snapshot could not be decoded. Local data was not changed.") from exc
                if not isinstance(result, dict):
                    raise RemoteContractError("The snapshot is not an object.")
                return result
        finally:
            try:
                self._request("DELETE", f"{path}/{identity}")
            except RemoteContractError:
                pass  # Expiry cleans up after disconnection; preserve the original outcome.

    def push(
        self, bunker_id: str, cursor: str, changes: tuple[Mapping[str, Any], ...]
    ) -> Mapping[str, Any]:
        return self._request(
            "POST",
            f"/bunkers/{urllib.parse.quote(bunker_id, safe='')}/changes",
            {"cursor": cursor, "changes": list(changes)},
        )


def _profile_payload(profile: Mapping[str, str]) -> dict[str, str]:
    display_name = str(profile.get("display_name") or profile.get("name") or "").strip()
    email = str(profile.get("email") or "").strip()
    if not display_name:
        raise RemoteContractError(
            "Enter your name and email in Settings before using a TicketBunker server."
        )
    return {"display_name": display_name[:120], "email": email[:254]}


def _objects(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RemoteContractError(f"The remote snapshot has invalid {name}.")
    return tuple(value)


def _snapshot(value: Mapping[str, Any]) -> RemoteSnapshot:
    bunker = value.get("bunker")
    if not isinstance(bunker, dict) or not str(bunker.get("id") or ""):
        raise RemoteContractError("The remote snapshot does not identify its Bunker.")
    required = ("projects", "repository_bindings", "config_documents", "tickets", "reports", "members")
    if any(field not in value for field in required):
        raise RemoteContractError("The server returned an incomplete Bunker snapshot. Local data was not changed. Retry synchronization when the server is ready.")
    if any(not isinstance(value.get(field), str) or not value[field].strip() for field in ("revision", "cursor")):
        raise RemoteContractError("The server snapshot is missing its synchronization revision or cursor. Local data was not changed. Retry synchronization when the server is ready.")
    capabilities = value.get("capabilities", [])
    if not isinstance(capabilities, list) or any(
        not isinstance(item, str) or not item for item in capabilities
    ):
        raise RemoteContractError("The remote snapshot has invalid capabilities.")
    members = _objects(value.get("members", []), "members")
    current_member_id = str(value.get("current_member_id") or "")
    member_ids = {str(item.get("id") or "") for item in members}
    if not current_member_id or current_member_id not in member_ids:
        raise RemoteContractError(
            "The remote snapshot does not identify the enrolled member."
        )
    return RemoteSnapshot(
        bunker=bunker,
        projects=_objects(value.get("projects", []), "projects"),
        repository_bindings=_objects(
            value.get("repository_bindings", []), "repository bindings"
        ),
        config_documents=_objects(
            value.get("config_documents", []), "configuration"
        ),
        tickets=_objects(value.get("tickets", []), "tickets"),
        reports=_objects(value.get("reports", []), "reports"),
        members=members,
        current_member_id=current_member_id,
        capabilities=tuple(capabilities),
        revision=str(value.get("revision") or ""),
        cursor=str(value.get("cursor") or ""),
    )


__all__ = [
    "API_VERSION",
    "CHANGE_CAPABILITIES",
    "CHANGE_KINDS",
    "MAX_CHANGE_BATCH",
    "DEFAULT_INVITATION_SECONDS",
    "ROLE_CAPABILITIES",
    "ROLE_MEMBER",
    "ROLE_OWNER",
    "ROLES",
    "capabilities_for",
    "change_record",
    "normalize_role",
    "repository_list_revision",
    "Enrollment",
    "HTTPRemoteAdapter",
    "JoinInvitation",
    "RemoteAdapter",
    "RemoteContractError",
    "RemoteSnapshot",
    "normalize_server_url",
]
