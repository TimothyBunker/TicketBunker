"""Remote TicketBunker protocol contracts.

TicketBunker does not own a hosted service.  This module defines the boundary
that any company-hosted, self-hosted, or future managed service must implement.
The desktop application connects to a service, never to its database.
"""

from __future__ import annotations

import json
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
    """Authenticated enrollment result returned by a remote adapter."""

    snapshot: RemoteSnapshot
    credential_reference: str


class RemoteAdapter(Protocol):
    """Host-neutral service boundary used by join and synchronization flows."""

    def inspect(self, invitation: JoinInvitation) -> Mapping[str, Any]: ...

    def enroll(self, invitation: JoinInvitation) -> Enrollment: ...

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
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
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
            raise RemoteContractError(detail) from exc
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

    def enroll(self, invitation: JoinInvitation) -> Enrollment:
        if invitation.server != self.server:
            raise RemoteContractError("The invitation belongs to a different server.")
        if not self.bearer or not self.credential_reference:
            raise RemoteContractError(
                "Sign in to this TicketBunker server before joining the Bunker."
            )
        value = self._request(
            "POST",
            "/invitations/redeem",
            {"bunker_id": invitation.bunker_id, "invite": invitation.token},
        )
        snapshot_value = value.get("snapshot")
        if not isinstance(snapshot_value, dict):
            raise RemoteContractError(
                "The TicketBunker server did not complete authenticated enrollment."
            )
        return Enrollment(_snapshot(snapshot_value), self.credential_reference)

    def pull(self, bunker_id: str, cursor: str) -> RemoteSnapshot:
        query = urllib.parse.urlencode({"after": cursor}) if cursor else ""
        suffix = f"?{query}" if query else ""
        value = self._request(
            "GET", f"/bunkers/{urllib.parse.quote(bunker_id, safe='')}/snapshot{suffix}"
        )
        return _snapshot(value)

    def push(
        self, bunker_id: str, cursor: str, changes: tuple[Mapping[str, Any], ...]
    ) -> Mapping[str, Any]:
        return self._request(
            "POST",
            f"/bunkers/{urllib.parse.quote(bunker_id, safe='')}/changes",
            {"cursor": cursor, "changes": list(changes)},
        )


def _objects(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RemoteContractError(f"The remote snapshot has invalid {name}.")
    return tuple(value)


def _snapshot(value: Mapping[str, Any]) -> RemoteSnapshot:
    bunker = value.get("bunker")
    if not isinstance(bunker, dict) or not str(bunker.get("id") or ""):
        raise RemoteContractError("The remote snapshot does not identify its Bunker.")
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
    "Enrollment",
    "HTTPRemoteAdapter",
    "JoinInvitation",
    "RemoteAdapter",
    "RemoteContractError",
    "RemoteSnapshot",
    "normalize_server_url",
]
