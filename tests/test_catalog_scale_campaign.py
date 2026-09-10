"""A provider-free 3,000-ticket catalog, built through the shared backend."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
import random
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ticketbunker import app_worker, bunkerdb, bunkerruntime, commands


class CatalogScaleCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.area = tempfile.TemporaryDirectory(prefix="tb-catalog-simulation-")
        cls.root = Path(cls.area.name)
        cls.environment = patch.dict(os.environ, {"TB_DATA_HOME": str(cls.root / "data"), "TB_AGENT_DISABLE": "1"})
        cls.environment.start()
        try:
            bunker = bunkerdb.create_bunker("Simulation catalog", project_key="SYS")
            cls.bid = bunker["id"]
            cls.projects = {"SYS": bunker["projects"][0]["id"]}
            for key, name, parent in (("ENG", "Engine team", "SYS"), ("REN", "Renderer 日本語", "ENG"), ("OPS", "Operations", None)):
                proposal = bunkerdb.propose_project(cls.bid, key=key, name=name, parent_id=cls.projects.get(parent))
                bunkerdb.apply_project_change(cls.bid, proposal)
                cls.projects[key] = proposal["project"]["id"]
            cls.tickets = []
            for key, project_id in cls.projects.items():
                batch = []
                for number in range(1, 751):
                    status = ("todo", "in-progress", "blocked", "done", "closed", "cancelled")[number % 6]
                    ticket = {"id": f"{key}-{number}", "title": f"Simulation café feature {number}", "status": status,
                        "assignee": {"displayName": ("Ada", "Tim", "Zoë")[number % 3]},
                        "reporter": {"login": ("reviewer", "owner")[number % 2]},
                        "labels": [{"name": f"group-{number % 5}"}, "simulation"],
                        "description": "private detailed context omitted from references " * 80,
                        "comments": [{"body": "a comment that must not be materialized in the list"}],
                        "metadata": {"source_updated": "2026-09-10T00:00:00Z", "source_url": f"https://example.test/{key}/{number}"}}
                    batch.append(ticket)
                    cls.tickets.append({**ticket, "project_id": project_id, "project_key": key})
                proposal = bunkerdb.propose_ticket_sync(cls.bid, project_id=project_id, tickets=batch, source_kind="manual")
                bunkerdb.apply_ticket_sync_change(cls.bid, proposal)
        except BaseException:
            cls.environment.stop()
            cls.area.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.area.cleanup()

    def listing(self, **payload):
        return commands.dispatch("application.ticket.list", {"bunker_id": self.bid, **payload}, commands.CommandContext(self.root))

    def expected(self, **filters):
        scope = {None: set(self.projects), "SYS": {"SYS", "ENG", "REN"}, "ENG": {"ENG", "REN"}, "REN": {"REN"}, "OPS": {"OPS"}}[filters.get("project")]
        result = []
        for ticket in self.tickets:
            if ticket["project_key"] not in scope:
                continue
            status = filters.get("status", "active")
            if status == "active" and ticket["status"] in {"done", "closed", "cancelled"}:
                continue
            if status not in {"active", "all"} and ticket["status"] != status:
                continue
            if filters.get("assignee") and ticket["assignee"]["displayName"].casefold() != filters["assignee"].casefold():
                continue
            if filters.get("reporter") and ticket["reporter"]["login"].casefold() != filters["reporter"].casefold():
                continue
            if filters.get("label") and filters["label"].casefold() not in {ticket["labels"][0]["name"], "simulation"}:
                continue
            result.append(ticket["id"])
        return sorted(result, key=str.casefold)

    def test_all_pages_are_complete_stable_and_reference_only(self):
        ids = []
        started = time.monotonic()
        with patch.object(bunkerruntime, "materialize", side_effect=AssertionError("listing must not materialize ticket runtime")):
            for page in range(1, 31):
                result = self.listing(status="all", page=page, page_size=100)
                self.assertEqual((result["total"], result["page_count"], result["page"]), (3000, 30, page))
                self.assertEqual(len(result["tickets"]), 100)
                ids.extend(ticket["id"] for ticket in result["tickets"])
                encoded = json.dumps(result)
                self.assertNotIn("private detailed context", encoded)
                self.assertNotIn("a comment that must", encoded)
                self.assertLess(len(encoded), 100_000)
        self.assertEqual(ids, self.expected(status="all"))
        self.assertEqual(len(set(ids)), 3000)
        print(f"catalog simulation: 30 pages / 3,000 tickets in {time.monotonic() - started:.3f}s (machine-specific)")

    def test_parent_child_status_and_combined_person_label_filters(self):
        cases = [
            {"project": "SYS"}, {"project": "ENG", "status": "all"}, {"project": "OPS", "status": "done"},
            {"project": "REN", "status": "all", "assignee": "zoë"},
            {"project": "SYS", "status": "all", "reporter": "OWNER", "label": "GROUP-2"},
            {"status": "in-progress", "assignee": "TIM", "reporter": "owner", "label": "group-3"},
            {"status": "all", "assignee": "not-a-member"},
        ]
        for filters in cases:
            with self.subTest(filters=filters):
                expected = self.expected(**filters)
                payload = {key: value for key, value in filters.items() if key != "project"}
                if filters.get("project"):
                    payload["project_id"] = self.projects[filters["project"]]
                first = self.listing(**payload, page_size=100)
                self.assertEqual(first["total"], len(expected))
                ids = [ticket["id"] for ticket in first["tickets"]]
                for page in range(2, first["page_count"] + 1):
                    ids.extend(ticket["id"] for ticket in self.listing(**payload, page=page, page_size=100)["tickets"])
                self.assertEqual(ids, expected)

    def test_search_unicode_id_project_and_literal_punctuation(self):
        for search, count in (("CAFÉ", 3000), ("日本語", 750), ("engine team", 750), ("REN-750", 1), ("' OR 1=1 --", 0), ("%", 0)):
            with self.subTest(search=search):
                self.assertEqual(self.listing(status="all", search=search)["total"], count)

    def test_pagination_clamps_and_invalid_project_is_actionable(self):
        result = self.listing(status="all", page=9999, page_size=10000)
        self.assertEqual((result["page"], result["page_size"], len(result["tickets"])), (30, 100, 100))
        empty = self.listing(search="simulation-no-match", page=30, page_size=7)
        self.assertEqual((empty["page"], empty["page_count"], empty["total"]), (1, 1, 0))
        response = app_worker.dispatch_request({"id": 1, "action": "application.ticket.list", "payload": {"bunker_id": self.bid, "project_id": "missing-project"}})
        self.assertFalse(response["ok"])
        self.assertNotEqual(response["code"], "internal_error")

    def test_facets_are_scoped_to_project_not_current_filter(self):
        result = self.listing(project_id=self.projects["REN"], status="todo", assignee="Ada", label="group-1")
        self.assertEqual(result["facets"]["statuses"], ["blocked", "cancelled", "closed", "done", "in-progress", "todo"])
        self.assertEqual(result["facets"]["assignees"], ["Ada", "Tim", "Zoë"])
        self.assertEqual(result["facets"]["reporters"], ["owner", "reviewer"])
        self.assertEqual(result["facets"]["labels"], ["group-0", "group-1", "group-2", "group-3", "group-4", "simulation"])

    def test_seeded_filter_combinations_match_independent_reference(self):
        rng = random.Random(9173)
        for _ in range(40):
            filters = {"project": rng.choice([None, "SYS", "ENG", "REN", "OPS"]),
                "status": rng.choice(["active", "all", "todo", "in-progress", "blocked", "done", "closed", "cancelled"]),
                "assignee": rng.choice(["", "Ada", "Tim", "Zoë"]),
                "reporter": rng.choice(["", "owner", "reviewer"]),
                "label": rng.choice(["", "group-0", "group-3", "simulation"])}
            with self.subTest(filters=filters):
                expected = self.expected(**filters)
                payload = {key: value for key, value in filters.items() if key != "project"}
                if filters["project"]:
                    payload["project_id"] = self.projects[filters["project"]]
                result = self.listing(**payload, page_size=37)
                self.assertEqual(result["total"], len(expected))
                self.assertEqual([row["id"] for row in result["tickets"]], expected[:37])

    def test_concurrent_same_ticket_updates_have_one_winner_and_rollback_loser_batch(self):
        originals = [dict(ticket) for ticket in self.tickets if ticket["id"] in {"REN-749", "REN-750"}]
        originals = [{key: value for key, value in ticket.items() if key not in {"project_id", "project_key"}} for ticket in originals]
        proposals = [bunkerdb.propose_ticket_sync(self.bid, project_id=self.projects["REN"], source_kind="manual",
            tickets=[{**ticket, "title": f"participant {index}"} for ticket in originals]) for index in (1, 2)]
        barrier = threading.Barrier(2)
        def apply(proposal):
            barrier.wait(timeout=5)
            try:
                bunkerdb.apply_ticket_sync_change(self.bid, proposal)
                return True
            except bunkerdb.BunkerDatabaseError:
                return False
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(apply, proposals))
            self.assertEqual(sum(results), 1)
            first = bunkerdb.get_ticket(self.bid, "REN-749")["ticket"]["title"]
            second = bunkerdb.get_ticket(self.bid, "REN-750")["ticket"]["title"]
            self.assertEqual(first, second)
            self.assertEqual(self.listing(status="all")["total"], 3000)
        finally:
            bunkerdb.apply_ticket_sync_change(self.bid, bunkerdb.propose_ticket_sync(self.bid, project_id=self.projects["REN"], tickets=originals, source_kind="manual"))

    def test_manual_ticket_without_source_timestamp_has_valid_updated_reference(self):
        # A separate Bunker keeps this focused input case out of the 3,000-row
        # fixture and its stable pagination expectations.
        local = bunkerdb.create_bunker("Manual timestamp simulation", project_key="MAN")
        explicit = "2026-08-01T10:20:30+00:00"
        variants = ({}, {"metadata": None}, {"metadata": {}},
                    {"metadata": {"source_url": "https://example.test/manual"}},
                    {"metadata": {"source_updated": None}}, {"metadata": {"source_updated": ""}},
                    {"metadata": {"source_updated": explicit}})
        proposal = bunkerdb.propose_ticket_sync(local["id"], project_id=local["projects"][0]["id"], source_kind="manual",
            tickets=[{"id": f"MAN-{index}", "title": "Manual input", **variant} for index, variant in enumerate(variants, 1)])
        bunkerdb.apply_ticket_sync_change(local["id"], proposal)
        references = bunkerdb.list_tickets(local["id"])["tickets"]
        for reference in references:
            with self.subTest(ticket=reference["id"]):
                self.assertNotEqual(reference["updated"], "None", "Absent upstream timestamps must fall back to local update time")
                datetime.fromisoformat(reference["updated"].replace("Z", "+00:00"))
        self.assertEqual(next(row for row in references if row["id"] == "MAN-7")["updated"], explicit)


if __name__ == "__main__":
    unittest.main()
