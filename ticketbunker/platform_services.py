"""Host-platform services for TicketBunker's machine-local behavior.

The domain model never needs to know whether it is running on Linux, Windows,
macOS, or inside a particular environment such as WSL. Only installation
paths, executable names, command parsing, terminal discovery, and detached
process flags vary. Keeping those choices here makes the Python core portable
and gives tests an explicit platform to substitute.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


_SYSTEMS = {
    "linux": ("linux", "linux"),
    "win32": ("windows", "windows"),
    "cygwin": ("windows", "windows"),
    "darwin": ("macos", "darwin"),
}
_ARCHITECTURES = {
    "x86_64": "x64",
    "amd64": "x64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


@dataclass(frozen=True)
class PlatformServices:
    """Host facts and machine-local operations that are allowed to vary."""

    family: str
    artifact_system: str
    architecture: str | None

    @classmethod
    def detect(
        cls,
        *,
        system: str | None = None,
        machine: str | None = None,
    ) -> "PlatformServices":
        raw_system = (system or sys.platform).casefold()
        family, artifact_system = _SYSTEMS.get(
            raw_system, ("unsupported", raw_system or "unknown")
        )
        architecture = _ARCHITECTURES.get((machine or platform.machine()).casefold())
        return cls(family, artifact_system, architecture)

    @property
    def runtime_key(self) -> str | None:
        if self.family == "unsupported" or self.architecture is None:
            return None
        return f"{self.artifact_system}-{self.architecture}"

    @property
    def is_windows(self) -> bool:
        return self.family == "windows"

    def executable_name(self, stem: str) -> str:
        return f"{stem}.exe" if self.is_windows else stem

    def data_home(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        home: Path | None = None,
    ) -> Path:
        values = os.environ if environ is None else environ
        override = values.get("TB_DATA_HOME")
        if override:
            return Path(override).expanduser().resolve()
        user_home = Path.home() if home is None else Path(home)
        if self.family == "windows":
            base = Path(values.get("LOCALAPPDATA") or (user_home / "AppData" / "Local"))
            return base / "TicketBunker"
        if self.family == "macos":
            return user_home / "Library" / "Application Support" / "TicketBunker"
        base = Path(values.get("XDG_DATA_HOME") or (user_home / ".local" / "share"))
        return base / "ticketbunker"

    def split_command(self, command: str) -> list[str]:
        values = shlex.split(command, posix=not self.is_windows)
        if not self.is_windows:
            return values
        # shlex's non-POSIX mode deliberately retains paired quotes. Popen's
        # argv form expects the executable without them, so remove only one
        # balanced outer pair while preserving every character inside it.
        return [
            value[1:-1]
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}
            else value
            for value in values
        ]

    def background_process_options(
        self,
        *,
        detached: bool,
        new_console: bool = False,
    ) -> dict[str, Any]:
        """Return subprocess options without leaking OS flags into callers."""
        if not self.is_windows:
            return {"start_new_session": True}
        flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        if new_console:
            flags |= int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        elif detached:
            flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))
        return {"creationflags": flags}

    def terminal_argv(
        self,
        argv: list[str],
        *,
        which: Callable[[str], str | None] = shutil.which,
    ) -> list[str] | None:
        if self.is_windows:
            return list(argv)
        if self.family != "linux":
            return None
        for terminal, separator in (
            ("x-terminal-emulator", "-e"),
            ("gnome-terminal", "--"),
            ("konsole", "-e"),
        ):
            resolved = which(terminal)
            if resolved:
                return [resolved, separator, *argv]
        return None

    def editor_candidates(
        self,
        command: str,
        *,
        git_executable: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> list[Path]:
        """Return OS-owned editor candidates not normally visible on PATH."""
        if not self.is_windows:
            return []
        name = Path(command).name.casefold().removesuffix(".exe")
        if name != "vim":
            return []
        values = os.environ if environ is None else environ
        roots: list[Path] = []
        git = git_executable or shutil.which("git")
        if git:
            git_path = Path(git).expanduser()
            if git_path.parent.name.casefold() in {"cmd", "bin"}:
                roots.append(git_path.parent.parent)
        for variable, suffix in (
            ("ProgramFiles", "Git"),
            ("ProgramFiles(x86)", "Git"),
            ("LOCALAPPDATA", "Programs/Git"),
        ):
            base = values.get(variable)
            if base:
                roots.append(Path(base) / suffix)
        candidates: list[Path] = []
        for root in roots:
            for relative in ("usr/bin/vim.exe", "mingw64/bin/vim.exe"):
                candidate = root / relative
                if candidate not in candidates:
                    candidates.append(candidate)
        return candidates

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "architecture": self.architecture or "unknown",
            "runtime_key": self.runtime_key or "",
            "data_home": str(self.data_home()),
        }


def current() -> PlatformServices:
    return PlatformServices.detect()


__all__ = ["PlatformServices", "current"]
