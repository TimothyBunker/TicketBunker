from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ticketbunker import layout, project, types
from ticketbunker.api import TicketCatalog, TicketDatabase, TicketQuery


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
            """<!-- tb:verified branch=ada/TB-7-api commit=abc1234 ts=2026-08-12T08:10:00+00:00 harness=pi provider=openai model=gpt-5.6-sol -->
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
        self.assertEqual(ticket.reports[0].agent_harness, "pi")
        self.assertEqual(ticket.reports[0].agent_provider, "openai")
        self.assertEqual(ticket.reports[0].agent_model, "gpt-5.6-sol")
        self.assertEqual(ticket.reports[0].state, "predates development")
        self.assertIsNotNone(ticket.reports[0].brief)
        self.assertIn("Ada owns", ticket.reports[0].brief.who)
        self.assertNotIn("Reader brief", ticket.reports[0].evidence_text)
        self.assertIn("Tim is the assignee", ticket.reports[0].who)
        self.assertIn("## What", ticket.reports[0].evidence_text)
        self.assertIn("Three commits", ticket.reports[0].what)
        self.assertEqual(ticket.validation_errors, ())

    def test_list_is_a_compact_view_of_the_same_snapshot(self) -> None:
        summary = TicketDatabase(self.root).list_tickets()[0]

        self.assertEqual(summary.id, "TB-7")
        self.assertEqual(summary.branch_count, 2)
        self.assertEqual(summary.report_count, 1)
        self.assertEqual(summary.last_development, "2026-08-12T08:30:00+00:00")
        self.assertEqual(summary.reporter, "Ada")

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
        self.assertIn(
            "'people_of_interest' must be a list of non-empty strings",
            types.validate("ticket", {"id": "TB-7", "key": "seven", "people_of_interest": [7]}),
        )
        self.assertTrue(types.validate("ticket", {"id": 7, "key": "seven"}))
        self.assertTrue(types.validate("ticket", {"id": "TB-7", "key": "seven", "references": [7]}))
        self.assertIn(
            "comment 0: optional 'when' must be a string",
            types.validate(
                "ticket",
                {"id": "TB-7", "key": "seven", "comments": [{"author": "Ada", "text": "Hi", "when": 7}]},
            ),
        )
        self.assertIn(
            "activity 0: requires non-empty 'kind' and 'text' fields",
            types.validate(
                "ticket",
                {"id": "TB-7", "key": "seven", "activity": [{"kind": "history"}]},
            ),
        )
        self.assertEqual(
            types.validate(
                "ticket",
                {
                    "id": "TB-7",
                    "key": "seven",
                    "activity": [{"kind": "note", "text": "Mirrored note"}],
                },
            ),
            [],
        )


class TicketCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / project.LOCATOR_FILE).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "project": "ECS",
                    "bunker_repository": "example.invalid/tickets.git",
                    "config_branch": project.DEFAULT_CONFIG_BRANCH,
                    "data_branch": project.DEFAULT_DATA_BRANCH,
                }
            ),
            encoding="utf-8",
        )
        for key, name in (("ECS", "Engine"), ("WEB", "Website")):
            config_dir = project.project_config_dir(self.root, key)
            config_dir.mkdir(parents=True)
            (config_dir / "project.json").write_text(
                json.dumps({"key": key, "name": name, "source_remote": f"git@example/{key}.git"}),
                encoding="utf-8",
            )
            project.tickets_dir(self.root, key).mkdir(parents=True)
        self._ticket(
            "ECS",
            "ECS-1",
            title="Position system",
            status="todo",
            assignee="TimothyBunker",
            reporter="Ada",
            labels=["ECSF", "C0"],
        )
        self._ticket(
            "ECS",
            "ECS-2",
            title="Chunk loading",
            status="done",
            assignee="Grace",
            reporter="TimothyBunker",
            labels=["world"],
        )
        self._ticket(
            "WEB",
            "WEB-1",
            title="Documentation shell",
            status="open",
            assignee="TimothyBunker",
            reporter="Grace",
            labels=["docs"],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _ticket(self, project_key: str, ticket_id: str, **values: object) -> None:
        bunker = project.tickets_dir(self.root, project_key) / ticket_id
        layout.init_dirs(bunker)
        data = {
            "id": ticket_id,
            "key": ticket_id.casefold(),
            "description": f"Work for {ticket_id}",
            "updated": f"2026-08-{10 + len(ticket_id)}T09:00:00+00:00",
            **values,
        }
        layout.paths(bunker)["ticket"].write_text(json.dumps(data), encoding="utf-8")

    def test_filters_and_pages_lightweight_ticket_references(self) -> None:
        catalog = TicketCatalog(self.root)
        results = catalog.query(TicketQuery(assignee="TimothyBunker", per_page=1))

        self.assertEqual(results.total, 2)
        self.assertEqual(results.page_count, 2)
        self.assertEqual(len(results.tickets), 1)
        self.assertNotEqual(results.tickets[0].status, "done")
        self.assertEqual(catalog.query(TicketQuery(status="all", label="world")).tickets[0].id, "ECS-2")
        self.assertEqual(catalog.query(TicketQuery(search="documentation")).tickets[0].project, "WEB")

    def test_project_catalog_and_cross_project_database_are_explicit(self) -> None:
        catalog = TicketCatalog(self.root)

        self.assertEqual(catalog.project_keys(), ("ECS", "WEB"))
        self.assertEqual([item.name for item in catalog.projects()], ["Engine", "Website"])
        self.assertEqual(catalog.database("WEB").get_ticket("WEB-1").title, "Documentation shell")
        self.assertEqual(catalog.facets("ECS").labels, ("C0", "ECSF", "world"))


if __name__ == "__main__":
    unittest.main()
