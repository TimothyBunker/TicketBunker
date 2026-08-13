from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ticketbunker import layout, types
from ticketbunker.api import TicketDatabase


class TicketDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        layout.bunkers_dir(self.root).mkdir()
        self.bunker = layout.bunker_path(self.root, "TB-7")
        layout.init_dirs(self.bunker)
        paths = layout.paths(self.bunker)
        ticket = {
            "id": "TB-7",
            "key": "seven",
            "title": "Keep ticket context with the repository",
            "status": "in-review",
            "type": "story",
            "priority": "high",
            "assignee": "Tim",
            "reporter": "Ada",
            "description": "One global ticket with child reports.",
            "acceptance_criteria": ["Branches are visible", "Reports are linked"],
            "metadata": {"jira_project": "TB", "sprint": 4},
            "assigned_branches": ["ada/TB-7-api", "lin/TB-7-missing"],
            "created": "2026-08-10T09:00:00+00:00",
            "updated": "2026-08-12T09:00:00+00:00",
        }
        paths["ticket"].write_text(json.dumps(ticket), encoding="utf-8")
        paths["branch_resolution"].write_text(
            json.dumps(
                {
                    "branches": ["ada/TB-7-api"],
                    "assigned_missing": ["lin/TB-7-missing"],
                    "match_rules": {"ada/TB-7-api": "explicit assignment"},
                }
            ),
            encoding="utf-8",
        )
        paths["merge_history"].write_text(
            json.dumps(
                {
                    "per_branch": {
                        "ada/TB-7-api": {
                            "ahead_of_merge_base": 3,
                            "behind_baseline": 1,
                            "merged_back": False,
                            "last_sync_date": "2026-08-12T08:00:00+00:00",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        paths["authorship"].write_text(
            json.dumps(
                {
                    "per_branch": {
                        "ada/TB-7-api": {
                            "authors": {"Ada": {"commits": 3}},
                            "recency_of_development": "2026-08-12T08:30:00+00:00",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        layout.session_config(self.bunker, "ada/TB-7-api").write_text(
            json.dumps({"branch": "ada/TB-7-api", "user": "Ada"}), encoding="utf-8"
        )
        layout.child_report(self.bunker, "ada/TB-7-api").write_text(
            """<!-- tb:verified branch=ada/TB-7-api commit=abc1234 ts=2026-08-12T08:10:00+00:00 -->
## Reader brief

### Who

- [speculation] grounded: Ada owns the report and Tim owns the ticket (ticket, authorship).

### What

- [speculation] grounded: The API now keeps presentations aligned (observations/diff.json).

### Where

- [real] Start with `ticketbunker/api.py:1`, the shared read boundary.

### Why

- [speculation] grounded: One read model prevents presentation drift (ticket, observations/diff.json).

## Who

- [observation] Tim is the assignee and Ada is the author.

## What

- [observation] Three commits changed the API.

## Where

- [real] The API is in `ticketbunker/api.py:1`.

## Why

- [speculation] grounded: one read model keeps presentations aligned.

## State

- [observation] The branch is open.
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_composes_ticket_branches_and_reports(self) -> None:
        database = TicketDatabase(self.root)
        ticket = database.get_ticket("TB-7")

        self.assertEqual(ticket.title, "Keep ticket context with the repository")
        self.assertEqual([branch.name for branch in ticket.branches], ["ada/TB-7-api", "lin/TB-7-missing"])
        self.assertEqual(ticket.branches[0].state, "open")
        self.assertEqual(ticket.branches[1].state, "missing")
        self.assertEqual(ticket.branches[0].authors, ("Ada",))
        self.assertEqual(len(ticket.reports), 1)
        self.assertEqual(ticket.reports[0].owner, "Ada")
        self.assertEqual(ticket.reports[0].verified_commit, "abc1234")
        self.assertEqual(ticket.reports[0].state, "predates development")
        self.assertIsNotNone(ticket.reports[0].brief)
        self.assertIn("Ada owns", ticket.reports[0].brief.who)
        self.assertNotIn("Reader brief", ticket.reports[0].evidence_text)
        self.assertIn("## What", ticket.reports[0].evidence_text)
        self.assertIn("Three commits", ticket.reports[0].what)
        self.assertEqual(ticket.validation_errors, ())

    def test_list_is_a_compact_view_of_the_same_snapshot(self) -> None:
        summary = TicketDatabase(self.root).list_tickets()[0]

        self.assertEqual(summary.id, "TB-7")
        self.assertEqual(summary.branch_count, 2)
        self.assertEqual(summary.report_count, 1)
        self.assertEqual(summary.last_development, "2026-08-12T08:30:00+00:00")

    def test_discover_walks_up_to_repository_database(self) -> None:
        nested = self.root / "src" / "package"
        nested.mkdir(parents=True)

        self.assertEqual(TicketDatabase.discover(nested).project_root, self.root)

    def test_ticket_extension_fields_are_typed(self) -> None:
        self.assertIn(
            "'acceptance_criteria' must be a list of non-empty strings",
            types.validate("ticket", {"id": "TB-7", "key": "seven", "acceptance_criteria": "done"}),
        )
        self.assertIn(
            "'metadata' must be a mapping with non-empty string keys",
            types.validate("ticket", {"id": "TB-7", "key": "seven", "metadata": {"": 1}}),
        )


if __name__ == "__main__":
    unittest.main()
