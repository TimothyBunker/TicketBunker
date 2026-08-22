from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from ticketbunker import layout
from ticketbunker.viewer import (
    page_index,
    page_project,
    page_projects,
    page_ticket,
    render_report,
    render_ticket_text,
    serve,
)


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
                    "type": "task",
                    "priority": "high",
                    "assignee": "Tim",
                    "reporter": "Grace",
                    "description": "## Context\n\nShow **important** <script>alert('no')</script> safely.\n\n- Keep the ticket readable",
                    "acceptance_criteria": ["Ticket details are **visible**"],
                    "labels": ["viewer", "jira"],
                    "people_of_interest": ["Ada"],
                    "references": ["TB-8"],
                    "created": "2026-08-11T09:30:00+00:00",
                    "updated": "2026-08-12T10:00:00+00:00",
                    "comments": [
                        {
                            "author": "Ada",
                            "when": "2026-08-12T09:00:00+00:00",
                            "source": "https://jira.example/TB-9#comment-1",
                            "text": "Please check `viewer.py` first.",
                        }
                    ],
                    "activity": [
                        {
                            "kind": "history",
                            "author": "Grace",
                            "when": "2026-08-12T09:30:00+00:00",
                            "text": "Moved the ticket from To do to In progress.",
                        },
                        {
                            "kind": "worklog",
                            "author": "Ada",
                            "when": "2026-08-12T09:15:00+00:00",
                            "text": "Investigated the viewer layout.",
                        },
                        {
                            "kind": "note",
                            "text": "Mirrored from the source; editing is disabled here.",
                        },
                    ],
                    "metadata": {
                        "tracker": "github",
                        "source_url": "https://github.example/acme/repo/issues/9",
                        "github": {
                            "author_association": "OWNER",
                            "created_at": "2026-08-10T08:00:00+00:00",
                            "issue_node_id": "NODE_9",
                            "issue_number": 9,
                            "issue_state": "open",
                            "locked": False,
                            "repository": "acme/repo",
                            "source_title": "Duplicate source title",
                            "updated_at": "2026-08-12T08:15:00+00:00",
                            "project": {
                                "fields": {"Charge code": "C100", "Status": "In progress"},
                                "item_id": "ITEM_9",
                                "number": 77,
                                "owner": "acme",
                                "title": "Nine Board",
                                "url": "https://github.example/acme/projects/77",
                            },
                        },
                    },
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
            """<!-- tb:verified branch=grace/TB-9-viewer commit=abc1234 ts=2026-08-12T09:00:00+00:00 harness=pi provider=openai model=gpt-5.6-sol -->
## Reader brief

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
        self.assertIn('role="tablist" aria-label="Ticket view"', page)
        self.assertIn('data-view-tab="ticket">Ticket</button>', page)
        self.assertIn('data-view-tab="agent">Agent</button>', page)
        self.assertIn(
            '<a class="ticket-project" href="https://github.example/acme/projects/77" '
            'rel="noreferrer">Nine Board</a>',
            page,
        )
        self.assertLess(page.index("Nine Board"), page.index("A single, complete ticket"))
        self.assertIn('<span class="badge status">In progress</span>', page)
        self.assertIn('<span class="badge label">viewer</span>', page)
        self.assertIn('<span class="badge label">jira</span>', page)
        self.assertIn('<span class="head-updated">Updated 12 Aug 2026, 08:15</span>', page)
        self.assertIn('id="ticket-panel" role="tabpanel"', page)
        self.assertIn('data-view-panel="ticket"><article class="ticket-main ticket-page">', page)
        self.assertIn('id="agent-panel" role="tabpanel" aria-labelledby="agent-tab" data-view-panel="agent" hidden', page)
        self.assertIn("Agent work", page)
        self.assertIn("Work by Grace", page)
        self.assertIn("Generated by gpt-5.6-sol (openai) via pi", page)
        self.assertIn('<option value="master">Master</option>', page)
        self.assertIn('data-report-select aria-label="Agent report"', page)
        self.assertIn('id="report-master" data-report-panel="master"', page)
        self.assertIn('data-report-panel="grace__TB-9-a-very-long-branch-name-that-must-wrap" hidden', page)
        self.assertIn("The viewer now reads one composed snapshot", page)
        self.assertEqual(page.count("Full trail"), 8)
        self.assertIn("Report state", page)
        self.assertEqual(page.count('<details class="category-trail"'), 10)
        self.assertIn('data-category="who"', page)
        self.assertIn('data-category="what"', page)
        self.assertIn('data-category="where"', page)
        self.assertIn('data-category="why"', page)
        self.assertIn('data-category="state"', page)
        self.assertIn("Grace authored the branch", page)
        self.assertIn("The report is published", page)
        self.assertIn("Lifecycle", page)
        self.assertIn("Branches", page)
        self.assertIn("Details", page)
        ticket_panel = page.split('id="ticket-panel"', 1)[1].split('id="agent-panel"', 1)[0]
        agent_panel = page.split('id="agent-panel"', 1)[1]
        self.assertIn('<section class="issue-details"><div class="details-heading"><h2>Details</h2>', ticket_panel)
        self.assertEqual(ticket_panel.count('<section class="issue-details">'), 1)
        self.assertNotIn("More fields", ticket_panel)
        self.assertNotIn('class="ticket-rail', ticket_panel)
        self.assertLess(ticket_panel.index("Description"), ticket_panel.index("Activity"))
        self.assertLess(ticket_panel.index("Activity"), ticket_panel.index("Details"))
        self.assertIn('<dt>Type</dt><dd><span>Task</span></dd>', ticket_panel)
        self.assertIn('<dt>Created</dt><dd><span>10 Aug 2026, 08:00</span></dd>', ticket_panel)
        self.assertNotIn('<details class="details">', ticket_panel)
        self.assertNotIn("Lifecycle", ticket_panel)
        self.assertNotIn("Branches", ticket_panel)
        self.assertIn("Lifecycle", agent_panel)
        self.assertIn("Branches", agent_panel)
        self.assertIn("Activity", ticket_panel)
        self.assertIn("Please check <code>viewer.py</code> first.", ticket_panel)
        self.assertIn('<span class="activity-kind">Comment</span>', ticket_panel)
        self.assertIn('<span class="activity-kind">History</span>', ticket_panel)
        self.assertIn('<span class="activity-kind">Work log</span>', ticket_panel)
        self.assertIn('<span class="activity-kind">Note</span>', ticket_panel)
        self.assertNotIn('<span class="field-token">viewer</span>', ticket_panel)
        self.assertIn('<a class="field-token mono" href="/ticket/TB-8">TB-8</a>', ticket_panel)
        self.assertIn('<dt>Tracker</dt><dd><span>GitHub</span></dd>', ticket_panel)
        self.assertIn('<dt>Source repository</dt><dd><span>acme/repo</span></dd>', ticket_panel)
        self.assertIn('<dt>GitHub · Project · Fields · Charge code</dt><dd><span>C100</span></dd>', ticket_panel)
        self.assertIn('href="https://github.example/acme/repo/issues/9" rel="noreferrer">Open source</a>', ticket_panel)
        self.assertNotIn("GitHub · Project · Number", ticket_panel)
        self.assertNotIn("GitHub · Source title", ticket_panel)
        self.assertNotIn("GitHub · Updated at", ticket_panel)
        self.assertNotIn("GitHub · Project · Fields · Status", ticket_panel)
        self.assertIn('<details class="metadata-disclosure"><summary>Metadata</summary>', ticket_panel)
        self.assertIn("GitHub · Issue node ID", ticket_panel)
        self.assertIn("GitHub · Locked", ticket_panel)
        self.assertIn("GitHub · Project · Item ID", ticket_panel)
        self.assertIn('aria-current="step"', page)
        self.assertIn('<link rel="icon" href="data:,">', page)
        self.assertIn('href="#report-grace__TB-9-a-very-long-branch-name-that-must-wrap"', page)
        self.assertNotIn('<details class="category-trail" open', page)
        self.assertNotIn("Reader brief", page)
        self.assertIn("<h3>Context</h3>", ticket_panel)
        self.assertIn("<strong>important</strong>", ticket_panel)
        self.assertIn("<strong>visible</strong>", ticket_panel)
        self.assertIn("&lt;script&gt;alert(&#x27;no&#x27;)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert('no')</script>", page)
        self.assertNotIn("<form", page)

    def test_index_uses_the_same_ticket_database(self) -> None:
        page = page_index(self.root, "fingerprint", {"assignee": "*"}).decode("utf-8")

        self.assertIn("TB-9", page)
        self.assertIn("A single, complete ticket", page)
        self.assertIn('aria-current="page">Tickets</a>', page)
        self.assertIn('<a href="/projects">Projects</a>', page)
        self.assertIn('name="q" type="search"', page)
        self.assertIn('<span>Status</span><select name="status">', page)
        self.assertIn('<span>Assignee</span><select name="assignee">', page)
        self.assertIn('<span>Reporter</span><select name="reporter">', page)
        self.assertIn('<span>Label</span><select name="label">', page)
        self.assertIn('<span>Project</span><select name="project">', page)
        self.assertIn("Assigned to Tim", page)
        self.assertIn('<span class="ticket-label">viewer</span>', page)

    def test_completed_tickets_are_hidden_until_status_filter_changes(self) -> None:
        bunker = layout.bunker_path(self.root, "TB-10")
        layout.init_dirs(bunker)
        layout.paths(bunker)["ticket"].write_text(
            json.dumps(
                {
                    "id": "TB-10",
                    "key": "ten",
                    "title": "Already shipped",
                    "status": "done",
                    "assignee": "Tim",
                }
            ),
            encoding="utf-8",
        )

        active = page_index(self.root, "fingerprint", {"assignee": "*"}).decode("utf-8")
        complete = page_index(
            self.root, "fingerprint", {"assignee": "*", "status": "all"}
        ).decode("utf-8")

        self.assertNotIn("Already shipped", active)
        self.assertIn("Already shipped", complete)

    def test_projects_link_to_filtered_project_ticket_views(self) -> None:
        projects = page_projects(self.root, "fingerprint", {"assignee": "*"}).decode("utf-8")
        selected = page_project(
            self.root, "TB", "fingerprint", {"assignee": "*", "label": "viewer"}
        )

        self.assertIn("Projects", projects)
        self.assertIn('href="/project/TB?', projects)
        self.assertIsNotNone(selected)
        selected_page = selected.decode("utf-8")
        self.assertIn("TB tickets", selected_page)
        self.assertIn("TB-9", selected_page)
        self.assertIn('action="/project/TB"', selected_page)

    def test_ticket_results_are_paginated(self) -> None:
        for number in range(10, 31):
            bunker = layout.bunker_path(self.root, f"TB-{number}")
            layout.init_dirs(bunker)
            layout.paths(bunker)["ticket"].write_text(
                json.dumps(
                    {
                        "id": f"TB-{number}",
                        "key": str(number),
                        "title": f"Ticket {number}",
                        "status": "todo",
                        "assignee": "Tim",
                    }
                ),
                encoding="utf-8",
            )

        first = page_index(self.root, "fingerprint", {"assignee": "*"}).decode("utf-8")
        second = page_index(
            self.root, "fingerprint", {"assignee": "*", "page": "2"}
        ).decode("utf-8")

        self.assertIn("Page 1 of 2", first)
        self.assertIn(">Next</a>", first)
        self.assertIn("Page 2 of 2", second)
        self.assertIn(">Previous</a>", second)

    def test_http_routes_preserve_filters_and_open_project_tickets(self) -> None:
        server = serve(self.root, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            tickets = urlopen(f"{base}/?assignee=*&label=viewer").read().decode("utf-8")
            projects = urlopen(f"{base}/projects?assignee=*").read().decode("utf-8")
            project_page = urlopen(f"{base}/project/TB?assignee=*").read().decode("utf-8")
            ticket = urlopen(f"{base}/project/TB/ticket/TB-9").read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertIn("A single, complete ticket", tickets)
        self.assertIn('value="viewer" selected', tickets)
        self.assertIn('href="/project/TB?', projects)
        self.assertIn("TB tickets", project_page)
        self.assertIn("Agent work", ticket)

    def test_report_markup_is_escaped_before_tags_are_reintroduced(self) -> None:
        rendered = render_report("- [real] Safe `<img src=x onerror=alert(1)>`")

        self.assertIn('<span class="tag real">real</span>', rendered)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", rendered)
        self.assertNotIn("<img", rendered)

    def test_ticket_markup_is_rich_but_does_not_invent_provenance(self) -> None:
        rendered = render_ticket_text(
            "[real] Tracker copy\n\n1. Visit [source](https://example.com)\n\n```\n<script>\n```"
        )

        self.assertIn("<p>[real] Tracker copy</p>", rendered)
        self.assertNotIn('<span class="tag real">', rendered)
        self.assertIn('<ol>\n<li>Visit <a href="https://example.com" rel="noreferrer">source</a></li>\n</ol>', rendered)
        self.assertIn("<pre><code>&lt;script&gt;</code></pre>", rendered)

    def test_legacy_report_gets_category_previews_without_inventing_a_brief(self) -> None:
        report = next(layout.paths(self.bunker)["children"].glob("*.md"))
        text = report.read_text(encoding="utf-8")
        legacy = "## Who\n" + text.split("## Who\n", 1)[1]
        report.write_text(legacy, encoding="utf-8")

        rendered = page_ticket(self.root, "TB-9", "fingerprint")
        self.assertIsNotNone(rendered)
        page = rendered.decode("utf-8")

        self.assertIn("Agent work", page)
        self.assertIn("Grace authored the branch", page)
        self.assertEqual(page.count("Full trail"), 8)
        self.assertIn("Report state", page)
        self.assertNotIn("Reader brief", page)

    def test_master_separates_each_branch_and_selector_exposes_each_report(self) -> None:
        paths = layout.paths(self.bunker)
        branch = "lin/TB-9-second-view"
        resolution = json.loads(paths["branch_resolution"].read_text(encoding="utf-8"))
        resolution["branches"].append(branch)
        paths["branch_resolution"].write_text(json.dumps(resolution), encoding="utf-8")
        merge = json.loads(paths["merge_history"].read_text(encoding="utf-8"))
        merge["per_branch"][branch] = {
            "ahead_of_merge_base": 2,
            "behind_baseline": 0,
            "merged_back": False,
        }
        paths["merge_history"].write_text(json.dumps(merge), encoding="utf-8")
        authorship = json.loads(paths["authorship"].read_text(encoding="utf-8"))
        authorship["per_branch"][branch] = {
            "authors": {"Lin": {}},
            "recency_of_development": "2026-08-13T10:00:00+00:00",
        }
        paths["authorship"].write_text(json.dumps(authorship), encoding="utf-8")
        first_report = next(paths["children"].glob("*.md")).read_text(encoding="utf-8")
        layout.child_report(self.bunker, branch).write_text(
            first_report.replace("Grace", "Lin").replace("grace/TB-9-viewer", branch),
            encoding="utf-8",
        )

        rendered = page_ticket(self.root, "TB-9", "fingerprint")
        self.assertIsNotNone(rendered)
        page = rendered.decode("utf-8")

        self.assertIn('<option value="lin__TB-9-second-view">lin/TB-9-second-view</option>', page)
        self.assertIn('id="report-lin__TB-9-second-view" data-report-panel="lin__TB-9-second-view" hidden', page)
        self.assertEqual(page.count('<article class="master-branch-summary">'), 8)
        self.assertIn(".master-branch-summary + .master-branch-summary { border-top: 1px solid var(--line-strong); }", page)
        self.assertIn('href="#report-lin__TB-9-second-view"', page)


if __name__ == "__main__":
    unittest.main()
