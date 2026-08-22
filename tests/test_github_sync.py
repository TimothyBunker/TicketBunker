from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ticketbunker import commands, project
from ticketbunker.integrations import github


def run_git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


class GitHubSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.source = base / "source"
        self.remote = base / "ticketbunker.git"
        self.fixture = base / "github.json"
        self.source.mkdir()
        run_git(self.source, "init", "-b", "ecs")
        run_git(self.source, "config", "user.name", "Tim")
        run_git(self.source, "config", "user.email", "tim@example.com")
        run_git(self.source, "commit", "--allow-empty", "-m", "initial")
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        commands.dispatch(
            "project.initialize",
            {"project": "ECS", "bunker_repo": str(self.remote), "base_branch": "ecs"},
            commands.CommandContext(self.source),
        )
        self.write_fixture()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def issue(number: int, status: str = "Todo") -> dict:
        return {
            "issue": {
                "node_id": f"I_{number}",
                "number": number,
                "title": f"ECS-{number} Mock ticket {number}",
                "body": f"Description {number}",
                "state": "open",
                "html_url": f"https://github.com/example/voxel/issues/{number}",
                "created_at": "2026-08-20T12:00:00Z",
                "updated_at": "2026-08-21T12:00:00Z",
                "user": {"login": "Tim"},
                "assignee": {"login": "Ada"},
                "assignees": [{"login": "Ada"}],
                "labels": [{"name": "ecs"}],
            },
            "comments": [],
            "project": {
                "owner": "example",
                "number": 8,
                "title": "Voxel ECS",
                "url": "https://github.com/users/example/projects/8",
                "item_id": f"PVTI_{number}",
                "fields": {"Status": status},
            },
        }

    def write_fixture(self, items: list[dict] | None = None) -> None:
        self.fixture.write_text(
            json.dumps({"items": items or [self.issue(4), self.issue(5)]}),
            encoding="utf-8",
        )

    def test_fixture_and_live_transports_share_idempotent_sync_path(self) -> None:
        first = github.sync(self.source, fixture=self.fixture)
        before = run_git(project.data_checkout(self.source), "rev-parse", "HEAD")

        second = github.sync(self.source, fixture=self.fixture)
        after = run_git(project.data_checkout(self.source), "rev-parse", "HEAD")

        self.assertEqual((first["created"], first["updated"], first["unchanged"]), (2, 0, 0))
        self.assertEqual((second["created"], second["updated"], second["unchanged"]), (0, 0, 2))
        self.assertEqual(after, before)
        index = json.loads(
            (project.tickets_dir(self.source) / "index.json").read_text(encoding="utf-8")
        )
        self.assertEqual([value["id"] for value in index["tickets"]], ["ECS-4", "ECS-5"])

    def test_changed_project_status_and_comment_update_existing_snapshot(self) -> None:
        github.sync(self.source, fixture=self.fixture)
        changed = self.issue(4, "In Progress")
        changed["comments"] = [
            {
                "user": {"login": "Ada"},
                "body": "Position order matters.",
                "created_at": "2026-08-22T12:00:00Z",
                "html_url": "https://github.com/example/voxel/issues/4#issuecomment-1",
            }
        ]
        self.write_fixture([changed, self.issue(5)])

        result = github.sync(self.source, fixture=self.fixture)

        stored = json.loads(
            (project.tickets_dir(self.source) / "ECS-4" / "ticket.json").read_text(encoding="utf-8")
        )
        self.assertEqual((result["updated"], result["unchanged"]), (1, 1))
        self.assertEqual(stored["status"], "in-progress")
        self.assertEqual(stored["comments"][0]["author"], "Ada")

    def test_configured_field_mapping_changes_the_normalized_ticket(self) -> None:
        state = commands.dispatch(
            "configuration.mapping.replace",
            {
                "base_revision": run_git(project.config_checkout(self.source), "rev-parse", "HEAD"),
                "provider": "github",
                "fields": [
                    {"ours": "title", "theirs": "issue.custom_title"},
                    {"ours": "status", "theirs": "project.fields.Status"},
                ],
            },
            commands.CommandContext(self.source),
        )
        self.assertTrue(state["ok"])
        entry = self.issue(4, "In Review")
        entry["issue"]["custom_title"] = "ECS-4 Mapped title"
        self.write_fixture([entry])

        github.sync(self.source, fixture=self.fixture)

        stored = json.loads(
            (project.tickets_dir(self.source) / "ECS-4" / "ticket.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stored["title"], "Mapped title")
        self.assertEqual(stored["status"], "in-review")


if __name__ == "__main__":
    unittest.main()
