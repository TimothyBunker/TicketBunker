"""Owned source editing plus optional external-editor opening."""

from __future__ import annotations

import hashlib
import difflib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import commands, config, platform_services


_TERMINAL_EDITORS = {"vi", "vim", "nvim", "nano", "micro"}
_GOTO_EDITORS = {"code", "code-insiders", "codium", "cursor", "windsurf"}
_AUTOMATIC_EDITORS = ("vim", "nano")
_SOURCE_LIMIT = 2_000_000
_DEFAULT_LOCAL_SETTINGS: dict[str, Any] = {
    "editor_mode": "builtin",
    "indent_style": "spaces",
    "tab_size": 4,
    "line_wrap": False,
    "trim_trailing_whitespace": False,
    "ensure_final_newline": False,
}


def _windows_git_editor_paths(command: str, git_executable: str | None = None) -> list[Path]:
    """Return editor locations bundled with Git for Windows.

    Git Bash adds ``Git/usr/bin`` to its own PATH, but a native process
    launched from the browser commonly sees only ``Git/cmd``. Keep this
    lookup explicit so terminal availability and viewer availability agree.
    """
    windows = platform_services.PlatformServices.detect(system="win32")
    return windows.editor_candidates(command, git_executable=git_executable)


def _find_editor_executable(command: str) -> str | None:
    executable = Path(command).expanduser()
    if executable.is_absolute():
        return str(executable) if executable.is_file() else None
    found = shutil.which(command)
    if found:
        return found
    if platform_services.current().is_windows:
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


def _setting_bool(cfg: dict[str, Any], key: str) -> bool:
    value = cfg.get(key, _DEFAULT_LOCAL_SETTINGS[key])
    if isinstance(value, str):
        return value.casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def local_state(root: Path) -> dict[str, Any]:
    cfg = config.load()
    effective, source = resolve_editor(Path(root).resolve())
    configured_mode = cfg.get("editor_mode")
    fallback_mode = (
        "external"
        if os.environ.get("TB_EDITOR")
        else _DEFAULT_LOCAL_SETTINGS["editor_mode"]
    )
    mode = str(configured_mode or fallback_mode)
    if mode not in {"builtin", "external"}:
        mode = "builtin"
    indent_style = str(cfg.get("indent_style") or _DEFAULT_LOCAL_SETTINGS["indent_style"])
    if indent_style not in {"spaces", "tabs"}:
        indent_style = "spaces"
    try:
        tab_size = int(cfg.get("tab_size", _DEFAULT_LOCAL_SETTINGS["tab_size"]))
    except (TypeError, ValueError):
        tab_size = int(_DEFAULT_LOCAL_SETTINGS["tab_size"])
    if not 1 <= tab_size <= 8:
        tab_size = int(_DEFAULT_LOCAL_SETTINGS["tab_size"])
    return {
        "editor_mode": mode,
        "configured_editor": str(cfg.get("editor") or ""),
        "effective_editor": effective,
        "editor_source": source,
        "indent_style": indent_style,
        "tab_size": tab_size,
        "line_wrap": _setting_bool(cfg, "line_wrap"),
        "trim_trailing_whitespace": _setting_bool(cfg, "trim_trailing_whitespace"),
        "ensure_final_newline": _setting_bool(cfg, "ensure_final_newline"),
    }


def _validate_editor(command: str) -> None:
    try:
        argv = platform_services.current().split_command(command)
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
    if not isinstance(raw, str):
        raise commands.CommandRejected("source_path_invalid", "Choose a valid source file path, then retry.")
    value = raw
    if not value.strip():
        raise commands.CommandRejected("source_path_required", "Choose a source file, then retry.")
    try:
        candidate = Path(value).expanduser()
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    except (ValueError, OSError, RuntimeError) as exc:
        raise commands.CommandRejected("source_path_invalid", "That file path cannot be opened. Choose a readable source file, then retry.") from exc
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


def _line(raw: Any) -> int:
    try:
        line = int(raw or 1)
    except (TypeError, ValueError) as exc:
        raise commands.CommandRejected("source_line_invalid", "Choose a numeric source line.") from exc
    if line < 1:
        raise commands.CommandRejected("source_line_invalid", "Choose a source line greater than zero.")
    return line


