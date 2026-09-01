"""Launch the optional TicketBunker desktop shell.

The Python package remains the application core.  This module only discovers
and starts the native shell; it never uses a shell command string and never
changes project state.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import platform_services


class DesktopLaunchError(RuntimeError):
    """The user explicitly configured a desktop app that cannot be started."""


def _executable_name() -> str:
    return platform_services.current().executable_name("ticketbunker-desktop")


def launcher() -> Path | None:
    """Return the desktop executable, preferring an explicit local override."""
    configured = os.environ.get("TB_DESKTOP_APP", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_file():
            raise DesktopLaunchError(
                f"the TicketBunker app configured by TB_DESKTOP_APP was not found at "
                f"{candidate}. Update that path or unset TB_DESKTOP_APP."
            )
        return candidate.resolve()

    name = _executable_name()
    sibling = Path(sys.executable).resolve().parent / name
    if sibling.is_file():
        return sibling
    found = shutil.which(name)
    return Path(found).resolve() if found else None


def _route(value: str) -> str:
    if not value.startswith("/") or value.startswith("//") or "://" in value:
        raise DesktopLaunchError("a TicketBunker app route must begin with one '/'")
    return value


def open_project(root: Path, *, route: str = "/") -> bool:
    """Open *root* in the desktop app, or return False when it is not installed."""
    executable = launcher()
    if executable is None:
        return False

    argv = [
        str(executable),
        "--repo",
        str(root.resolve()),
        "--route",
        _route(route),
    ]
    kwargs: dict[str, object] = {
        "cwd": str(root.resolve()),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    kwargs.update(
        platform_services.current().background_process_options(detached=True)
    )
    try:
        subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        raise DesktopLaunchError(
            f"TicketBunker could not open its desktop app ({exc}). "
            "Run 'tb view --browser' to use the browser instead."
        ) from exc
    return True


__all__ = ["DesktopLaunchError", "launcher", "open_project"]
