"""Repository workspaces never mutate Git or leak drafts into Bunker sync."""
import unittest

import test_bunker_cutover as fixture_module
from test_bunker_cutover import git
from ticketbunker import app_worker


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.lab = fixture_module.BunkerCutoverTests()
        self.lab.setUp()
        self.addCleanup(self.lab.doCleanups)
        self.repo = self.lab.repo
        self.payload = {"bunker_id": self.lab.bid, "repository_id": self.lab.rid}

    def call(self, action, **payload):
        return app_worker.dispatch_request({"id": 1, "action": action, "payload": {**self.payload, **payload}})

    def test_browse_and_restore_draft_without_changing_worktree(self):
        opened = self.call("workspace.open")
        self.assertEqual(opened["branch"], "main")
        listing = self.call("workspace.tree")
        self.assertEqual([row["name"] for row in listing["entries"]], ["README.md"])
        original = (self.repo / "README.md").read_text()
        read = self.call("workspace.file.read", path="README.md")
        state = {"tabs": [{**read, "path": "README.md", "content": "unsaved draft", "saved": original}], "active": "README.md"}
        saved = self.call("workspace.save", revision=0, state=state)
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(self.call("workspace.open")["state"], state)
        self.assertEqual((self.repo / "README.md").read_text(), original)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        conflict = self.call("workspace.save", revision=0, state={})
        self.assertEqual(conflict["code"], "workspace_conflict")
        self.assertEqual(self.call("workspace.open")["state"], state)

    def test_boundaries_and_unattached_repository(self):
        for action in ("workspace.tree", "workspace.file.read"):
            for path in ("../", ".git/config", ".GIT/config", ".TB/private"):
                self.assertFalse(self.call(action, path=path)["ok"])
        self.assertFalse(self.call("workspace.open", repository_id="missing")["ok"])
        other = self.lab.fixture.root / "outside"
        other.mkdir()
        (other / "private.txt").write_text("private")
        try:
            (self.repo / "escape").symlink_to(other, target_is_directory=True)
        except OSError:
            return
        self.assertFalse(self.call("workspace.file.read", path="escape/private.txt")["ok"])
        self.assertNotIn("escape", [row["name"] for row in self.call("workspace.tree")["entries"]])

    def test_saves_refuse_external_edits_and_switched_branch(self):
        original = self.call("workspace.file.read", path="README.md")
        git(self.repo, "switch", "-c", "Ada/ECS-4-work")
        rejected = self.call("workspace.file.save", path="README.md", branch="main", content="draft", expected_digest=original["digest"])
        self.assertEqual(rejected["code"], "workspace_branch_changed")
        git(self.repo, "switch", "main")
        (self.repo / "README.md").write_text("external")
        rejected = self.call("workspace.file.save", path="README.md", branch="main", content="draft", expected_digest=original["digest"])
        self.assertEqual(rejected["code"], "source_changed")
        self.assertEqual((self.repo / "README.md").read_text(), "external")

    def test_workspace_identity_is_checkout_specific_and_save_works(self):
        original = self.call("workspace.open")
        other, rid = self.lab.second_repository()
        self.assertNotEqual(original["workspace_id"], self.call("workspace.open", repository_id=rid)["workspace_id"])
        read = self.call("workspace.file.read", path="README.md")
        result = self.call("workspace.file.save", path="README.md", branch="main", content="saved\n", expected_digest=read["digest"])
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.repo / "README.md").read_text(), "saved\n")
        self.assertNotEqual((other / "README.md").read_text(), "saved\n")

    def test_file_search_respects_ignores_and_workspace_schema(self):
        (self.repo / ".gitignore").write_text("ignored.py\n")
        (self.repo / "ignored.py").write_text("ignored")
        (self.repo / "searchable.py").write_text("visible")
        result = self.call("workspace.find", query=".py")
        self.assertEqual(result["paths"], ["searchable.py"])
        for state in ({"tabs": ["bad"]}, {"tabs": [{"path": "README.md"}]}, {"tabs": [], "overlays": [None]}):
            self.assertEqual(self.call("workspace.save", revision=0, state=state)["code"], "workspace_state_invalid")
        self.assertEqual(self.call("workspace.open")["revision"], 0)
