"""Shared backend command dispatch for every TicketBunker transport.

The CLI, localhost viewer, and future HTTP service normalize their input to
the same command envelope and receive the same result.  Transports may render
results differently, but they never reimplement validation or side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class CommandRejected(ValueError):
    """A user-actionable command rejection with a stable machine code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class CommandContext:
    project_root: Path
    transport: str = "cli"


CommandHandler = Callable[[CommandContext, dict[str, Any]], dict[str, Any]]
_REGISTRY: dict[str, CommandHandler] = {}
_BUILTINS_LOADED = False


def _load_builtins() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True
    # Importing registers handlers through the decorators below. Keeping this
    # lazy avoids package-import cycles while making commands.dispatch the one
    # complete programmatic mutation entry point.
    from . import coworker, project, projectconfig, ticketcommands, truth, workflow  # noqa: F401


def register(name: str) -> Callable[[CommandHandler], CommandHandler]:
    def decorator(handler: CommandHandler) -> CommandHandler:
        if name in _REGISTRY:
            raise RuntimeError(f"command {name!r} is already registered")
        _REGISTRY[name] = handler
        return handler

    return decorator


def dispatch(name: str, payload: dict[str, Any], context: CommandContext) -> dict[str, Any]:
    _load_builtins()
    handler = _REGISTRY.get(name)
    if handler is None:
        raise CommandRejected("unknown_command", f"Unknown TicketBunker command: {name}")
    result = handler(context, dict(payload))
    return {"ok": True, "command": name, **result}


def known_commands() -> tuple[str, ...]:
    _load_builtins()
    return tuple(sorted(_REGISTRY))
