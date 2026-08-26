from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import autoagent, commands, layout, project, truth
from ticketbunker.api import TicketDatabase


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "TB_AGENT_DISABLE": "1"},
    )
    return completed.stdout.strip()


class SharedTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Ada")
        git(self.root, "config", "user.email", "ada@example.com")
        (self.root / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(self.root, "add", "source.py")
        git(self.root, "commit", "-m", "Initial source")
        self.branch = "Ada/TB-9-refresh"
        git(self.root, "switch", "-c", self.branch)

        self.bunker = layout.bunker_path(self.root, "TB-9")
        layout.init_dirs(self.bunker)
        paths = layout.paths(self.bunker)
        paths["ticket"].write_text(
            json.dumps(
                {
                    "id": "TB-9",
                    "key": "TB-9",
                    "title": "Refresh shared truth",
                    "status": "in-progress",
                    "repo": str(self.root),
                    "assigned_branches": [self.branch],
                }
            ),
            encoding="utf-8",
        )
        paths["branch_resolution"].write_text(
            json.dumps({"branches": [self.branch], "assigned_missing": [], "match_rules": {}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_open_snapshot_reads_cached_refs_without_network(self) -> None:
        with mock.patch.object(
            project,
            "refresh_source_refs",
            side_effect=AssertionError("ticket query attempted network I/O"),
        ) as refresh:
            ticket = TicketDatabase(self.root).get_ticket("TB-9", refresh_branches=True)

        refresh.assert_not_called()
        self.assertEqual(ticket.branches[0].name, self.branch)
        self.assertEqual(ticket.branches[0].state, "comparison-missing")
        self.assertFalse((self.root / ".tb" / "ticket-open.jsonl").exists())
        self.assertTrue((self.root / ".git" / "ticketbunker" / "ticket-open.jsonl").is_file())

    def test_refresh_names_the_companion_git_and_synthesis_state(self) -> None:
        synthesis = {
            "status": "missing",
            "label": "No synthesis published",
            "detail": f"{self.branch} has no accepted report yet.",
            "action": "Generate synthesis",
        }
        with mock.patch.object(project, "refresh_source_refs", return_value=(True, "")):
            with mock.patch.object(truth, "_synthesis_queue", return_value={"queued": True}):
                with mock.patch.object(truth, "synthesis_state", return_value=synthesis):
                    result = truth.command_refresh(
                        commands.CommandContext(self.root, transport="test"),
                        {"ticket": "TB-9"},
                    )

        self.assertEqual(result["companion"]["label"], "Local ticket data")
        self.assertEqual(result["git"]["label"], "Git refs fetched")
        self.assertEqual(result["synthesis"]["label"], "No synthesis published")
        self.assertIn("source worktree was not changed", result["git"]["detail"])

    def test_ref_change_updates_the_page_content_revision(self) -> None:
        ticket = TicketDatabase(self.root).get_ticket("TB-9", refresh_branches=True)
        before = truth._content_revision(self.root, ticket)
        git(self.root, "branch", "Lin/OTHER-2-unrelated")
        after = truth._content_revision(self.root, ticket)

        self.assertNotEqual(before, after)

    def test_changed_refs_queue_refresh_even_when_the_last_inputs_were_current(self) -> None:
        ticket = TicketDatabase(self.root).get_ticket("TB-9", refresh_branches=True)
        with mock.patch.object(
            autoagent,
            "synthesis_input_state",
            return_value={"status": "current"},
        ):
            with mock.patch.object(
                autoagent,
                "enqueue",
                return_value={"queued": True, "branch": self.branch},
            ) as enqueue:
                result = truth._synthesis_queue(self.root, ticket, force=True)

        self.assertTrue(result["queued"])
        enqueue.assert_called_once_with(self.root, "ticket-refs-changed")

    def test_background_failure_hides_raw_exception_detail(self) -> None:
        secret = "BrokenPipeError: internal transport detail"
        with mock.patch.object(commands, "dispatch", side_effect=RuntimeError(secret)):
            truth._background(self.root, "", "TB-9")

        value = truth.state(self.root, "", "TB-9")
        self.assertEqual(value["refresh"], "failed")
        self.assertNotIn("BrokenPipeError", value["companion"]["detail"])
        self.assertIn("background refresh stopped", value["companion"]["detail"].casefold())
        self.assertIn("tb doctor", value["companion"]["action"])


if __name__ == "__main__":
    unittest.main()
