"""Browser fallback for the same bundled standalone application.

The native app uses private process pipes. An explicit browser fallback serves
the identical immutable frontend on loopback and exposes the same bounded
desktop action allowlist behind a per-launch CSRF token. This server is a local
transport only; it contains no domain behavior.
"""

from __future__ import annotations

import json
import mimetypes
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from . import __version__, app_worker


ASSET_ROOT = Path(__file__).resolve().parent / "app_assets"
MAX_BODY_BYTES = app_worker.MAX_REQUEST_BYTES
SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], root: Path, *, launch_id: str = ""):
        self.root = Path(root).resolve()
        self.launch_id = launch_id
        self.token = secrets.token_urlsafe(32)
        self._presence_lock = threading.Lock()
        self._sessions: dict[str, float] = {}
        self._started_at = time.monotonic()
        self._saw_session = False
        self._empty_since: float | None = None
        self._active_requests = 0
        self._monitor_stop = threading.Event()
        super().__init__(address, AppHandler)

    def process_request_thread(self, request: object, client_address: object) -> None:
        with self._presence_lock:
            self._active_requests += 1
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._presence_lock:
                self._active_requests -= 1

    def note_session(self, session: str, *, closed: bool = False) -> None:
        if not SESSION_RE.fullmatch(session):
            return
        with self._presence_lock:
            if closed:
                self._sessions.pop(session, None)
            else:
                self._sessions[session] = time.monotonic()
                self._saw_session = True
            if self._sessions:
                self._empty_since = None
            elif self._saw_session and self._empty_since is None:
                self._empty_since = time.monotonic()

    def _monitor_idle(self) -> None:
        while not self._monitor_stop.wait(1):
            now = time.monotonic()
            with self._presence_lock:
                self._sessions = {
                    key: seen for key, seen in self._sessions.items() if now - seen < 30
                }
                if self._saw_session and not self._sessions and self._empty_since is None:
                    self._empty_since = now
                stop = (
                    self._saw_session
                    and not self._sessions
                    and self._empty_since is not None
                    and now - self._empty_since >= 3
                    and self._active_requests == 0
                ) or (
                    not self._saw_session
                    and now - self._started_at >= 60
                    and self._active_requests == 0
                )
            if stop:
                self.shutdown()
                return

    def serve_until_closed(self) -> None:
        monitor = threading.Thread(
            target=self._monitor_idle, name="tb-app-browser-idle", daemon=True
        )
        monitor.start()
        try:
            self.serve_forever()
        finally:
            self._monitor_stop.set()
            monitor.join(timeout=2)

    def handle_error(self, _request: object, _client_address: object) -> None:
        return


class AppHandler(BaseHTTPRequestHandler):
    server: AppServer

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'",
        )
        self.end_headers()

    def _send(self, status: int, value: bytes, content_type: str) -> None:
        self._headers(status, content_type, len(value))
        try:
            self.wfile.write(value)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, value: Any) -> None:
        self._send(
            status,
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _authorized(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-TicketBunker-Token", ""), self.server.token
        )

    def _payload(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            return None
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _asset(self, raw_path: str) -> Path | None:
        relative = unquote(raw_path).lstrip("/")
        candidate = (ASSET_ROOT / relative).resolve()
        try:
            candidate.relative_to(ASSET_ROOT.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/_viewer/health":
            self._json(
                200,
                {
                    "ok": True,
                    "kind": "standalone-app",
                    "root": str(self.server.root),
                    "version": __version__,
                    "launch": self.server.launch_id,
                },
            )
            return
        if path == "/api/bootstrap":
            app_worker.dispatch_request(
                {
                    "id": "register-launch",
                    "action": "application.project.add",
                    "payload": {"path": str(self.server.root)},
                    "project_root": str(self.server.root),
                },
                default_root=self.server.root,
            )
            result = app_worker.dispatch_request(
                {
                    "id": "bootstrap",
                    "action": "application.state",
                    "payload": {},
                    "project_root": str(self.server.root),
                },
                default_root=self.server.root,
            )
            result["launch"] = {"repository": str(self.server.root), "route": "/"}
            self._json(200 if result.get("ok") else 400, result)
            return
        asset = self._asset(path)
        if asset is not None:
            content_type = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            self._send(200, asset.read_bytes(), content_type)
            return
        index = ASSET_ROOT / "index.html"
        if not index.is_file():
            self._send(
                503,
                b"TicketBunker's application assets are not installed.",
                "text/plain; charset=utf-8",
            )
            return
        document = index.read_text(encoding="utf-8").replace(
            "__TB_TRANSPORT_TOKEN__", self.server.token
        )
        self._send(200, document.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if not self._authorized():
            self._json(403, {"ok": False, "message": "Reload the TicketBunker app and retry."})
            return
        payload = self._payload()
        if payload is None:
            self._json(400, {"ok": False, "message": "The local application request is invalid."})
            return
        if path == "/api/session":
            self.server.note_session(
                str(payload.get("session") or ""), closed=bool(payload.get("closed"))
            )
            self._json(200, {"ok": True})
            return
        if path != "/api/action":
            self._json(404, {"ok": False, "message": "That local route does not exist."})
            return
        request = {
            "id": secrets.token_hex(8),
            "action": payload.get("action"),
            "payload": payload.get("payload", {}),
        }
        project_root = payload.get("project_root")
        if project_root:
            request["project_root"] = project_root
        result = app_worker.dispatch_request(request, default_root=self.server.root)
        self._json(200 if result.get("ok") else 400, result)

    def log_message(self, *_args: object) -> None:
        return


def serve(root: Path, port: int, *, launch_id: str = "") -> AppServer:
    if not (ASSET_ROOT / "index.html").is_file():
        raise OSError("TicketBunker application assets are not installed")
    return AppServer(("127.0.0.1", port), Path(root), launch_id=launch_id)


__all__ = ["ASSET_ROOT", "AppServer", "serve"]
