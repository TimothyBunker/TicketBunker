from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ticketbunker import layout
from ticketbunker.viewer import page_index, page_ticket, render_report


class ViewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        layout.bunkers_dir(self.root).mkdir()
        self.bunker = layout.bunker_path(self.root, "TB-9")
        layout.init_dirs(self.bunker)
        paths = layout.paths(self.bunker)
        paths["ticket"].write_text(
            json.dumps(
                {
                    "id": "TB-9",
                    "key": "nine",
                    "title": "A single, complete ticket",
                    "status": "in-progress",
                    "priority": "high",
                    "assignee": "Tim",
                    "reporter": "Grace",
                    "description": "Show <script>alert('no')</script> safely.",
                    "acceptance_criteria": ["Lifecycle is visible"],
                    "metadata": {"tracker": "Jira"},
                }
            ),
            encoding="utf-8",
        )
        branch = "grace/TB-9-a-very-long-branch-name-that-must-wrap"
        paths["branch_resolution"].write_text(
            json.dumps({"branches": [branch], "assigned_missing": [], "match_rules": {}}),
            encoding="utf-8",
        )
        paths["merge_history"].write_text(
            json.dumps({"per_branch": {branch: {"ahead_of_merge_base": 1, "behind_baseline": 0, "merged_back": False}}}),
            encoding="utf-8",
        )
        paths["authorship"].write_text(
            json.dumps({"per_branch": {branch: {"authors": {"Grace": {}}, "recency_of_development": "2026-08-12T10:00:00+00:00"}}}),
            encoding="utf-8",
        )
        layout.child_report(self.bunker, branch).write_text(
            """## Reader brief

### Who

- [speculation] grounded: Grace wrote the branch and Tim owns the ticket (ticket, authorship).

### What

- [speculation] grounded: The viewer now reads one composed snapshot (observations/diff.json).

### Where

- [real] Start with `ticketbunker/viewer.py:1`, where the ticket page is rendered.

### Why

- [speculation] grounded: One read path prevents presentation drift (ticket, observations/diff.json).

## Who

- [observation] Grace authored the branch.

## What

- [observation] The viewer reads one snapshot.

## Where

- [real] It lives at `ticketbunker/viewer.py:1`.

## Why

- [speculation] grounded: duplicate presentation logic drifts.

## State

- [observation] The report is published.
""",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ticket_page_is_simple_linked_and_escaped(self) -> None:
        rendered = page_ticket(self.root, "TB-9", "fingerprint")
        self.assertIsNotNone(rendered)
        page = rendered.decode("utf-8")

        self.assertIn("Description", page)
        self.assertIn("Acceptance criteria", page)
        self.assertIn("Reader brief", page)
        self.assertIn("The viewer now reads one composed snapshot", page)
        self.assertIn("Evidence trail", page)
        self.assertIn("1 child report", page)
        self.assertIn("Lifecycle", page)
        self.assertIn("Branches", page)
        self.assertIn("Details", page)
        self.assertIn('aria-current="step"', page)
        self.assertIn('<link rel="icon" href="data:,">', page)
        self.assertIn('href="#report-grace__TB-9-a-very-long-branch-name-that-must-wrap"', page)
        self.assertIn('<details class="evidence">', page)
        self.assertNotIn('<details class="evidence" open', page)
        self.assertNotIn("<h2>Reader brief</h2><h3>Who</h3>", page)
        self.assertIn("&lt;script&gt;alert(&#x27;no&#x27;)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert('no')</script>", page)
        self.assertNotIn("<form", page)

    def test_index_uses_the_same_ticket_database(self) -> None:
        page = page_index(self.root, "fingerprint").decode("utf-8")

        self.assertIn("TB-9", page)
        self.assertIn("A single, complete ticket", page)
        self.assertIn("1 branches · 1 reports", page)

    def test_report_markup_is_escaped_before_tags_are_reintroduced(self) -> None:
        rendered = render_report("- [real] Safe `<img src=x onerror=alert(1)>`")

        self.assertIn('<span class="tag real">real</span>', rendered)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", rendered)
        self.assertNotIn("<img", rendered)


if __name__ == "__main__":
    unittest.main()