def _source_bytes(source: Path) -> bytes:
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise commands.CommandRejected(
            "source_read_failed",
            "That source file could not be read. Check its local permissions, then retry.",
        ) from exc
    if size > _SOURCE_LIMIT:
        raise commands.CommandRejected(
            "source_too_large",
            "The built-in editor supports files up to 2 MB. Use an external editor for this file.",
        )
    try:
        value = source.read_bytes()
    except OSError as exc:
        raise commands.CommandRejected(
            "source_read_failed",
            "That source file could not be read. Check its local permissions, then retry.",
        ) from exc
    if len(value) > _SOURCE_LIMIT:
        raise commands.CommandRejected(
            "source_too_large",
            "The built-in editor supports files up to 2 MB. Use an external editor for this file.",
        )
    if b"\x00" in value:
        raise commands.CommandRejected(
            "source_binary",
            "The built-in editor opens text files only. Use an external editor for this file.",
        )
    return value


def _decode_source(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise commands.CommandRejected(
            "source_encoding",
            "The built-in editor supports UTF-8 text. Use an external editor for this file.",
        ) from exc


def _newline(value: bytes) -> str:
    crlf = value.count(b"\r\n")
    lf = value.count(b"\n") - crlf
    cr = value.count(b"\r") - crlf
    if cr > max(crlf, lf):
        return "cr"
    return "crlf" if crlf > lf else "lf"


def _encode_source(content: str, current: bytes) -> bytes:
    """Retain original terminators instead of rewriting unrelated mixed-EOL lines."""
    original = _decode_source(current)
    if content == original.replace("\r\n", "\n").replace("\r", "\n"):
        return current
    default = {"crlf": "\r\n", "cr": "\r", "lf": "\n"}[_newline(current)]
    parts = re.split(r"(\r\n|\r|\n)", original)
    old_lines, endings = parts[::2], parts[1::2]
    if len(set(endings)) <= 1:
        return content.replace("\n", default).encode("utf-8")
    new_lines = content.split("\n")
    mapped = {}
    prefix = 0
    while prefix < min(len(old_lines), len(new_lines)) and old_lines[prefix] == new_lines[prefix]:
        prefix += 1
    old_end, new_end = len(old_lines), len(new_lines)
    while old_end > prefix and new_end > prefix and old_lines[old_end - 1] == new_lines[new_end - 1]:
        old_end -= 1
        new_end -= 1
    mapped.update(zip(range(prefix), range(prefix)))
    mapped.update(zip(range(new_end, len(new_lines)), range(old_end, len(old_lines))))
    old_count, new_count = old_end - prefix, new_end - prefix
    # SequenceMatcher's autojunk does not bound periodic, low-cardinality
    # input. Keep unchanged edges exactly, then use positional terminators
    # for equal-sized edits or large changed regions. Only small unequal
    # regions need alignment; the fallback affects terminators, never text.
    if old_count == new_count or old_count * new_count > 1_000_000:
        mapped.update(zip(range(prefix, new_end), range(prefix, old_end)))
    else:
        for operation, start, end, new_start, new_stop in difflib.SequenceMatcher(None, old_lines[prefix:old_end], new_lines[prefix:new_end]).get_opcodes():
            if operation in {"equal", "replace"}:
                mapped.update(zip(range(prefix + new_start, prefix + new_stop), range(prefix + start, prefix + end)))
    pieces = []
    for index, line in enumerate(new_lines):
        pieces.append(line)
        if index < len(new_lines) - 1:
            prior = mapped.get(index, len(endings))
            ending = endings[prior] if prior < len(endings) else default
            # Removing a line's text can place a bare CR immediately before
            # its LF terminator. Those bytes would become one CRLF newline,
            # silently dropping a blank line. Keep two logical separators.
            if not line and len(pieces) >= 2 and pieces[-2] == "\r" and ending == "\n":
                ending = "\r\n"
            pieces.append(ending)
    return "".join(pieces).encode("utf-8")


def read_source(root: Path, path: str, line: int = 1) -> dict[str, Any]:
    root = Path(root).resolve()
    source, relative = _source(root, path)
    line = _line(line)
    raw = _source_bytes(source)
    content = _decode_source(raw).replace("\r\n", "\n").replace("\r", "\n")
    settings = local_state(root)
    proc = subprocess.run(
        ["git", "-C", str(root), "branch", "--show-current"],
        capture_output=True,
        text=True,
    )
    return {
        "path": relative,
        "line": min(line, max(1, content.count("\n") + 1)),
        "content": content,
        "digest": hashlib.sha256(raw).hexdigest(),
        "newline": _newline(raw),
        "branch": proc.stdout.strip() if proc.returncode == 0 else "",
        "settings": settings,
    }


def resolve_source(root: Path, path: str, line: int = 1) -> dict[str, Any]:
    """Resolve one checked source reference without opening or reading it."""
    root = Path(root).resolve()
    source, relative = _source(root, path)
    line = _line(line)
    return {
        "path": relative,
        "absolute_path": str(source),
        "line": line,
        "reference": f"{source}:{line}",
    }


def _formatted_content(content: str, settings: dict[str, Any]) -> str:
    value = content.replace("\r\n", "\n").replace("\r", "\n")
    if settings["trim_trailing_whitespace"]:
        value = "\n".join(line.rstrip(" \t") for line in value.split("\n"))
    if settings["ensure_final_newline"] and value and not value.endswith("\n"):
        value += "\n"
    return value


def save_source(root: Path, path: str, content: str, expected_digest: str) -> dict[str, Any]:
    root = Path(root).resolve()
    source, relative = _source(root, path)
    if not _valid_digest(expected_digest):
        raise commands.CommandRejected("source_digest_required", "Reload the file before saving it.")
    current = _source_bytes(source)
    current_digest = hashlib.sha256(current).hexdigest()
    if current_digest != expected_digest:
        raise commands.CommandRejected(
            "source_changed",
            "This file changed after the editor opened. Your draft was kept in the browser. Reload before saving.",
        )
    settings = local_state(root)
    formatted = _formatted_content(content, settings)
    if "\x00" in formatted:
        raise commands.CommandRejected("source_binary", "The editor saves text only. Remove the null character from this draft, then retry.")
    encoded = _encode_source(formatted, current)
    if len(encoded) > _SOURCE_LIMIT:
        raise commands.CommandRejected(
            "source_too_large",
            "The built-in editor supports files up to 2 MB. Shorten this draft or use an external editor.",
        )
    if encoded == current:
        return {
            "saved": False,
            "path": relative,
            "content": formatted,
            "digest": current_digest,
        }
    temporary = ""
    try:
        mode = stat.S_IMODE(source.stat().st_mode)
        if not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) or not os.access(source, os.W_OK):
            raise PermissionError("The source file is read-only")
        descriptor, temporary = tempfile.mkstemp(prefix=f".{source.name}.tb-", dir=source.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if hashlib.sha256(_source_bytes(source)).hexdigest() != current_digest:
            os.unlink(temporary)
            temporary = ""
            raise commands.CommandRejected(
                "source_changed",
                "This file changed while it was being saved. Your draft was kept in the browser. Reload before saving.",
            )
        os.replace(temporary, source)
    except OSError as exc:
        raise commands.CommandRejected(
            "source_save_failed",
            "The file could not be saved. Your draft was kept in the browser. Check local permissions, then retry.",
        ) from exc
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return {
        "saved": True,
        "path": relative,
        "content": formatted,
        "digest": hashlib.sha256(encoded).hexdigest(),
    }


def _valid_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.casefold())


