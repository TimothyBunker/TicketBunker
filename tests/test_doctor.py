from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import commands, config, doctor, project


def run_git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.source = base / "source"
        self.remote = base / "ticketbunker.git"
        self.source.mkdir()
        run_git(self.source, "init", "-b", "ecs")
        run_git(self.source, "config", "user.name", "Ada")
        run_git(self.source, "config", "user.email", "ada@example.com")
        run_git(self.source, "commit", "--allow-empty", "-m", "initial")
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        self.old_config = config.GLOBAL_CONFIG
        config.GLOBAL_CONFIG = base / "tbconfig.json"
        config.save({"credentials_file": str(base / "credentials.env")})

    def tearDown(self) -> None:
        config.GLOBAL_CONFIG = self.old_config
        self.tmp.cleanup()

    def initialize(self) -> None:
        commands.dispatch(
            "project.initialize",
            {"project": "ECS", "bunker_repo": str(self.remote), "base_branch": "ecs"},
            commands.CommandContext(self.source),
        )

    def test_uninitialized_report_points_directly_to_init(self) -> None:
        result = doctor.report(self.source, check_remote=False)

        locator = next(value for value in result["checks"] if value["key"] == "locator")
        self.assertFalse(result["ready"])
        self.assertEqual(locator["status"], "fail")
        self.assertEqual(locator["action"], "tb init")

    def test_initialized_but_uncommitted_locator_is_not_ready(self) -> None:
        self.initialize()

        result = doctor.report(self.source)

        source_contract = next(value for value in result["checks"] if value["key"] == "source_contract")
        remote = next(value for value in result["checks"] if value["key"] == "remote")
        self.assertFalse(result["ready"])
        self.assertEqual(source_contract["status"], "fail")
        self.assertIn("git add .ticketbunker.json .gitignore", source_contract["action"])
        self.assertEqual(remote["status"], "pass")

    def test_committed_setup_is_ready_with_optional_notes(self) -> None:
        self.initialize()
        run_git(self.source, "add", project.LOCATOR_FILE, ".gitignore")
        run_git(self.source, "commit", "-m", "Connect TicketBunker")
        data_before = run_git(project.data_checkout(self.source), "rev-parse", "HEAD")

        result = doctor.report(self.source)

        data_after = run_git(project.data_checkout(self.source), "rev-parse", "HEAD")
        self.assertTrue(result["ready"])
        self.assertEqual(result["failures"], 0)
        self.assertEqual(data_after, data_before)
        self.assertEqual(
            next(value for value in result["checks"] if value["key"] == "index")["status"],
            "pass",
        )
        credentials = next(value for value in result["checks"] if value["key"] == "credentials")
        self.assertNotIn("TOKEN", credentials["summary"])

    def test_unreachable_remote_is_an_actionable_failure(self) -> None:
        self.initialize()
        run_git(self.source, "add", project.LOCATOR_FILE, ".gitignore")
        run_git(self.source, "commit", "-m", "Connect TicketBunker")
        locator = project.load_locator(self.source)
        locator["bunker_repository"] = str(Path(self.tmp.name) / "missing.git")
        (self.source / project.LOCATOR_FILE).write_text(json.dumps(locator), encoding="utf-8")

        result = doctor.report(self.source)

        remote = next(value for value in result["checks"] if value["key"] == "remote")
        self.assertFalse(result["ready"])
        self.assertEqual(remote["status"], "fail")
        self.assertIn("repository URL and access grant", remote["action"])
        self.assertIn("tb init", remote["action"])

    def test_legacy_automatic_failure_is_not_called_a_requested_synthesis(self) -> None:
        self.initialize()
        run_git(self.source, "add", project.LOCATOR_FILE, ".gitignore")
        run_git(self.source, "commit", "-m", "Connect TicketBunker")
        automatic = {
            "ready": True,
            "installed": list(doctor.autoagent.HOOK_NAMES),
            "missing": [],
            "state": {
                "last": {
                    "status": "fail",
                    "events": ["head-advanced"],
                    "detail": "deterministic refresh failed",
                }
            },
        }

        with mock.patch.object(doctor.autoagent, "hook_status", return_value=automatic):
            result = doctor.report(self.source)

        synthesis = next(value for value in result["checks"] if value["key"] == "hooks")
        self.assertEqual(synthesis["status"], "pass")
        self.assertIn("No user-requested synthesis has run", synthesis["summary"])
        self.assertNotIn("failed", synthesis["summary"].casefold())

    def test_explicit_synthesis_failure_remains_actionable(self) -> None:
        self.initialize()
        run_git(self.source, "add", project.LOCATOR_FILE, ".gitignore")
        run_git(self.source, "commit", "-m", "Connect TicketBunker")
        automatic = {
            "ready": True,
            "installed": list(doctor.autoagent.HOOK_NAMES),
            "missing": [],
            "state": {
                "last": {
                    "status": "fail",
                    "events": ["user-requested-synthesis"],
                    "detail": "configured base is unavailable",
                },
                "log": "/tmp/agent.log",
            },
        }

        with mock.patch.object(doctor.autoagent, "hook_status", return_value=automatic):
            result = doctor.report(self.source)

        synthesis = next(value for value in result["checks"] if value["key"] == "hooks")
        self.assertEqual(synthesis["status"], "warn")
        self.assertIn("last requested synthesis failed", synthesis["summary"])
        self.assertIn("/tmp/agent.log", synthesis["action"])


if __name__ == "__main__":
    unittest.main()
