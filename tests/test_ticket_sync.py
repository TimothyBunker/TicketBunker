from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ticketbunker import commands, handlers, layout, project
from ticketbunker.ledger import Ledger


def run_git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


class TicketSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.source = base / "source"
        self.remote = base / "ticketbunker.git"
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

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def ticket(title: str = "Position system") -> dict:
        return {
            "id": "ECS-4",
            "key": "ECS-4",
            "title": title,
            "description": "Apply velocity to position.",
            "type": "task",
            "status": "todo",
            "labels": ["ecs"],
            "comments": [],
            "metadata": {
                "tracker": "github",
                "source_url": "https://example.invalid/issues/4",
                "github": {"issue_number": 4, "state": "open"},
            },
        }

    def dispatch(self, ticket: dict) -> dict:
        return commands.dispatch(
            "ticket.upsert",
            {"ticket": ticket, "intake": "github"},
            commands.CommandContext(self.source),
        )

    def test_upsert_is_idempotent_and_preserves_local_ticket_fields(self) -> None:
        created = self.dispatch(self.ticket())
        bunker = project.tickets_dir(self.source) / "ECS-4"
        handlers.assign_branch(bunker, "Tim/ECS-4-position", Ledger(layout.paths(bunker)["ledger"]))
        local = json.loads(layout.paths(bunker)["ticket"].read_text(encoding="utf-8"))
        local["metadata"]["charging_code"] = "R42"
        handlers.intake_ticket(bunker, local, Ledger(layout.paths(bunker)["ledger"]))
        project.publish_data(self.source, "Add local ticket context")
        before = run_git(project.data_checkout(self.source), "rev-parse", "HEAD")
        ledger_before = layout.paths(bunker)["ledger"].read_text(encoding="utf-8")

        unchanged = self.dispatch(self.ticket())

        after = run_git(project.data_checkout(self.source), "rev-parse", "HEAD")
        stored = json.loads(layout.paths(bunker)["ticket"].read_text(encoding="utf-8"))
        self.assertEqual(created["action"], "created")
        self.assertEqual(unchanged["action"], "unchanged")
        self.assertFalse(unchanged["publication"]["published"])
        self.assertEqual(after, before)
        self.assertEqual(layout.paths(bunker)["ledger"].read_text(encoding="utf-8"), ledger_before)
        self.assertEqual(stored["assigned_branches"], ["Tim/ECS-4-position"])
        self.assertEqual(stored["metadata"]["charging_code"], "R42")

    def test_changed_source_snapshot_updates_existing_ticket(self) -> None:
        self.dispatch(self.ticket())

        result = self.dispatch(self.ticket("Position system and integration"))

        stored = json.loads(
            (project.tickets_dir(self.source) / "ECS-4" / "ticket.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["action"], "updated")
        self.assertTrue(result["publication"]["published"])
        self.assertEqual(stored["title"], "Position system and integration")

    def test_batch_validates_every_ticket_before_writing_any(self) -> None:
        valid = self.ticket()
        invalid = {**self.ticket("Invalid"), "id": "not a ticket", "key": "not a ticket"}

        with self.assertRaises(commands.CommandRejected):
            commands.dispatch(
                "ticket.sync",
                {"tickets": [valid, invalid], "intake": "mock"},
                commands.CommandContext(self.source),
            )

        self.assertFalse((project.tickets_dir(self.source) / "ECS-4").exists())


if __name__ == "__main__":
    unittest.main()