def _editor_argv(editor: str, source: Path, line: int) -> tuple[list[str], bool]:
    try:
        argv = platform_services.current().split_command(editor)
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
    resolved = platform_services.current().terminal_argv(argv, which=shutil.which)
    if resolved is not None:
        return resolved
    raise commands.CommandRejected(
        "source_editor_terminal_required",
        "The configured editor needs a terminal, but no supported terminal launcher was found. "
        "Open 'tb config' and choose Local settings, then select a graphical editor command such as "
        "'code --goto {file}:{line}'.",
    )


def _builtin_open_result(relative: str, line: int, *, fallback: str = "") -> dict[str, Any]:
    value: dict[str, Any] = {
        "opened": True,
        "mode": "builtin",
        "path": relative,
        "line": line,
        "editor": "TicketBunker",
    }
    if fallback:
        value["fallback"] = fallback
    return value


def open_source(root: Path, path: str, line: int) -> dict[str, Any]:
    root = Path(root).resolve()
    source, relative = _source(root, path)
    line = _line(line)
    if local_state(root)["editor_mode"] == "builtin":
        return _builtin_open_result(relative, line)
    try:
        editor = _configured_editor(root)
        if not editor:
            raise commands.CommandRejected(
                "source_editor_missing",
                "No external editor was found.",
            )
        argv, terminal = _editor_argv(editor, source, line)
        if terminal:
            argv = _terminal_argv(argv)
    except commands.CommandRejected as exc:
        if exc.code.startswith("source_editor"):
            return _builtin_open_result(relative, line, fallback=exc.code)
        raise
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    host = platform_services.current()
    kwargs.update(
        host.background_process_options(detached=False, new_console=terminal)
    )
    if host.is_windows and terminal:
        kwargs.pop("stdin", None)
        kwargs.pop("stdout", None)
        kwargs.pop("stderr", None)
    try:
        subprocess.Popen(argv, **kwargs)
    except OSError:
        return _builtin_open_result(relative, line, fallback="source_editor_failed")
    return {
        "opened": True,
        "mode": "external",
        "path": relative,
        "line": line,
        "editor": Path(argv[0]).name,
    }


