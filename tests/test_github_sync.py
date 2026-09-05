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


class GitHubDiscoveryTests(unittest.TestCase):
    def test_discover_lists_boards_and_repositories_for_the_token(self) -> None:
        from unittest import mock

        viewer = {
            "viewer": {
                "login": "tim",
                "name": "Tim",
                "projectsV2": {"nodes": [{"number": 8, "title": "Voxel ECS", "url": "https://github.com/users/tim/projects/8", "closed": False}, {"number": 3, "title": "Old", "url": "", "closed": True}]},
            }
        }
        organizations = {
            "viewer": {
                "organizations": {"nodes": [{"login": "acme", "projectsV2": {"nodes": [{"number": 2, "title": "Acme Board", "url": "https://github.com/orgs/acme/projects/2", "closed": False}]}}]},
            }
        }
        repos = [
            {"name": "voxel", "full_name": "tim/voxel", "owner": {"login": "tim"}, "html_url": "https://github.com/tim/voxel", "open_issues_count": 4, "private": False},
            {"name": "gone", "full_name": "tim/gone", "owner": {"login": "tim"}, "archived": True},
        ]
        with mock.patch.object(github, "_graphql", side_effect=[viewer, organizations]):
            with mock.patch.object(github, "_request_json", return_value=repos):
                found = github.discover("ghp_x")

        self.assertEqual(found["account"], {"login": "tim", "name": "Tim"})
        self.assertEqual(
            [(board["kind"], board["owner"], board["title"]) for board in found["boards"]],
            [("project", "tim", "Voxel ECS"), ("project", "acme", "Acme Board"), ("issues", "tim", "tim/voxel")],
        )
        self.assertEqual(found["notes"], [])

    def test_discover_without_project_scope_still_names_the_account(self) -> None:
        from unittest import mock

        responses = [{"login": "tim", "name": "Tim"}, []]
        with mock.patch.object(github, "_graphql", side_effect=github.GitHubError("GitHub GraphQL error: missing scope")):
            with mock.patch.object(github, "_request_json", side_effect=responses):
                found = github.discover("ghp_x")

        self.assertEqual(found["account"]["login"], "tim")
        self.assertEqual(found["boards"], [])
        self.assertIn("read:project", found["notes"][0])
        self.assertIn("read:org", found["notes"][1])

    def test_missing_org_scope_keeps_the_accounts_own_boards(self) -> None:
        from unittest import mock

        viewer = {"viewer": {"login": "tim", "name": "Tim", "projectsV2": {"nodes": [{"number": 8, "title": "Voxel ECS", "url": "", "closed": False}]}}}
        with mock.patch.object(github, "_graphql", side_effect=[viewer, github.GitHubError("requires read:org")]):
            with mock.patch.object(github, "_request_json", return_value=[]):
                found = github.discover("ghp_x")

        self.assertEqual([board["title"] for board in found["boards"]], ["Voxel ECS"])
        self.assertEqual(len(found["notes"]), 1)
        self.assertIn("read:org", found["notes"][0])

    def test_inspect_and_derive_build_the_project_model_from_the_board(self) -> None:
        from unittest import mock

        graph = {
            "owner": {
                "projectV2": {
                    "title": "Voxel ECS",
                    "url": "https://github.com/users/tim/projects/8",
                    "items": {"totalCount": 12},
                    "fields": {"nodes": [
                        {"name": "Title", "dataType": "TITLE"},
                        {"name": "Status", "dataType": "SINGLE_SELECT", "options": [{"name": "Todo"}, {"name": "In Progress"}, {"name": "Done"}]},
                        {"name": "Priority", "dataType": "SINGLE_SELECT", "options": [{"name": "P0"}, {"name": "P1"}]},
                        {"name": "Sprint", "dataType": "ITERATION"},
                        {"name": "Story Points", "dataType": "NUMBER"},
                        {"name": "Labels", "dataType": "LABELS"},
                    ]},
                }
            }
        }
        with mock.patch.object(github, "_request_json", return_value={"type": "User"}):
            with mock.patch.object(github, "_graphql", return_value=graph):
                inspection = github.inspect_board({"kind": "project", "owner": "tim", "number": 8}, "ghp_x")

        self.assertEqual(inspection["items"], 12)
        self.assertEqual([field["name"] for field in inspection["fields"]], ["Status", "Priority", "Sprint", "Story Points"])

        fields = [
            {"key": "title", "label": "Title", "type": "string", "required": True},
            {"key": "status", "label": "Status", "type": "string", "required": True},
            {"key": "priority", "label": "Priority", "type": "string", "required": False},
            {"key": "mine", "label": "Mine", "type": "string", "required": False},
        ]
        derived = github.derive_model(inspection, fields, {"title": "title", "status": "state"})

        by_key = {field["key"]: field for field in derived["fields"]}
        self.assertEqual(by_key["status"]["values"], ["todo", "in-progress", "done"])
        self.assertEqual(by_key["priority"]["values"], ["P0", "P1"])
        self.assertEqual(by_key["sprint"]["type"], "string")
        self.assertEqual(by_key["story_points"]["type"], "number")
        self.assertIn("mine", by_key)
        self.assertEqual(derived["mapping"]["priority"], "project.fields.Priority")
        self.assertEqual(derived["mapping"]["sprint"], "project.fields.Sprint.title")
        self.assertEqual(derived["mapping"]["status"], "state")
        self.assertIn("Status: Todo, In Progress, Done", derived["summary"])
        self.assertIn("12 items", derived["summary"])

