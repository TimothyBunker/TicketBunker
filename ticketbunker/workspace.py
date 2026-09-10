"""Device-local checkout navigation and draft recovery. Never Bunker sync data."""
from __future__ import annotations

import hashlib
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
    key = hashlib.sha256(platform_services.local_path_identity(root).encode()).hexdigest()
    return root, key


def _database() -> sqlite3.Connection:
    path = platform_services.current().data_home() / "workspaces.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    db.execute("CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, state TEXT NOT NULL)")
    return db


def _inside(root: Path, raw: str) -> Path:
    path = (root / raw).resolve()
    try:
        rel = path.relative_to(root)
    except ValueError as exc:
        raise commands.CommandRejected("workspace_path_outside", "Choose a path inside this checkout.") from exc
    if any(part.casefold() in {".git", ".tb"} for part in rel.parts):
        raise commands.CommandRejected("workspace_internal_path", "Git and TicketBunker internals are not workspace files.")
    return path


@commands.register("workspace.open")
def open_workspace(_context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root, key = _repository(payload)
    db = _database()
    try:
        row = db.execute("SELECT revision,state FROM workspaces WHERE id=?", (key,)).fetchone()
    finally:
        db.close()
    log = Ledger(platform_services.current().data_home() / "workspace-git.jsonl")
    branch = git(root, ["branch", "--show-current"], log, "workspace_state").strip()
    head = git(root, ["rev-parse", "HEAD"], log, "workspace_state", check=False).strip()
    return {"workspace_id": key, "root": str(root), "branch": branch, "head": head,
            "revision": row[0] if row else 0, "state": json.loads(row[1]) if row else {}}


@commands.register("workspace.tree")
def tree(_context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root, _ = _repository(payload)
    folder = _inside(root, str(payload.get("path") or ""))
    if not folder.is_dir():
        raise commands.CommandRejected("workspace_directory_missing", "This folder is no longer present. Refresh the file tree.")
    rows = []
    # Lazy, bounded directory reads; never recursively crawl ignored build trees.
    with os.scandir(folder) as entries:
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
            if len(rows) >= 1000:
                break
    return {"entries": sorted(rows, key=lambda row: (not row["directory"], row["name"].casefold())), "truncated": len(rows) == 1000}


@commands.register("workspace.file.read")
def read_file(_context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root, _ = _repository(payload)
    path = str(payload.get("path") or "")
    _inside(root, path)
    return editor.read_source(root, path, editor._line(payload.get("line")))


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
        if not isinstance(payload.get("content"), str):
            raise commands.CommandRejected("workspace_content_required", "The file draft is missing.")
        return editor.save_source(root, str(payload.get("path") or ""), payload["content"], str(payload.get("expected_digest") or ""))


@commands.register("workspace.save")
def save_workspace(_context: commands.CommandContext, payload: dict[str, Any]) -> dict[str, Any]:
    root, key = _repository(payload)
    state = payload.get("state")
    if not isinstance(state, dict) or not isinstance(state.get("tabs", []), list) or len(state.get("tabs", [])) > 30:
        raise commands.CommandRejected("workspace_state_invalid", "Keep at most 30 files open in a workspace.")
    for tab in state.get("tabs", []):
        if (not isinstance(tab, dict) or not isinstance(tab.get("path"), str)
                or not all(isinstance(tab.get(key), str) for key in ("content", "saved", "branch", "digest"))
                or not re.fullmatch(r"[0-9a-f]{64}", tab["digest"])
                or not isinstance(tab.get("settings"), dict)
                or not isinstance(tab.get("line"), int) or tab["line"] < 1):
            raise commands.CommandRejected("workspace_state_invalid", "A workspace file is missing its path.")
        _inside(root, tab["path"])
    if (not isinstance(state.get("overlays", []), list)
            or len(state.get("overlays", [])) > 32
            or not all(isinstance(value, str) for value in state.get("overlays", []))):
        raise commands.CommandRejected("workspace_state_invalid", "Choose at most 32 ticket overlays.")
    encoded = json.dumps(state, ensure_ascii=False)
    if len(encoded.encode()) > 7_000_000:
        raise commands.CommandRejected("workspace_state_large", "Save and close some drafts before restoring more workspace files.")
    db = _database()
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM workspaces WHERE id=?", (key,)).fetchone()
            revision = row[0] if row else 0
            if payload.get("revision") != revision:
                raise commands.CommandRejected("workspace_conflict", "This workspace is open in another window. Your drafts remain here; close the other window before reopening this workspace.")
            db.execute("INSERT INTO workspaces VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,state=excluded.state", (key, revision + 1, encoded))
    finally:
        db.close()
    return {"revision": revision + 1}
