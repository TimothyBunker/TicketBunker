from __future__ import annotations

import datetime
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
            "label": "Not synthesized",
            "detail": f"{self.branch} has not been synthesized yet.",
            "action": "Synthesize",
        }
        with mock.patch.object(project, "refresh_source_refs", return_value=(True, "")):
            with mock.patch.object(autoagent, "enqueue") as enqueue:
                with mock.patch.object(truth, "synthesis_state", return_value=synthesis):
                    result = truth.command_refresh(
                        commands.CommandContext(self.root, transport="test"),
                        {"ticket": "TB-9"},
                    )

        self.assertEqual(result["companion"]["label"], "Local ticket data")
        self.assertEqual(result["git"]["label"], "Git refs fetched")
        self.assertEqual(result["synthesis"]["label"], "Not synthesized")
        self.assertIn("source worktree was not changed", result["git"]["detail"])
        enqueue.assert_not_called()

    def test_ref_change_updates_the_page_content_revision(self) -> None:
        ticket = TicketDatabase(self.root).get_ticket("TB-9", refresh_branches=True)
        before = truth._content_revision(self.root, ticket)
        git(self.root, "branch", "Lin/OTHER-2-unrelated")
        after = truth._content_revision(self.root, ticket)

        self.assertNotEqual(before, after)

    def test_synthesis_runs_only_after_the_user_requests_it(self) -> None:
        with mock.patch.object(
            autoagent,
            "enqueue",
            return_value={"queued": True, "branch": self.branch},
        ) as enqueue:
            result = truth.command_synthesize(
                commands.CommandContext(self.root, transport="test"),
                {"ticket": "TB-9"},
            )

        self.assertTrue(result["queued"])
        enqueue.assert_called_once_with(self.root, "user-requested-synthesis")

    def test_stopped_synthesis_exposes_retry_and_local_log(self) -> None:
        with mock.patch.object(
            autoagent,
            "read_state",
            return_value={
                "last": {
                    "status": "fail",
                    "events": ["user-requested-synthesis"],
                    "branch": self.branch,
                    "detail": "pi synthesis failed",
                }
            },
        ):
            ticket = TicketDatabase(self.root).get_ticket("TB-9", refresh_branches=True)
            value = truth.synthesis_state(self.root, ticket)

        self.assertEqual(value["label"], "Synthesis stopped")
        self.assertEqual(value["compact"], "Stopped")
        self.assertEqual(value["action"], "Retry synthesis")
        self.assertEqual(value["command"], "synthesize")
        self.assertEqual(value["log_url"], "/truth/agent-log")

    def test_legacy_automatic_failure_is_an_unsynthesized_state_not_user_failure(self) -> None:
        with mock.patch.object(
            autoagent,
            "read_state",
            return_value={
                "last": {
                    "status": "fail",
                    "events": ["head-advanced"],
                    "branch": self.branch,
                    "detail": "deterministic refresh failed",
                }
            },
        ):
            ticket = TicketDatabase(self.root).get_ticket("TB-9", refresh_branches=True)
            value = truth.synthesis_state(self.root, ticket)

        self.assertEqual(value["label"], "Not synthesized")
        self.assertEqual(value["action"], "Synthesize")
        self.assertNotIn("failed", value["detail"].casefold())

    def test_outdated_synthesis_names_age_and_commit_count(self) -> None:
        published = git(self.root, "rev-parse", "HEAD")
        verified_at = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
        ).isoformat(timespec="seconds")
        layout.child_report(self.bunker, self.branch).write_text(
            f"""<!-- tb:verified branch={self.branch} commit={published} ts={verified_at} -->
## Who

- [observation] Ada owns the branch.

## What

- [observation] The ticket has a report.

## Where

- [observation] source.py changed.

## Why

- [observation] The ticket requests it.

## State

- [observation] Work is in progress.
""",
            encoding="utf-8",
        )
        (self.root / "source.py").write_text("VALUE = 2\n", encoding="utf-8")
        git(self.root, "add", "source.py")
        git(self.root, "commit", "-m", "Change ticket work")

        ticket = TicketDatabase(self.root).get_ticket("TB-9", refresh_branches=True)
        with mock.patch.object(
            autoagent,
            "synthesis_input_state",
            return_value={"status": "changed", "published_at": verified_at},
        ):
            value = truth.synthesis_state(self.root, ticket)

        self.assertEqual(value["label"], "Synthesis needs update")
        self.assertEqual(value["changes"], 1)
        self.assertEqual(value["compact"], "Updated 2 hours ago")
        self.assertIn("Updated 2 hours ago", value["detail"])
        self.assertIn("1 commit since", value["detail"])

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
