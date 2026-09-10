"""Folder picker uses disposable directories and never attaches a repository."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ticketbunker import app_worker


class DirectoryBrowserTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tb-directory-browser-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        home = patch("ticketbunker.application.Path.home", return_value=self.root)
        home.start()
        self.addCleanup(home.stop)

    def listing(self, **payload):
        return app_worker.dispatch_request({"id": 1, "action": "application.directory.list", "payload": payload})

    def test_only_visible_directories_sorted_and_no_mutation(self):
        for name in ("zebra", "Alpha", ".hidden", "space project"):
            (self.root / name).mkdir()
        (self.root / "README.md").write_text("simulation", encoding="utf-8")
        before = set(self.root.iterdir())
        result = self.listing(path=str(self.root))
        self.assertTrue(result["ok"], result)
        self.assertEqual([entry["name"] for entry in result["directories"]], ["Alpha", "space project", "zebra"])
        self.assertEqual(result["path"], str(self.root))
        self.assertEqual(result["parent"], str(self.root.parent))
        self.assertIn(str(self.root), result["locations"])
        self.assertIn(self.root.anchor, result["locations"])
        self.assertFalse(result["truncated"])
        self.assertEqual(set(self.root.iterdir()), before)

    def test_initial_location_and_nested_navigation(self):
        child = self.root / "child"
        child.mkdir()
        self.assertEqual(self.listing()["path"], str(self.root))
        self.assertEqual(self.listing(path="")["path"], str(self.root))
        self.assertEqual(self.listing(path=str(child))["parent"], str(self.root))
        self.assertEqual(self.listing(path=str(child / ".."))["path"], str(self.root))

    def test_missing_file_malformed_and_permission_errors_are_actionable(self):
        file = self.root / "file.txt"
        file.write_text("simulation", encoding="utf-8")
        for path in (str(self.root / "missing"), str(file), "bad\0path"):
            with self.subTest(path=path):
                result = self.listing(path=path)
                self.assertFalse(result["ok"])
                self.assertEqual(result["code"], "directory_unavailable")
                self.assertIn("Choose another folder", result["message"])
        self.assertEqual(self.listing(path=["not", "a", "path"])["code"], "directory_invalid")
        for path in ([], False, 0, None):
            self.assertEqual(self.listing(path=path)["code"], "directory_invalid")
        with patch("ticketbunker.application.os.scandir", side_effect=PermissionError("simulation denied")):
            self.assertEqual(self.listing(path=str(self.root))["code"], "directory_unavailable")

    def test_large_listing_is_bounded(self):
        for index in range(2001):
            (self.root / f"folder-{index:04}").mkdir()
        result = self.listing(path=str(self.root))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["directories"]), 2000)
        self.assertEqual(result["directories"], sorted(result["directories"], key=lambda entry: entry["name"].casefold()))


if __name__ == "__main__":
    unittest.main()
