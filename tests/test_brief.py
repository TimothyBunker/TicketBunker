from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ticketbunker import layout
from ticketbunker.builders import master, scaffold
from ticketbunker.processors import reportlint
from tools.stub_agent import fill


EVIDENCE = """\
## Who

- [observation] Tim is the assignee (ticket config).

## What

- [observation] One file changed (diff).

## Where

- [real] The change is in `ticketbunker/viewer.py:1`.

## Why

- [observation] The ticket asks for a clearer report (ticket).

## State

- [observation] The branch is open (merge_history).
"""


def report_with(brief: str) -> str:
    return f"## Reader brief\n\n{brief.strip()}\n\n{EVIDENCE}"


class ReaderBriefTests(unittest.TestCase):
    def test_scaffold_and_stub_produce_a_complete_gated_brief(self) -> None:
        form = scaffold.build(
            {"assignee": "Tim", "reporter": "Ada", "title": "Brief", "description": "Readable evidence"},
            {"merge_history": {}, "authorship": {}, "diff": {}, "work_separation": {}},
            "tim/TB-1-brief",
        )

        self.assertIn("## Reader brief", form)
        self.assertEqual(form.count("reader brief "), 4)
        self.assertEqual(form.count("highest apparent importance first"), 3)
        filled = fill(form)
        ok, findings = reportlint.gate(filled)

        self.assertNotIn("<!-- agent:", filled)
        self.assertTrue(ok, [str(finding) for finding in findings])

    def test_brief_is_compact_and_cannot_mint_observations(self) -> None:
        brief = """
### Who
- [observation] Tim is the most important person.
### What
- [speculation] grounded: the diff clarifies the viewer (observations/diff.json).
### Where
- [real] Start with `ticketbunker/viewer.py:1`.
### Why
- [speculation] grounded: the ticket needs readable evidence (ticket, observations/diff.json).
"""

        ok, findings = reportlint.gate(report_with(brief))

        self.assertFalse(ok)
        self.assertTrue(any(finding.check == "brief-provenance" for finding in findings))

    def test_brief_requires_all_four_parts(self) -> None:
        brief = """
### Who
- [speculation] grounded: Tim owns the ticket (ticket, authorship).
### What
- [speculation] grounded: the diff clarifies the viewer (observations/diff.json).
### Where
- [real] Start with `ticketbunker/viewer.py:1`.
"""

        ok, findings = reportlint.gate(report_with(brief))

        self.assertFalse(ok)
        self.assertTrue(any("missing '### Why'" in finding.message for finding in findings))

    def test_where_is_one_prioritized_claim_not_a_second_path_inventory(self) -> None:
        brief = """
### Who
- [speculation] grounded: Tim owns the ticket (ticket, authorship).
### What
- [speculation] grounded: the diff clarifies the viewer (observations/diff.json).
### Where
- [real] Start with `ticketbunker/viewer.py:1`.
- [real] Then inspect `ticketbunker/api.py:1`.
### Why
- [speculation] grounded: the ticket needs readable evidence (ticket, observations/diff.json).
"""

        ok, findings = reportlint.gate(report_with(brief))

        self.assertFalse(ok)
        self.assertTrue(any("Reader brief 'Where' has 2 claims (max 1)" in finding.message for finding in findings))

    def test_legacy_report_without_brief_remains_admissible(self) -> None:
        ok, findings = reportlint.gate(EVIDENCE)

        self.assertTrue(ok, [str(finding) for finding in findings])

    def test_master_digest_copies_brief_then_state_without_rewriting(self) -> None:
        brief = """
### Who
- [speculation] grounded: Tim owns the ticket (ticket, authorship).
### What
- [speculation] grounded: the diff clarifies the viewer (observations/diff.json).
### Where
- [real] Start with `ticketbunker/viewer.py:1`.
### Why
- [speculation] grounded: the ticket needs readable evidence (ticket, observations/diff.json).
"""
        with tempfile.TemporaryDirectory() as tmp:
            bunker = Path(tmp) / "TB-1_bunker"
            layout.init_dirs(bunker)
            layout.child_report(bunker, "tim/TB-1-brief").write_text(
                report_with(brief), encoding="utf-8"
            )

            master_text = master.regenerate(bunker).read_text(encoding="utf-8")

        digest = master_text.split("## Digest", 1)[1].split("---", 1)[0]
        self.assertIn("### Who", digest)
        self.assertIn("Tim owns the ticket", digest)
        self.assertIn("**State**", digest)
        self.assertIn("The branch is open", digest)


if __name__ == "__main__":
    unittest.main()


class WhoAndProactiveClaimTests(unittest.TestCase):
    def brief(self, who: str, what: str = "- [speculation] grounded — one file changed (diff).") -> str:
        return (
            "## Reader brief\n\n### Who\n\n" + who + "\n\n### What\n\n" + what
            + "\n\n### Where\n\n- [real] Start in `ticketbunker/viewer.py:1`.\n\n### Why\n\n"
            "- [speculation] grounded — the ticket wants a clearer report (ticket).\n\n" + EVIDENCE
        )

    def test_who_real_claim_may_rest_on_ticket_or_people_evidence(self) -> None:
        text = self.brief("- [real] Tim is the assignee and the only author so far (ticket).")
        checks = {f.check for f in reportlint.lint(text) if f.severity == "error"}
        self.assertNotIn("real-anchor", checks)
        text = self.brief("- [real] Grace owns the parser this branch touches (people).")
        self.assertNotIn("real-anchor", {f.check for f in reportlint.lint(text)})
        text = self.brief("- [real] Grace owns the parser this branch touches.")
        self.assertIn("real-anchor", {f.check for f in reportlint.lint(text)})

    def test_who_warns_when_a_claim_says_nobody_instead_of_naming_someone(self) -> None:
        text = self.brief("- [real] `src/parse.py:7-42` identifies no contributor beyond the assignee (people).")
        findings = [f for f in reportlint.lint(text) if f.check == "who-people"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warn")
        ok, _ = reportlint.gate(text)
        self.assertTrue(ok)

    def test_each_brief_part_allows_one_risk_and_one_next_on_top(self) -> None:
        who = (
            "- [real] Tim is the assignee (ticket).\n"
            "- [real] Grace owns the parser (people).\n"
            "- [speculation] grounded — Risk: nobody has reviewed parser changes since Grace left (people).\n"
            "- [real] Next: ask Grace to review (people)."
        )
        text = self.brief(who)
        self.assertNotIn("brief-length", {f.check for f in reportlint.lint(text)})
        text = self.brief(who + "\n- [real] Next: also ping Lin (people).")
        messages = [f.message for f in reportlint.lint(text) if f.check == "brief-length"]
        self.assertTrue(any("Next: claims" in m for m in messages), messages)
        text = self.brief(who + "\n- [real] Lin spiked the same ticket (people).")
        messages = [f.message for f in reportlint.lint(text) if f.check == "brief-length"]
        self.assertTrue(any("has 3 claims (max 2)" in m for m in messages), messages)
