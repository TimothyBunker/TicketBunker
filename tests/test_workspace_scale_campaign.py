"""Bounded explorer and recovery identities on disposable files only."""
import copy
import unittest

import test_bunker_cutover as fixture_module
from ticketbunker import app_worker, workspace


class WorkspaceScaleCampaignTests(unittest.TestCase):
    def test_explorer_truncation_means_an_additional_entry_exists(self):
        lab = fixture_module.BunkerCutoverTests()
        lab.setUp()
        self.addCleanup(lab.doCleanups)
        folder = lab.repo / "many files"
        folder.mkdir()
        for index in range(1000):
            (folder / f"file-{index:04}.py").write_bytes(b"# dummy\n")
        def listing():
            return app_worker.dispatch_request({"id": 1, "action": "workspace.tree", "payload": {
                "bunker_id": lab.bid, "repository_id": lab.rid, "path": "many files",
            }})
        result = listing()
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["entries"]), 1000)
        self.assertFalse(result["truncated"], "Exactly 1,000 entries is not truncated")
        (folder / "one-more.py").write_bytes(b"# extra\n")
        result = listing()
        self.assertEqual(len(result["entries"]), 1000)
        self.assertTrue(result["truncated"])

    def test_duplicate_recovery_paths_do_not_hide_another_draft(self):
        draft = {"path": "file.py", "content": "first", "saved": "", "branch": "main", "digest": "a" * 64, "line": 1, "settings": {}}
        for alias in ("file.py", "./file.py", "dir/../file.py"):
            other = {**copy.deepcopy(draft), "path": alias, "content": "second"}
            with self.subTest(alias=alias):
                self.assertFalse(workspace._valid_state_shape({"tabs": [draft, other]}))


if __name__ == "__main__":
    unittest.main()
