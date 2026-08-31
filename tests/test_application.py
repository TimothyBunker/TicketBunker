from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import application, commands, config


class ApplicationStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.settings = self.root / ".tbconfig"
        subprocess.run(["git", "init", "-q", str(self.root / "source")], check=True)
        self.source = self.root / "source"
        self.config_patch = mock.patch.object(config, "GLOBAL_CONFIG", self.settings)
        self.config_patch.start()

    def tearDown(self) -> None:
        self.config_patch.stop()
        self.tempdir.cleanup()

    def test_state_defaults_the_local_profile_from_git(self) -> None:
        with mock.patch.object(
            application,
            "_git_value",
            side_effect=lambda key: {"user.name": "Ada Lovelace", "user.email": "ada@example.test"}[key],
        ):
            value = application.state()

        self.assertEqual(value["profile"]["name"], "Ada Lovelace")
        self.assertEqual(value["profile"]["branch_user"], "Ada-Lovelace")
        self.assertTrue(value["profile"]["complete"])

    def test_profile_update_is_machine_local_and_validated(self) -> None:
        result = commands.dispatch(
            "application.profile.update",
            {"name": "Ada", "email": "ada@example.test", "branch_user": "ada dev"},
            commands.CommandContext(self.root, transport="desktop"),
        )

        self.assertEqual(result["profile"]["branch_user"], "ada-dev")
        saved = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(saved["profile"]["email"], "ada@example.test")

    def test_add_project_normalizes_the_git_root_and_deduplicates(self) -> None:
        nested = self.source / "src"
        nested.mkdir()
        context = commands.CommandContext(self.root, transport="desktop")
        first = commands.dispatch(
            "application.project.add", {"path": str(nested)}, context
        )
        second = commands.dispatch(
            "application.project.add", {"path": str(self.source)}, context
        )

        self.assertEqual(first["project"]["path"], str(self.source))
        self.assertFalse(first["project"]["initialized"])
        self.assertEqual(len(second["projects"]), 1)

    def test_non_git_directory_is_rejected_actionably(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        with self.assertRaisesRegex(commands.CommandRejected, "Git working tree"):
            application.inspect_project(str(outside))


if __name__ == "__main__":
    unittest.main()
