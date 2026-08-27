"""Explicit local source opening shared by the CLI and localhost viewer."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import commands


_TERMINAL_EDITORS = {"vi", "vim", "nvim", "nano", "micro"}
_GOTO_EDITORS = {"code", "code-insiders", "codium", "cursor", "windsurf"}


def _configured_editor(root: Path) -> str:
    configured = os.environ.get("TB_EDITOR") or os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if configured:
        return configured
    proc = subprocess.run(
        ["git", "-C", str(root), "config", "--get", "core.editor"],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    for candidate in ("code", "cursor", "codium", "subl", "idea"):
        if shutil.which(candidate):
            return candidate
    return ""


def _source(root: Path, raw: str) -> tuple[Path, str]:
    value = raw.strip()
    if not value:
        raise commands.CommandRejected("source_path_required", "Choose a source file, then retry.")
    candidate = Path(value).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise commands.CommandRejected(
            "source_path_outside_project",
            "That source reference is outside this checkout. Choose a project file, then retry.",
        ) from exc
    if not resolved.is_file():
        raise commands.CommandRejected(
            "source_path_missing",
            f"{relative.as_posix()} is not present on this branch. Refresh the report or choose another file.",
        )
    return resolved, relative.as_posix()


def _editor_argv(editor: str, source: Path, line: int) -> tuple[list[str], bool]:
    try:
        argv = shlex.split(editor, posix=os.name != "nt")
    except ValueError as exc:
        raise commands.CommandRejected(
            "source_editor_invalid",
            "TB_EDITOR or your configured editor could not be parsed. Use a command such as "
            "'code --goto {file}:{line}'.",
        ) from exc
    if not argv:
        raise commands.CommandRejected(
            "source_editor_missing",
            "No local editor was found. Set TB_EDITOR, for example "
            "TB_EDITOR='code --goto {file}:{line}'.",
        )
    source_text = str(source)
    name = Path(argv[0]).name.casefold().removesuffix(".exe")
    templated = any("{file}" in part or "{line}" in part for part in argv)
    if templated:
        return (
            [part.replace("{file}", source_text).replace("{line}", str(line)) for part in argv],
            name in _TERMINAL_EDITORS,
        )
    if name in _GOTO_EDITORS:
        argv.extend(("--goto", f"{source_text}:{line}"))
    elif name in {"subl", "sublime_text"}:
        argv.append(f"{source_text}:{line}")
    elif name in {"idea", "idea64", "pycharm", "pycharm64"}:
        argv.extend(("--line", str(line), source_text))
    elif name in {"emacs", "emacsclient"}:
        argv.extend((f"+{line}", source_text))
    elif name in _TERMINAL_EDITORS:
        argv.extend((f"+{line}", source_text))
        return argv, True
    else:
        argv.append(source_text)
    return argv, False


def _terminal_argv(argv: list[str]) -> list[str]:
    if os.name == "nt":
        return argv
    if sys.platform == "darwin":
        raise commands.CommandRejected(
            "source_editor_terminal_required",
            "The configured editor needs a terminal. Set TB_EDITOR to a graphical editor command such as "
            "'code --goto {file}:{line}'.",
        )
    for terminal, separator in (
        ("x-terminal-emulator", "-e"),
        ("gnome-terminal", "--"),
        ("konsole", "-e"),
    ):
        resolved = shutil.which(terminal)
        if resolved:
            return [resolved, separator, *argv]
    raise commands.CommandRejected(
        "source_editor_terminal_required",
        "The configured editor needs a terminal, but no supported terminal launcher was found. "
        "Set TB_EDITOR to a graphical editor command such as 'code --goto {file}:{line}'.",
    )


def open_source(root: Path, path: str, line: int) -> dict[str, Any]:
    root = Path(root).resolve()
    source, relative = _source(root, path)
    if line < 1:
        raise commands.CommandRejected("source_line_invalid", "Choose a source line greater than zero.")
    editor = _configured_editor(root)
    if not editor:
        raise commands.CommandRejected(
            "source_editor_missing",
            "No local editor was found. Set TB_EDITOR, for example "
            "TB_EDITOR='code --goto {file}:{line}'.",
        )
    argv, terminal = _editor_argv(editor, source, line)
    if terminal:
        argv = _terminal_argv(argv)
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        if terminal:
            kwargs["creationflags"] |= subprocess.CREATE_NEW_CONSOLE
            kwargs.pop("stdin", None)
            kwargs.pop("stdout", None)
            kwargs.pop("stderr", None)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        raise commands.CommandRejected(
            "source_editor_failed",
            f"The configured editor could not start ({exc}). Set TB_EDITOR and retry.",
        ) from exc
    return {
        "opened": True,
        "path": relative,
        "line": line,
        "editor": Path(argv[0]).name,
    }


@commands.register("editor.open")
def command_open(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    raw_line = payload.get("line") or 1
    try:
        line = int(raw_line)
    except (TypeError, ValueError) as exc:
        raise commands.CommandRejected("source_line_invalid", "Choose a numeric source line.") from exc
    return open_source(Path(context.project_root), str(payload.get("path") or ""), line)


__all__ = ["command_open", "open_source"]
