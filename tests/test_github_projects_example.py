from __future__ import annotations

import unittest

from examples.github_projects.import_issue import normalize_issue, parse_issue


class GitHubProjectsExampleTests(unittest.TestCase):
    def test_live_shape_normalizes_without_coupling_core_to_github(self) -> None:
        issue = {
            "node_id": "I_1",
            "number": 4,
            "title": "ECS-4 Position System Implementation",
            "body": "Apply velocity to position.",
            "state": "open",
            "state_reason": None,
            "html_url": "https://github.com/TimothyBunker/coal/issues/4",
            "created_at": "2026-08-20T12:00:00Z",
            "updated_at": "2026-08-21T12:00:00Z",
            "closed_at": None,
            "author_association": "OWNER",
            "locked": False,
            "user": {"login": "TimothyBunker"},
            "assignees": [{"login": "TimothyBunker"}, {"login": "Ada"}],
            "labels": [{"name": "ECSF"}, {"name": "C0"}],
            "milestone": None,
        }
        comments = [
            {
                "user": {"login": "Ada"},
                "body": "The order matters.",
                "created_at": "2026-08-21T13:00:00Z",
                "html_url": "https://github.com/TimothyBunker/coal/issues/4#issuecomment-2",
            }
        ]
        project = {
            "owner": "TimothyBunker",
            "number": 8,
            "title": "@TimothyBunker's ECS",
            "url": "https://github.com/users/TimothyBunker/projects/8",
            "item_id": "PVTI_4",
            "fields": {"Status": "In Progress"},
        }

        ticket = normalize_issue(issue, comments, project)

        self.assertEqual(ticket["id"], "ECS-4")
        self.assertEqual(ticket["title"], "Position System Implementation")
        self.assertEqual(ticket["status"], "in-progress")
        self.assertEqual(ticket["assignee"], "TimothyBunker")
        self.assertEqual(ticket["people_of_interest"], ["Ada"])
        self.assertEqual(ticket["comments"][0]["author"], "Ada")
        self.assertEqual(ticket["metadata"]["github"]["project"]["number"], 8)

    def test_issue_reference_forms_are_direct(self) -> None:
        expected = ("TimothyBunker", "coal", 4)
        self.assertEqual(parse_issue("https://github.com/TimothyBunker/coal/issues/4"), expected)
        self.assertEqual(parse_issue("TimothyBunker/coal#4"), expected)


if __name__ == "__main__":
    unittest.main()
