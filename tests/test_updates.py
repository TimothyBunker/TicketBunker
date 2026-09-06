from __future__ import annotations

import unittest

from ticketbunker import updates

REPORT = """\
<!-- tb:verified branch=bunker/ECS-2 commit=53481ef ts=2026-09-06T04:38:02+00:00 harness=pi provider=x model=y -->
## Reader brief

### Standing

- [speculation] grounded — Verdict: blocked — PositionComponent does not compile: duplicate constructor at @[coal/components/position/PositionComponent.cpp]:21.
- [real] Next: fix the header/source mismatch first (@[coal/components/position/PositionComponent.h]:7).
- [real] Ask: TimothyBunker, the only person on it (people).

### Who

- [real] TimothyBunker is the assignee (ticket).

### What

- [real] Defect: @[coal/components/position/PositionComponent.cpp]:21 defines the Pos3 constructor twice.

### Checklist

- [real] Done: CommonTypes (@[coal/common/types/CommonTypes.h]:11).
- [speculation] grounded — Partial: Coordinates for general Pos3 (@[coal/common/types/CommonTypes.h]:84).
- [real] Missing: WorldPos3 and LocalPos3 (ticket).

## Who

- [observation] Assignee: TimothyBunker (ticket config).
"""


class ComposeUpdateTests(unittest.TestCase):
    def test_standing_and_checklist_become_plain_markdown(self) -> None:
        value = updates.compose(REPORT, ticket_id="ECS-2", branch="bunker/ECS-2", commit="53481efa04adc8929d")
        self.assertEqual(value["verdict"], "blocked")
        self.assertEqual(value["next"], "fix the header/source mismatch first (`coal/components/position/PositionComponent.h:7`).")
        self.assertEqual([item["mark"] for item in value["checklist"]], ["done", "partial", "missing"])
        text = value["text"]
        self.assertTrue(text.startswith("**Blocked** — PositionComponent does not compile: duplicate constructor at `coal/components/position/PositionComponent.cpp:21`."))
        self.assertIn("**Ask:** TimothyBunker, the only person on it (people).", text)
        self.assertIn("- ✅ CommonTypes (`coal/common/types/CommonTypes.h:11`).", text)
        self.assertIn("- ❌ WorldPos3 and LocalPos3 (ticket).", text)
        self.assertEqual(value["defects"], ["`coal/components/position/PositionComponent.cpp:21` defines the Pos3 constructor twice."])
        self.assertIn("**Defects found:**\n- `coal/components/position/PositionComponent.cpp:21` defines the Pos3 constructor twice.", text)
        self.assertIn("_Synthesized by TicketBunker for ECS-2 on `bunker/ECS-2` at `53481efa04ad`._", text)
        for token in ("[real]", "[speculation]", "grounded —", "@["):
            self.assertNotIn(token, text)

    def test_report_without_standing_yields_nothing_to_post(self) -> None:
        value = updates.compose("## Reader brief\n\n### Who\n\n- [real] Tim (ticket).\n", ticket_id="X-1", branch="b")
        self.assertEqual(value["text"], "")
        self.assertEqual(value["checklist"], [])


class UpdateCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        from ticketbunker import application, commands

        self.commands = commands
        self.application = application
        self.context = commands.CommandContext("/tmp")
        self.detail = {
            "ticket": {
                "id": "ECS-2",
                "source_kind": "github",
                "source_reference": "https://github.com/acme/coal/issues/2",
                "metadata": {"tracker": "github", "github": {"repository": "acme/coal", "issue_number": 2}},
            },
            "reports": [
                {"scope": "combined", "content": "# Master", "updated_at": "2026-09-06T05:00:00+00:00"},
                {"scope": "branch:bunker/ECS-2", "content": REPORT, "updated_at": "2026-09-06T04:38:02+00:00"},
            ],
        }

    def test_draft_composes_from_the_branch_report_and_names_the_target(self) -> None:
        from unittest import mock

        with mock.patch("ticketbunker.bunkerdb.get_ticket", return_value=self.detail), \
             mock.patch("ticketbunker.projectconfig.credential_value", return_value="ghp_x"):
            value = self.commands.dispatch("application.ticket.update.draft", {"bunker_id": "b", "ticket_id": "ECS-2"}, self.context)
        self.assertEqual(value["branch"], "bunker/ECS-2")
        self.assertEqual(value["target"]["label"], "acme/coal#2")
        self.assertTrue(value["can_post"])
        self.assertIn("**Blocked**", value["text"])
        self.assertIn("at `53481ef`", value["text"])

    def test_post_sends_the_edited_text_to_the_issue(self) -> None:
        from unittest import mock

        with mock.patch("ticketbunker.bunkerdb.get_ticket", return_value=self.detail), \
             mock.patch("ticketbunker.projectconfig.credential_value", return_value="ghp_x"), \
             mock.patch("ticketbunker.integrations.github.post_issue_comment", return_value={"url": "https://github.com/acme/coal/issues/2#issuecomment-9", "id": 9, "created_at": "now"}) as posted:
            value = self.commands.dispatch("application.ticket.update.post", {"bunker_id": "b", "ticket_id": "ECS-2", "text": "**Blocked** — edited by hand."}, self.context)
        posted.assert_called_once_with("acme/coal", 2, "**Blocked** — edited by hand.", "ghp_x")
        self.assertEqual(value["url"], "https://github.com/acme/coal/issues/2#issuecomment-9")

    def test_post_refuses_without_a_target_or_token(self) -> None:
        from unittest import mock

        local = {**self.detail, "ticket": {**self.detail["ticket"], "source_kind": "manual", "metadata": {}}}
        with mock.patch("ticketbunker.bunkerdb.get_ticket", return_value=local):
            with self.assertRaises(self.commands.CommandRejected) as caught:
                self.commands.dispatch("application.ticket.update.post", {"bunker_id": "b", "ticket_id": "ECS-2", "text": "x"}, self.context)
        self.assertEqual(caught.exception.code, "update_target_missing")
        with mock.patch("ticketbunker.bunkerdb.get_ticket", return_value=self.detail), \
             mock.patch("ticketbunker.projectconfig.credential_value", return_value=None):
            with self.assertRaises(self.commands.CommandRejected) as caught:
                self.commands.dispatch("application.ticket.update.post", {"bunker_id": "b", "ticket_id": "ECS-2", "text": "x"}, self.context)
        self.assertEqual(caught.exception.code, "github_credential_missing")
