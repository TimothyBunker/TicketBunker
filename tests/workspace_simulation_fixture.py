"""Disposable two-repository product lab; no user data or network credentials.

Run through desktop/tests/workspace-simulation.e2e.mjs, or keep this process open
and use its printed URL manually. Enter/newline shuts down and removes the lab.
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
from pathlib import Path
import tempfile
import threading
from unittest import mock

from test_bunkerruntime import git
from ticketbunker import app_server, bunkerdb, commands, config


ENGINE = """\"\"\"Grid Garden: move an entity inside the playfield.\"\"\"


def move(position, delta, width):
    # LAB-1: the right edge incorrectly permits an off-grid coordinate.
    return min(width, max(0, position + delta))
"""
RENDERER = """\"\"\"Grid Garden: human-readable position labels.\"\"\"


def label(position):
    # LAB-2: labels should be one-based for players.
    return f\"Cell {position}\"
"""


def main():
    with tempfile.TemporaryDirectory(prefix="tb-workspace-simulation-") as temporary, contextlib.ExitStack() as stack:
        def terminate(_signal, _frame):
            raise SystemExit(0)
        previous = signal.signal(signal.SIGTERM, terminate)
        stack.callback(signal.signal, signal.SIGTERM, previous)
        root = Path(temporary).resolve()
        settings = root / "settings.json"
        settings.write_text(json.dumps({"credentials_file": str(root / "isolated-tracker-credentials.env")}) + "\n", encoding="utf-8")
        stack.enter_context(mock.patch.dict(os.environ, {
            "TB_DATA_HOME": str(root / "data"), "TB_CONFIG": str(settings),
            "TB_AGENT_DISABLE": "1", "TB_AGENT_HARNESS": "off",
            "TB_AGENT_COMMAND": "", "TB_AUTO_AGENT": "0",
            "PI_CODING_AGENT_DIR": str(root / "isolated-pi"),
            "GIT_CONFIG_GLOBAL": str(root / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
        }))
        stack.enter_context(mock.patch.object(config, "GLOBAL_CONFIG", settings))
        # Prevent inherited tracker/model secrets from entering a disposable lab.
        for key in tuple(os.environ):
            if any(name in key.upper() for name in ("TOKEN", "SECRET", "API_KEY")):
                os.environ.pop(key, None)
        bunker = bunkerdb.create_bunker("Grid Garden Lab", project_key="LAB", project_name="Grid Garden")
        bid, pid = bunker["id"], bunker["projects"][0]["id"]
        cfg = bunkerdb.get_configuration(bid, project_id=pid)
        cfg = bunkerdb.replace_repository_bindings(bid, project_id=pid,
            expected_revision=cfg["repositories"]["revision"], repositories=[
                {"name": name, "canonical_remote": f"github.com/tb-simulation/{slug}", "default_branch": "main"}
                for name, slug in (("Garden engine", "engine"), ("Garden renderer", "renderer"))])
        repositories = {}
        for binding in cfg["repositories"]["items"]:
            slug = binding["canonical_remote"].split("/")[-1]
            repo = root / slug
            (repo / "src").mkdir(parents=True)
            (repo / "docs").mkdir()
            (repo / "README.md").write_text(f"# Grid Garden {slug}\n\nDisposable simulation repository.\n", encoding="utf-8")
            (repo / "src" / "grid.py").write_text(ENGINE if slug == "engine" else RENDERER, encoding="utf-8")
            (repo / "test_grid.py").write_text(
                "import unittest\nfrom src.grid import " + ("move" if slug == "engine" else "label") + "\n\n"
                "class GridTests(unittest.TestCase):\n"
                "    def test_player_boundary(self):\n" +
                ("        self.assertEqual(move(4, 1, 5), 4)\n" if slug == "engine" else "        self.assertEqual(label(0), 'Cell 1')\n"), encoding="utf-8")
            (repo / "docs" / "design.md").write_text("# Coordinate contract\n\nEngine coordinates are zero-based; player labels are one-based.\n", encoding="utf-8")
            (repo / ".gitignore").write_text("cache/\n__pycache__/\n", encoding="utf-8")
            (repo / "cache").mkdir()
            (repo / "cache" / "ignored.txt").write_text("Not a quick-search result.\n", encoding="utf-8")
            git(repo, "init", "-b", "main")
            git(repo, "config", "user.name", "Simulation QA")
            git(repo, "config", "user.email", "qa@example.test")
            git(repo, "add", ".")
            git(repo, "commit", "-m", "Seed Grid Garden simulation")
            bare = root / f"{slug}.git"
            git(repo, "clone", "--quiet", "--bare", str(repo), str(bare))
            remote_url = f"https://github.com/tb-simulation/{slug}.git"
            git(repo, "remote", "add", "origin", remote_url)
            git(repo, "config", f"url.{bare}.insteadOf", remote_url)
            bunkerdb.attach_repository(bid, project_id=pid, binding_id=binding["id"], local_path=str(repo))
            git(repo, "switch", "-c", f"qa/LAB-{'1' if slug == 'engine' else '2'}-grid")
            repositories[slug] = str(repo)
        change = bunkerdb.propose_ticket_sync(bid, project_id=pid, source_kind="manual", tickets=[
            {"id": "LAB-1", "title": "Keep movement inside the garden", "description": "Fix the upper bound in `src/grid.py:6`. Inspect `docs/design.md:3` first.", "type": "bug", "status": "in-progress"},
            {"id": "LAB-2", "title": "Use player-friendly coordinate labels", "description": "Renderer labels in `src/grid.py:6` must start at one.", "type": "task", "status": "todo"},
            {"id": "LAB-3", "title": "Document the coordinate contract", "description": "Compare both repositories against `docs/design.md:3`.", "type": "task", "status": "todo"},
        ])
        bunkerdb.apply_ticket_sync_change(bid, change)
        repo = Path(repositories["engine"])
        commands.dispatch("application.profile.update", {"name": "Simulation QA", "email": "qa@example.test", "branch_user": "qa"}, commands.CommandContext(repo, transport="desktop"))
        server = app_server.serve(repo, 0, launch_id="workspace-simulation")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "root": str(root), "repositories": repositories}), flush=True)
            try:
                input()
            except EOFError:
                pass
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
