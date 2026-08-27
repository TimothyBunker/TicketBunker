"""Explicit local source opening shared by the CLI and localhost viewer."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import commands, config


_TERMINAL_EDITORS = {"vi", "vim", "nvim", "nano", "micro"}
_GOTO_EDITORS = {"code", "code-insiders", "codium", "cursor", "windsurf"}
_AUTOMATIC_EDITORS = ("vim", "nano")


def _windows_git_editor_paths(command: str, git_executable: str | None = None) -> list[Path]:
    """Return editor locations bundled with Git for Windows.

    Git Bash adds ``Git/usr/bin`` to its own PATH, but a native process
    launched from the browser commonly sees only ``Git/cmd``. Keep this
    lookup explicit so terminal availability and viewer availability agree.
    """
    name = Path(command).name.casefold().removesuffix(".exe")
    if name != "vim":
        return []
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
        base = os.environ.get(variable)
        if base:
            roots.append(Path(base) / suffix)
    paths: list[Path] = []
    for root in roots:
        for relative in ("usr/bin/vim.exe", "mingw64/bin/vim.exe"):
            candidate = root / relative
            if candidate not in paths:
                paths.append(candidate)
    return paths


def _find_editor_executable(command: str) -> str | None:
    executable = Path(command).expanduser()
    if executable.is_absolute():
        return str(executable) if executable.is_file() else None
    found = shutil.which(command)
    if found:
        return found
    if os.name == "nt":
        for candidate in _windows_git_editor_paths(command):
            if candidate.is_file():
                return str(candidate)
    return None


def resolve_editor(root: Path) -> tuple[str, str]:
    """Return the effective editor command and the machine-local source."""
    if os.environ.get("TB_EDITOR"):
        return os.environ["TB_EDITOR"], "TB_EDITOR"
    configured = str(config.load().get("editor") or "").strip()
    if configured:
        return configured, "local setting"
    if os.environ.get("VISUAL"):
        return os.environ["VISUAL"], "VISUAL"
    if os.environ.get("EDITOR"):
        return os.environ["EDITOR"], "EDITOR"
    proc = subprocess.run(
        ["git", "-C", str(root), "config", "--get", "core.editor"],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip(), "Git core.editor"
    for candidate in _AUTOMATIC_EDITORS:
        if _find_editor_executable(candidate):
            return candidate, "automatic"
    return "", "unavailable"


def _configured_editor(root: Path) -> str:
    return resolve_editor(root)[0]


def local_state(root: Path) -> dict[str, str]:
    effective, source = resolve_editor(Path(root).resolve())
    return {
        "configured_editor": str(config.load().get("editor") or ""),
        "effective_editor": effective,
        "editor_source": source,
    }


def _validate_editor(command: str) -> None:
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise commands.CommandRejected(
            "local_editor_invalid",
            "That editor command could not be parsed. Use a command such as 'vim' or "
            "'code --goto {file}:{line}'.",
        ) from exc
    if not argv:
        return
    if not _find_editor_executable(argv[0]):
        raise commands.CommandRejected(
            "local_editor_missing",
            f"{argv[0]} is not available on this machine. Choose an installed editor or leave the field blank for automatic selection.",
        )


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
            "Your local editor command could not be parsed. Open 'tb config' and choose Local settings, "
            "or use a command such as "
            "'code --goto {file}:{line}'.",
        ) from exc
    if not argv:
        raise commands.CommandRejected(
            "source_editor_missing",
            "No local editor was found. Open 'tb config' and choose Local settings, then select an editor.",
        )
    resolved = _find_editor_executable(argv[0])
    if not resolved:
        raise commands.CommandRejected(
            "source_editor_missing",
            f"{argv[0]} is no longer available on this machine. Open 'tb config', choose Local settings, "
            "and select another editor.",
        )
    argv[0] = resolved
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
            "The configured editor needs a terminal. Open 'tb config' and choose Local settings, then "
            "select a graphical editor command such as "
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
        "Open 'tb config' and choose Local settings, then select a graphical editor command such as "
        "'code --goto {file}:{line}'.",
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
            "No local editor was found. Open 'tb config' and choose Local settings, then select an editor.",
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
            f"The configured editor could not start ({exc}). Open 'tb config', choose Local settings, "
            "and select another editor.",
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


@commands.register("local.editor.update")
def command_update(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    if "editor" not in payload:
        raise commands.CommandRejected("local_editor_required", "Choose an editor or automatic selection.")
    value = str(payload.get("editor") or "").strip()
    if value.casefold() in {"auto", "automatic"}:
        value = ""
    if len(value) > 500:
        raise commands.CommandRejected(
            "local_editor_too_long",
            "Keep the editor command under 500 characters.",
        )
    if value:
        _validate_editor(value)
    cfg = config.load()
    if value:
        cfg["editor"] = value
    else:
        cfg.pop("editor", None)
    config.save(cfg)
    return local_state(Path(context.project_root))


__all__ = ["command_open", "command_update", "local_state", "open_source", "resolve_editor"]
