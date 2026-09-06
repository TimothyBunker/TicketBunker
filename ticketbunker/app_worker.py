"""Long-lived typed transport between the native shell and Python core.

The desktop app sends one bounded JSON object per line and receives one JSON
object with the same request id. The worker is a transport only: every action
still crosses ``commands.dispatch`` and therefore the existing typed handlers,
validation, ledgering, and report gates.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from . import commands, platform_services


MAX_REQUEST_BYTES = 8 * 1024 * 1024
DESKTOP_ACTIONS = frozenset(
    {
        "application.state",
        "application.profile.update",
        "application.agent.state",
        "application.agent.prepare",
        "application.agent.configure",
        "application.release.check",
        "application.project.add",
        "application.project.connect",
        "application.project.remove",
        "application.bunker.create",
        "application.bunker.get",
        "application.bunker.configuration.get",
        "application.bunker.configuration.replace",
        "application.bunker.project.create",
        "application.repository.inspect",
        "application.bunker.repository.attach",
        "application.bunker.repository.detach",
        "application.bunker.repository.replace",
        "application.bunker.remote.configure",
        "application.bunker.share",
        "application.bunker.invitation.create",
        "application.bunker.invitations",
        "application.bunker.invitation.revoke",
        "application.bunker.members",
        "application.bunker.sync",
        "application.project.sync",
        "application.project.checks.detect",
        "application.ticket.update.draft",
        "application.ticket.update.post",
        "coworker.file.forget",
        "application.tracker.connect",
        "application.tracker.boards",
        "application.project.link",
        "application.project.unlink",
        "credentials.state",
        "credentials.update",
        "application.invitation.inspect",
        "application.bunker.join",
        "application.bunker.import.companion",
        "application.ticket.list",
        "application.ticket.get",
        "coworker.state",
        "coworker.chat",
        "coworker.proposal.accept",
        "coworker.proposal.dismiss",
        "editor.read",
        "editor.resolve",
        "editor.save",
        "local.editor.update",
        "truth.queue",
        "truth.refresh",
        "truth.synthesize",
        "workflow.start",
        "workflow.attach",
    }
)


def _error(request_id: Any, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"id": request_id, "ok": False, "code": code, "message": message, **extra}


def _diagnostic(action: str, exc: BaseException) -> tuple[str, str]:
    diagnostic_id = uuid.uuid4().hex[:12]
    path = platform_services.current().data_home() / "diagnostics" / "app-worker.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        frames = [
            {
                "file": Path(frame.filename).name,
                "line": frame.lineno,
                "function": frame.name,
            }
            for frame in traceback.extract_tb(exc.__traceback__)
        ]
        value = {
            "id": diagnostic_id,
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "action": action,
            "error_type": type(exc).__name__,
            "frames": frames,
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return diagnostic_id, str(path)


def dispatch_request(value: Any, *, default_root: Path | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _error(None, "request_invalid", "The application request must be an object.")
    request_id = value.get("id")
    if not isinstance(request_id, (str, int)) or len(str(request_id)) > 128:
        return _error(None, "request_id_invalid", "The application request ID is invalid.")
    action = value.get("action")
    if not isinstance(action, str) or action not in DESKTOP_ACTIONS:
        return _error(
            request_id,
            "command_unavailable",
            "That command is not available to the desktop application.",
        )
    payload = value.get("payload", {})
    if not isinstance(payload, dict):
        return _error(request_id, "payload_invalid", "The application payload must be an object.")
    raw_root = value.get("project_root")
    if raw_root is not None and (not isinstance(raw_root, str) or not raw_root.strip()):
        return _error(request_id, "project_root_invalid", "The project root is invalid.")
    root = (
        Path(raw_root).expanduser().resolve()
        if raw_root
        else (default_root or Path.cwd()).resolve()
    )
    try:
        # A stray print from a handler must never corrupt the JSONL protocol.
        with contextlib.redirect_stdout(sys.stderr):
            result = commands.dispatch(
                action,
                payload,
                commands.CommandContext(root, transport="desktop-ipc"),
            )
        return {"id": request_id, **result}
    except commands.CommandRejected as exc:
        return _error(request_id, exc.code, exc.message)
    except Exception as exc:  # the native shell is an error boundary
        diagnostic_id, diagnostic_path = _diagnostic(action, exc)
        return _error(
            request_id,
            "internal_error",
            f"TicketBunker could not complete that action. Diagnostic {diagnostic_id} was saved at {diagnostic_path}.",
            diagnostic_id=diagnostic_id,
            diagnostic_path=diagnostic_path,
        )


def serve(
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
    *,
    default_root: Path | None = None,
) -> int:
    source = input_stream or sys.stdin.buffer
    target = output_stream or sys.stdout.buffer
    while True:
        raw = source.readline(MAX_REQUEST_BYTES + 2)
        if not raw:
            return 0
        if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            response = _error(
                None,
                "request_too_large",
                "The application request exceeded the local transport limit.",
            )
            if not raw.endswith(b"\n"):
                while raw and not raw.endswith(b"\n"):
                    raw = source.readline(MAX_REQUEST_BYTES + 2)
        else:
            try:
                request = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                response = _error(
                    None,
                    "request_invalid",
                    "The application request is not valid JSON.",
                )
            else:
                response = dispatch_request(request, default_root=default_root)
        target.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        target.flush()


__all__ = ["DESKTOP_ACTIONS", "MAX_REQUEST_BYTES", "dispatch_request", "serve"]
