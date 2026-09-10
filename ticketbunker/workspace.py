"""Device-local checkout navigation and draft recovery. Never Bunker sync data."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import sqlite3
from typing import Any

from . import bunkerdb, commands, editor, platform_services
from .adapters.gitrun import git, repository_lock
from .ledger import Ledger


def _repository(payload: dict[str, Any]) -> tuple[Path, str]:
    bunker = bunkerdb.get_bunker(str(payload.get("bunker_id") or ""))
    row = next((row for row in bunker["repositories"] if row["id"] == payload.get("repository_id")), None)
    if not row or not row.get("local_path"):
        raise commands.CommandRejected("workspace_repository_missing", "Connect this repository's local folder in Project settings.")
    root = Path(row["local_path"]).resolve()
    if not root.is_dir():
        raise commands.CommandRejected("workspace_folder_missing", "This checkout is unavailable. Reconnect its folder in Project settings.")
    expected = payload.get("expected_root")
    if expected is not None and (not isinstance(expected, str) or platform_services.local_path_identity(Path(expected)) != platform_services.local_path_identity(root)):
        raise commands.CommandRejected("workspace_checkout_replaced", "This repository now points to another folder. Reopen its workspace. The old draft will not be written into the new checkout.")
    key = hashlib.sha256(platform_services.local_path_identity(root).encode()).hexdigest()
    return root, key


@contextmanager
def _database():
    path = platform_services.current().data_home() / "workspaces.sqlite3"
    db = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=10)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        db.execute("CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, state TEXT NOT NULL)")
        yield db
    except (sqlite3.Error, OSError, json.JSONDecodeError) as exc:
        raise commands.CommandRejected("workspace_recovery_unavailable", f"Workspace recovery could not be read or saved at {path}. Keep this window open and save your source files or copy your drafts before closing. If another TicketBunker window is saving, wait and retry. Existing recovery has not been reset.") from exc
    finally:
        if db is not None:
            db.close()


def _valid_state_shape(state: Any) -> bool:
    if (not isinstance(state, dict) or not isinstance(state.get("tabs", []), list)
            or len(state.get("tabs", [])) > 30):
        return False
    for tab in state.get("tabs", []):
        if (not isinstance(tab, dict) or not isinstance(tab.get("path"), str)
                or not all(isinstance(tab.get(key), str) for key in ("content", "saved", "branch", "digest"))
                or not re.fullmatch(r"[0-9a-f]{64}", tab["digest"])
                or not isinstance(tab.get("settings"), dict)
                or type(tab.get("line")) is not int or tab["line"] < 1):
            return False
    identities = [os.path.normcase(os.path.normpath(tab["path"])) for tab in state.get("tabs", [])]
    if len(set(identities)) != len(identities):
        return False
    for field in ("overlays", "expanded"):
        if not isinstance(state.get(field, []), list) or not all(isinstance(value, str) for value in state.get(field, [])):
            return False
    return (len(state.get("overlays", [])) <= 32
            and all(field not in state or isinstance(state[field], str) for field in ("active", "conversation", "bunkerId")))


def _recovery_state(raw: str) -> dict[str, Any]:
    state = json.loads(raw)
    if not _valid_state_shape(state):
        raise json.JSONDecodeError("Workspace recovery has an invalid shape", raw, 0)
    return state


def _inside(root: Path, raw: str) -> Path:
    try:
        raw.encode("utf-8")
        path = (root / raw).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise commands.CommandRejected("workspace_path_invalid", "That path cannot be opened. Choose a readable path inside this checkout.") from exc
    try:
        rel = path.relative_to(root)
    except ValueError as exc:
        raise commands.CommandRejected("workspace_path_outside", "Choose a path inside this checkout.") from exc
    if any(part.casefold() in {".git", ".tb"} for part in rel.parts):
        raise commands.CommandRejected("workspace_internal_path", "Git and TicketBunker internals are not workspace files.")
    return path


@contextmanager
def _directory_entries(folder: Path):
    try:
        with os.scandir(folder) as entries:
            yield entries
    except OSError as exc:
        raise commands.CommandRejected("workspace_directory_unavailable", "This folder cannot be read. Check its local permissions, or choose another folder.") from exc


@commands.register("workspace.open")
def open_workspace(_context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root, key = _repository(payload)
    with _database() as db:
        row = db.execute("SELECT revision,state FROM workspaces WHERE id=?", (key,)).fetchone()
        state = _recovery_state(row[1]) if row else {}
    log = Ledger(platform_services.current().data_home() / "workspace-git.jsonl")
    branch = git(root, ["branch", "--show-current"], log, "workspace_state").strip()
    head = git(root, ["rev-parse", "--verify", "--quiet", "HEAD"], log, "workspace_state", check=False).strip()
    return {"workspace_id": key, "root": str(root), "branch": branch, "head": head,
            "revision": row[0] if row else 0, "state": state}


@commands.register("workspace.tree")
def tree(_context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root, _ = _repository(payload)
    folder = _inside(root, str(payload.get("path") or ""))
    if not folder.is_dir():
        raise commands.CommandRejected("workspace_directory_missing", "This folder is no longer present. Refresh the file tree.")
    rows = []
    # Lazy, bounded directory reads; never recursively crawl ignored build trees.
    with _directory_entries(folder) as entries:
        for entry in entries:
            if entry.name.casefold() in {".git", ".tb"}:
                continue
            try:
                target = _inside(root, str(Path(entry.path).relative_to(root)))
                directory = entry.is_dir(follow_symlinks=False)
                if entry.is_symlink() and target.is_dir():
                    continue
                rows.append({"name": entry.name, "path": Path(entry.path).relative_to(root).as_posix(), "directory": directory})
            except (OSError, commands.CommandRejected):
                continue
            if len(rows) > 1000:
                break
    return {"entries": sorted(rows[:1000], key=lambda row: (not row["directory"], row["name"].casefold())), "truncated": len(rows) > 1000}


@commands.register("workspace.file.read")
def read_file(_context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root, _ = _repository(payload)
    path = str(payload.get("path") or "")
    _inside(root, path)
    log = Ledger(platform_services.current().data_home() / "workspace-git.jsonl")
    with repository_lock(root):
        draft = editor.read_source(root, path, editor._line(payload.get("line")))
        head = git(root, ["rev-parse", "--verify", "--quiet", "HEAD"], log, "workspace_read", check=False).strip()
        return {**draft, "head": head}


@commands.register("workspace.find")
def find_files(_context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root, _ = _repository(payload)
    query = str(payload.get("query") or "").strip().casefold()[:200]
    if not query:
        return {"paths": []}
    log = Ledger(platform_services.current().data_home() / "workspace-git.jsonl")
    listing = git(root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], log, "workspace_find")
    paths = []
    for name in dict.fromkeys(listing.split("\0")):
        if query not in name.casefold():
            continue
        try:
            if _inside(root, name).is_file():
                paths.append(name)
        except commands.CommandRejected:
            continue
        if len(paths) == 50:
            break
    return {"paths": paths}


@commands.register("workspace.file.save")
def save_file(_context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root, _ = _repository(payload)
    _inside(root, str(payload.get("path") or ""))
    log = Ledger(platform_services.current().data_home() / "workspace-git.jsonl")
    with repository_lock(root):
        branch = git(root, ["branch", "--show-current"], log, "workspace_save").strip()
        if "branch" not in payload or branch != payload["branch"]:
            raise commands.CommandRejected("workspace_branch_changed", "The checkout changed since this draft was opened. Your draft is kept. Return to its branch before saving, or reload the file to discard it.")
        if not branch:
            head = git(root, ["rev-parse", "--verify", "--quiet", "HEAD"], log, "workspace_save", check=False).strip()
            if not head or payload.get("head") != head:
                raise commands.CommandRejected("workspace_branch_changed", "The detached checkout changed or this draft has no saved commit identity. Your draft is kept. Return to its original commit, or copy the draft and reopen the file before saving.")
        if not isinstance(payload.get("content"), str):
            raise commands.CommandRejected("workspace_content_required", "The file draft is missing.")
        return editor.save_source(root, str(payload.get("path") or ""), payload["content"], str(payload.get("expected_digest") or ""))


@commands.register("workspace.save")
def save_workspace(_context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root, key = _repository(payload)
    state = payload.get("state")
    if not _valid_state_shape(state):
        raise commands.CommandRejected("workspace_state_invalid", "Workspace recovery needs valid file drafts, at most 30 files, and at most 32 ticket overlays. Copy your drafts before closing, then reopen the workspace.")
    for tab in state.get("tabs", []):
        _inside(root, tab["path"])
    try:
        encoded = json.dumps(state, ensure_ascii=False)
        size = len(encoded.encode("utf-8"))
    except (UnicodeError, TypeError, ValueError) as exc:
        raise commands.CommandRejected("workspace_state_invalid", "This workspace draft contains text that cannot be stored as UTF-8. Copy your draft before closing, then remove invalid characters and retry.") from exc
    if size > 7_000_000:
        raise commands.CommandRejected("workspace_state_large", "Save and close some drafts before restoring more workspace files.")
    with _database() as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision,state FROM workspaces WHERE id=?", (key,)).fetchone()
            revision = row[0] if row else 0
            if payload.get("revision") != revision:
                # Retrying a lost receipt is harmless only while the complete
                # requested snapshot is still the one already stored.
                if row and _recovery_state(row[1]) == state:
                    return {"revision": revision}
                raise commands.CommandRejected("workspace_conflict", "Another window updated this workspace's recovery. Your unsaved text is still in this window. Save your files or copy your drafts before closing it; do not discard either window's work.")
            db.execute("INSERT INTO workspaces VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,state=excluded.state", (key, revision + 1, encoded))
    return {"revision": revision + 1}