@commands.register("editor.open")
def command_open(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    return open_source(
        Path(context.project_root),
        str(payload.get("path") or ""),
        _line(payload.get("line")),
    )


@commands.register("editor.read")
def command_read(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    return read_source(
        Path(context.project_root),
        str(payload.get("path") or ""),
        _line(payload.get("line")),
    )


@commands.register("editor.resolve")
def command_resolve(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    return resolve_source(
        Path(context.project_root),
        str(payload.get("path") or ""),
        _line(payload.get("line")),
    )


@commands.register("editor.save")
def command_save(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    content = payload.get("content")
    if not isinstance(content, str):
        raise commands.CommandRejected("source_content_required", "The editor draft is missing. Reload the file.")
    return save_source(
        Path(context.project_root),
        str(payload.get("path") or ""),
        content,
        str(payload.get("expected_digest") or ""),
    )


@commands.register("local.editor.update")
def command_update(context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    recognized = {
        "editor_mode",
        "editor",
        "indent_style",
        "tab_size",
        "line_wrap",
        "trim_trailing_whitespace",
        "ensure_final_newline",
    }
    if not recognized.intersection(payload):
        raise commands.CommandRejected("local_editor_required", "Choose at least one local editor setting.")
    cfg = config.load()
    if "editor_mode" in payload:
        mode = str(payload.get("editor_mode") or "").strip().casefold().replace("-", "")
        mode = "builtin" if mode in {"builtin", "included"} else mode
        if mode not in {"builtin", "external"}:
            raise commands.CommandRejected(
                "local_editor_mode_invalid",
                "Choose the built-in editor or an external editor.",
            )
        cfg["editor_mode"] = mode
    if "editor" in payload:
        value = str(payload.get("editor") or "").strip()
        if value.casefold() in {"builtin", "built-in", "included"}:
            cfg["editor_mode"] = "builtin"
            value = ""
        elif value.casefold() in {"auto", "automatic"}:
            if "editor_mode" not in payload:
                cfg["editor_mode"] = "external"
            value = ""
        elif value and "editor_mode" not in payload:
            cfg["editor_mode"] = "external"
        if len(value) > 500:
            raise commands.CommandRejected(
                "local_editor_too_long",
                "Keep the external editor command under 500 characters.",
            )
        if value:
            if str(cfg.get("editor_mode") or "builtin") == "external":
                _validate_editor(value)
            cfg["editor"] = value
        else:
            cfg.pop("editor", None)
    if "indent_style" in payload:
        indent_style = str(payload.get("indent_style") or "").strip().casefold()
        if indent_style not in {"spaces", "tabs"}:
            raise commands.CommandRejected("local_indent_invalid", "Choose spaces or tabs for indentation.")
        cfg["indent_style"] = indent_style
    if "tab_size" in payload:
        try:
            tab_size = int(payload.get("tab_size"))
        except (TypeError, ValueError) as exc:
            raise commands.CommandRejected("local_tab_size_invalid", "Choose a tab width from 1 to 8.") from exc
        if not 1 <= tab_size <= 8:
            raise commands.CommandRejected("local_tab_size_invalid", "Choose a tab width from 1 to 8.")
        cfg["tab_size"] = tab_size
    for key in ("line_wrap", "trim_trailing_whitespace", "ensure_final_newline"):
        if key in payload:
            value = payload[key]
            if isinstance(value, str):
                value = value.casefold() in {"1", "true", "yes", "on"}
            cfg[key] = bool(value)
    config.save(cfg)
    return local_state(Path(context.project_root))


__all__ = [
    "command_open",
    "command_read",
    "command_save",
    "command_update",
    "local_state",
    "open_source",
    "read_source",
    "resolve_source",
    "resolve_editor",
    "save_source",
]
