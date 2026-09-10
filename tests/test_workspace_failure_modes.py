"""Disposable failure injection for checkout files and local recovery storage."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_bunker_cutover as fixture_module
from test_bunker_cutover import git
from ticketbunker import app_worker, bunkerdb, commands, editor, platform_services, workspace


class WorkspaceFailureModesTests(unittest.TestCase):
    def setUp(self):
        self.lab = fixture_module.BunkerCutoverTests()
        self.lab.setUp()
        self.addCleanup(self.lab.doCleanups)
        self.repo = self.lab.repo
        self.payload = {"bunker_id": self.lab.bid, "repository_id": self.lab.rid}

    def call(self, action, **payload):
        return app_worker.dispatch_request({"id": 1, "action": action, "payload": {**self.payload, **payload}})

    def recovery_path(self):
        return platform_services.current().data_home() / "workspaces.sqlite3"

    def state(self, content="private draft"):
        read = self.call("workspace.file.read", path="README.md")
        return {"tabs": [{**read, "saved": read["content"], "content": content}], "active": "README.md"}

    def test_surrogate_source_draft_is_rejected_without_internal_error_or_write(self):
        read = self.call("workspace.file.read", path="README.md")
        result = self.call("workspace.file.save", path="README.md", branch="main", content="bad\ud800draft", expected_digest=read["digest"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "source_encoding")
        self.assertEqual((self.repo / "README.md").read_text(), read["content"])
        self.assertEqual(list(self.repo.glob(".README.md.tb-*")), [])

    def test_surrogate_recovery_draft_cannot_replace_valid_recovery(self):
        self.assertEqual(self.call("workspace.save", revision=0, state=self.state())["revision"], 1)
        result = self.call("workspace.save", revision=1, state=self.state("bad\udffftext"))
        self.assertEqual(result["code"], "workspace_state_invalid")
        self.assertEqual(self.call("workspace.open")["state"]["tabs"][0]["content"], "private draft")

    def test_broken_recovery_database_is_preserved_and_source_stays_accessible(self):
        path = self.recovery_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        original = b"not a SQLite database: private recovery remains here"
        path.write_bytes(original)
        for action, payload in (("workspace.open", {}), ("workspace.save", {"revision": 0, "state": {}})):
            result = self.call(action, **payload)
            self.assertEqual(result["code"], "workspace_recovery_unavailable")
            self.assertIn("copy", result["message"].lower())
            self.assertEqual(path.read_bytes(), original)
        self.assertTrue(self.call("workspace.file.read", path="README.md")["ok"])

    def test_invalid_recovery_json_is_not_silently_discarded(self):
        opened = self.call("workspace.open")
        with sqlite3.connect(self.recovery_path()) as db:
            db.execute("INSERT INTO workspaces VALUES (?,?,?)", (opened["workspace_id"], 3, "broken { private draft"))
        result = self.call("workspace.open")
        self.assertEqual(result["code"], "workspace_recovery_unavailable")
        with sqlite3.connect(self.recovery_path()) as db:
            self.assertEqual(db.execute("SELECT revision,state FROM workspaces").fetchone(), (3, "broken { private draft"))

    def test_valid_json_with_wrong_recovery_shape_is_not_returned_to_ui(self):
        opened = self.call("workspace.open")
        for raw in ("null", "[]", '{"tabs": "not a list"}', '{"tabs": ["broken file"]}', '{"tabs": [{"path": "README.md"}]}', '{"overlays": "bad"}', '{"expanded": [null]}'):
            with self.subTest(raw=raw):
                with sqlite3.connect(self.recovery_path()) as db:
                    db.execute("INSERT OR REPLACE INTO workspaces VALUES (?,?,?)", (opened["workspace_id"], 1, raw))
                self.assertEqual(self.call("workspace.open")["code"], "workspace_recovery_unavailable")
                with sqlite3.connect(self.recovery_path()) as db:
                    self.assertEqual(db.execute("SELECT state FROM workspaces").fetchone()[0], raw)
        with sqlite3.connect(self.recovery_path()) as db:
            db.execute("UPDATE workspaces SET state='{}'")
        self.assertEqual(self.call("workspace.open")["state"], {})

    def test_invalid_workspace_paths_are_actionable(self):
        for path in ("bad\x00path", "bad\ud800path"):
            for action in ("workspace.tree", "workspace.file.read"):
                with self.subTest(path=repr(path), action=action):
                    result = self.call(action, path=path)
                    self.assertEqual(result["code"], "workspace_path_invalid")

    def test_unreadable_explorer_has_actionable_error(self):
        with patch.object(workspace.os, "scandir", side_effect=PermissionError("simulation directory denied")):
            self.assertEqual(self.call("workspace.tree")["code"], "workspace_directory_unavailable")

    def test_unborn_checkout_has_empty_head_not_git_error_text(self):
        original_git = self.repo / ".git"
        original_git.rename(self.repo / "original-git-metadata")
        git(self.repo, "init", "-b", "main")
        result = self.call("workspace.open")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["branch"], "main")
        self.assertEqual(result["head"], "")

    def test_moved_folder_reports_reconnect_without_losing_old_recovery(self):
        original = self.call("workspace.open")
        state = self.state()
        self.call("workspace.save", revision=0, state=state)
        moved = self.lab.fixture.root / "moved-checkout"
        self.repo.rename(moved)
        self.assertEqual(self.call("workspace.open")["code"], "workspace_folder_missing")
        with patch.object(bunkerdb, "_fetch_repository"):
            bunkerdb.attach_repository(self.lab.bid, project_id=self.lab.pid, binding_id=self.lab.rid, local_path=str(moved))
        self.assertEqual(self.call("workspace.open")["root"], str(moved))
        with sqlite3.connect(self.recovery_path()) as db:
            old = db.execute("SELECT state FROM workspaces WHERE id=?", (original["workspace_id"],)).fetchone()
        self.assertEqual(json.loads(old[0]), state)

    def test_locked_recovery_reports_actionable_error_without_losing_snapshot(self):
        state = self.state()
        self.call("workspace.save", revision=0, state=state)
        locked = sqlite3.connect(self.recovery_path())
        self.addCleanup(locked.close)
        locked.execute("BEGIN EXCLUSIVE")
        original_connect = sqlite3.connect
        def quick_timeout(*args, **kwargs):
            kwargs["timeout"] = 0.01
            return original_connect(*args, **kwargs)
        # Bound only this injected test's waiting; retain real SQLite locking.
        with patch.object(workspace.sqlite3, "connect", side_effect=quick_timeout):
            result = self.call("workspace.save", revision=1, state={})
        self.assertEqual(result["code"], "workspace_recovery_unavailable")
        locked.rollback()
        self.assertEqual(self.call("workspace.open")["state"], state)

    def test_parent_redirected_to_external_directory_during_save_is_rejected(self):
        folder = self.repo / "nested"
        folder.mkdir()
        original = b"same bytes\n"
        (folder / "source.py").write_bytes(original)
        outside = self.lab.fixture.root / "external"
        outside.mkdir()
        (outside / "source.py").write_bytes(original)
        probe = self.repo / "symlink-probe"
        try:
            probe.symlink_to(outside, target_is_directory=True)
            probe.unlink()
        except OSError:
            self.skipTest("OS does not permit directory symlinks")
        read = editor.read_source(self.repo, "nested/source.py")
        settings = editor.local_state(self.repo)
        def move_parent(_root):
            folder.rename(self.repo / "saved-nested")
            folder.symlink_to(outside, target_is_directory=True)
            return settings
        with patch.object(editor, "local_state", side_effect=move_parent):
            with self.assertRaises(commands.CommandRejected):
                editor.save_source(self.repo, "nested/source.py", "private replacement\n", read["digest"])
        self.assertEqual((outside / "source.py").read_bytes(), original)
        self.assertEqual((self.repo / "saved-nested" / "source.py").read_bytes(), original)
        self.assertEqual(list(outside.glob(".source.py.tb-*")), [])

    def test_posix_renamed_parent_cleanup_uses_original_directory(self):
        import os
        if not hasattr(os, "O_DIRECTORY") or os.open not in os.supports_dir_fd:
            self.skipTest("Held directory descriptors are not supported on this OS")
        folder = self.repo / "nested"
        folder.mkdir()
        original = b"same bytes\n"
        (folder / "source.py").write_bytes(original)
        outside = self.lab.fixture.root / "outside-late"
        outside.mkdir()
        (outside / "source.py").write_bytes(original)
        read = editor.read_source(self.repo, "nested/source.py")
        original_read = editor._source_bytes
        calls = 0
        def swap_after_staging(source):
            nonlocal calls
            calls += 1
            value = original_read(source)
            if calls == 2:
                folder.rename(self.repo / "renamed-nested")
                folder.symlink_to(outside, target_is_directory=True)
            return value
        with patch.object(editor, "_source_bytes", side_effect=swap_after_staging):
            with self.assertRaises(commands.CommandRejected):
                editor.save_source(self.repo, "nested/source.py", "private draft", read["digest"])
        self.assertEqual((outside / "source.py").read_bytes(), original)
        self.assertEqual(list((self.repo / "renamed-nested").glob(".source.py.tb-*")), [])
        self.assertEqual(list(outside.glob(".source.py.tb-*")), [])

    def test_detached_head_is_explicit_and_old_named_branch_draft_is_rejected(self):
        read = self.call("workspace.file.read", path="README.md")
        git(self.repo, "checkout", "--detach", "HEAD")
        opened = self.call("workspace.open")
        self.assertEqual(opened["branch"], "")
        self.assertEqual(opened["head"], git(self.repo, "rev-parse", "HEAD"))
        result = self.call("workspace.file.save", path="README.md", branch="main", content="old branch draft", expected_digest=read["digest"])
        self.assertEqual(result["code"], "workspace_branch_changed")

    def test_linked_git_worktree_has_its_own_recovery_and_rebind_guard(self):
        original = self.call("workspace.open")
        state = self.state()
        self.call("workspace.save", revision=0, state=state)
        other = self.lab.fixture.root / "linked-worktree"
        git(self.repo, "worktree", "add", "-b", "Simulation/ECS-4-worktree", str(other))
        with patch.object(bunkerdb, "_fetch_repository"):
            bunkerdb.attach_repository(self.lab.bid, project_id=self.lab.pid, binding_id=self.lab.rid, local_path=str(other))
        opened = self.call("workspace.open")
        self.assertNotEqual(opened["workspace_id"], original["workspace_id"])
        self.assertEqual(opened["branch"], "Simulation/ECS-4-worktree")
        self.assertEqual(opened["state"], {})
        self.assertNotIn(".git", [row["name"] for row in self.call("workspace.tree")["entries"]])
        result = self.call("workspace.save", revision=1, expected_root=str(self.repo), state={})
        self.assertEqual(result["code"], "workspace_checkout_replaced")
        with sqlite3.connect(self.recovery_path()) as db:
            old = db.execute("SELECT state FROM workspaces WHERE id=?", (original["workspace_id"],)).fetchone()
        self.assertEqual(json.loads(old[0]), state)

    def test_source_save_lost_receipt_does_not_reapply_over_newer_content(self):
        read = self.call("workspace.file.read", path="README.md")
        payload = {"path": "README.md", "branch": "main", "content": "accepted", "expected_digest": read["digest"]}
        first = self.call("workspace.file.save", **payload)
        self.assertTrue(first["ok"])
        retry = self.call("workspace.file.save", **payload)
        self.assertEqual(retry["code"], "source_changed")
        self.assertEqual((self.repo / "README.md").read_text(), "accepted")
        (self.repo / "README.md").write_text("external follow-up", encoding="utf-8")
        self.assertEqual(self.call("workspace.file.save", **payload)["code"], "source_changed")
        self.assertEqual((self.repo / "README.md").read_text(), "external follow-up")


if __name__ == "__main__":
    unittest.main()
