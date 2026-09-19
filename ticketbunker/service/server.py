"""HTTP transport for the reference TicketBunker service (DESIGN.md §3.20).

The handler is a thin router over ``ServiceStore``: it bounds request bodies,
reads the bearer credential, maps stable ``ServiceError`` codes to HTTP
statuses, and never lets a traceback or token reach a client. TLS is either
terminated by the deployment platform or enabled with ``--certfile``.
"""

from __future__ import annotations

import json
import ssl
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .. import __version__, remote
from .storage import ServiceError, ServiceStore

MAX_BODY_BYTES = 8 * 1024 * 1024
PREFIX = f"/api/{remote.API_VERSION}"
FEATURES = ("setup-token", "invitation-credentials", "invitations", "members", "changes", "snapshot-chunks")


class ServiceServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], store: ServiceStore, *, quiet: bool = False):
        super().__init__(address, ServiceHandler)
        self.store = store
        self.quiet = quiet
        self.tls = False

    @property
    def origin(self) -> str:
        host, port = self.server_address[:2]
        if ":" in str(host):
            host = f"[{host}]"
        return f"{'https' if self.tls else 'http'}://{host}:{port}"


class ServiceHandler(BaseHTTPRequestHandler):
    server_version = f"TicketBunkerService/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    server: ServiceServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if self.server.quiet:
            return
        # Request lines carry only paths and Bunker identifiers; tokens travel in
        # bodies and headers, which are never logged.
        sys.stderr.write(f"ticketbunker-service: {format % args}\n")

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        try:
            payload, status = self._route(method)
        except ServiceError as exc:
            self._send(exc.status, exc.as_dict())
            return
        except Exception as exc:  # the service is an error boundary
            diagnostic = uuid.uuid4().hex[:12]
            sys.stderr.write(
                f"ticketbunker-service: internal error {diagnostic}: {type(exc).__name__}\n"
            )
            self._send(
                500,
                {
                    "ok": False,
                    "code": "internal_error",
                    "message": f"The service could not complete that request. Diagnostic {diagnostic}.",
                },
            )
            return
        self._send(status, payload)

    def _body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise ServiceError(411, "length_required", "Send a Content-Length header.")
        raw_length = self.headers.get("Content-Length")
        if not raw_length:
            return {}
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ServiceError(400, "request_invalid", "The request length is not valid.") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ServiceError(413, "request_too_large", "The request exceeds the service limit.")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ServiceError(400, "request_invalid", "The request body is not valid JSON.") from exc
        if not isinstance(value, dict):
            raise ServiceError(400, "request_invalid", "The request body must be an object.")
        return value

    def _user(self, *, required: bool = True) -> dict[str, Any] | None:
        header = str(self.headers.get("Authorization") or "")
        user = None
        if header.startswith("Bearer "):
            user = self.server.store.authenticate(header[len("Bearer "):].strip())
        if required and user is None:
            raise ServiceError(401, "authentication_required", "Sign in to this TicketBunker server.")
        return user

    def _route(self, method: str) -> tuple[dict[str, Any], int]:
        parsed = urlsplit(self.path)
        if not parsed.path.startswith(PREFIX):
            raise ServiceError(404, "not_found", "That service endpoint does not exist.")
        parts = [unquote(piece) for piece in parsed.path[len(PREFIX):].strip("/").split("/") if piece]
        query = parse_qs(parsed.query)
        store = self.server.store
        if parts == ["capabilities"] and method == "GET":
            return (
                {
                    "ok": True,
                    "protocol": remote.API_VERSION,
                    "service": {"name": "TicketBunker reference service", "version": __version__},
                    "features": list(FEATURES),
                },
                200,
            )
        if parts == ["setup"] and method == "POST":
            body = self._body()
            result = store.complete_setup(str(body.get("setup_token") or ""), body.get("profile") or {})
            return {"ok": True, **result}, 200
        if parts == ["invitations", "inspect"] and method == "POST":
            body = self._body()
            result = store.inspect_invitation(str(body.get("bunker_id") or ""), str(body.get("invite") or ""))
            return {"ok": True, **result}, 200
        if parts == ["invitations", "redeem"] and method == "POST":
            body = self._body()
            user = self._user(required=False)
            result = store.redeem_invitation(
                str(body.get("bunker_id") or ""),
                str(body.get("invite") or ""),
                user_id=str(user["id"]) if user else "",
                profile=None if user else body.get("profile"),
            )
            if body.get("snapshot_transport") == "chunks":
                # Enrollment commits before download. Never send the large
                # snapshot in the credential response.
                snapshot = result.pop("snapshot")
                result["snapshot_bunker_id"] = snapshot["bunker"]["id"]
            return {"ok": True, **result}, 200
        if parts == ["bunkers"] and method == "POST":
            user = self._user()
            body = self._body()
            snapshot = store.publish_bunker(str(user["id"]), body)  # type: ignore[index]
            if body.get("snapshot_transport") == "chunks":
                return {"ok": True, "snapshot_bunker_id": snapshot["bunker"]["id"]}, 201
            return {"ok": True, "snapshot": snapshot}, 201
        if len(parts) >= 3 and parts[0] == "bunkers":
            bunker_id = parts[1]
            rest = parts[2:]
            user = self._user()
            user_id = str(user["id"])  # type: ignore[index]
            if rest == ["snapshot-transfers"] and method == "POST":
                from . import snapshots
                self._body()
                return {"ok": True, "transfer": snapshots.create(store, bunker_id, user_id)}, 201
            if len(rest) == 2 and rest[0] == "snapshot-transfers" and method == "DELETE":
                from . import snapshots
                snapshots.discard(store, bunker_id, user_id, rest[1])
                return {"ok": True}, 200
            if len(rest) == 3 and rest[0] == "snapshot-transfers" and method == "GET":
                from . import snapshots
                try:
                    part = int(rest[2])
                except ValueError as exc:
                    raise ServiceError(400, "request_invalid", "Choose a snapshot part.") from exc
                return {"ok": True, **snapshots.read(store, bunker_id, user_id, rest[1], part)}, 200
            if rest == ["snapshot"] and method == "GET":
                after = str((query.get("after") or [""])[0])
                return {"ok": True, **store.snapshot(bunker_id, user_id, after=after)}, 200
            if rest == ["changes"] and method == "POST":
                body = self._body()
                changes = body.get("changes")
                if not isinstance(changes, list):
                    raise ServiceError(400, "changes_invalid", "Send a list of changes.")
                result = store.apply_changes(bunker_id, user_id, str(body.get("cursor") or ""), changes)
                return {"ok": True, **result}, 200
            if rest == ["members"] and method == "GET":
                return {"ok": True, "members": store.members(bunker_id, user_id)}, 200
            if rest == ["invitations"] and method == "GET":
                return {"ok": True, "invitations": store.list_invitations(bunker_id, user_id)}, 200
            if rest == ["invitations"] and method == "POST":
                body = self._body()
                invitation = store.create_invitation(
                    bunker_id,
                    user_id,
                    role=str(body.get("role") or remote.ROLE_MEMBER),
                    expires_in=body.get("expires_in") or remote.DEFAULT_INVITATION_SECONDS,
                )
                return {"ok": True, "invitation": invitation}, 201
            if len(rest) == 2 and rest[0] == "invitations" and method == "DELETE":
                store.revoke_invitation(bunker_id, user_id, rest[1])
                return {"ok": True}, 200
        raise ServiceError(404, "not_found", "That service endpoint does not exist.")

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)


def serve(
    store: ServiceStore,
    *,
    host: str = "127.0.0.1",
    port: int = 8480,
    certfile: str | None = None,
    keyfile: str | None = None,
    quiet: bool = False,
) -> ServiceServer:
    """Bind the service. The caller runs ``serve_forever`` and closes it."""
    server = ServiceServer((host, port), store, quiet=quiet)
    if certfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server.tls = True
    return server


__all__ = ["FEATURES", "MAX_BODY_BYTES", "ServiceServer", "serve"]
