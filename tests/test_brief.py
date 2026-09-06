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
        self.assertEqual(form.count("reader brief "), 7)  # Standing, Who, What, Where, Why, Checklist, Plan
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

    def test_who_citation_may_be_a_list(self) -> None:
        text = self.brief("- [real] TimothyBunker is the assignee, reporter, and only author (ticket, authorship).")
        self.assertNotIn("real-anchor", {f.check for f in reportlint.lint(text)})

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


class StandingAndChecklistTests(unittest.TestCase):
    def brief(self, standing: str = "", checklist: str = "", what: str = "- [speculation] grounded — one file changed (diff).") -> str:
        parts = "## Reader brief\n\n"
        if standing:
            parts += "### Standing\n\n" + standing + "\n\n"
        parts += (
            "### Who\n\n- [real] Tim is the assignee (ticket).\n\n"
            "### What\n\n" + what + "\n\n"
            "### Where\n\n- [real] Start in `ticketbunker/viewer.py:1`.\n\n"
            "### Why\n\n- [speculation] grounded — the ticket wants a clearer report (ticket).\n\n"
        )
        if checklist:
            parts += "### Checklist\n\n" + checklist + "\n\n"
        return parts + EVIDENCE

    def test_standing_opens_with_one_verdict_and_allows_next_and_ask(self) -> None:
        good = (
            "- [speculation] grounded — Verdict: blocked — PositionComponent does not compile (`ticketbunker/viewer.py:1`).\n"
            "- [real] Next: fix the constructor mismatch first (`ticketbunker/viewer.py:1`).\n"
            "- [real] Ask: TimothyBunker, the only person who has touched it (people)."
        )
        ok, findings = reportlint.gate(self.brief(standing=good))
        self.assertTrue(ok, [str(f) for f in findings])
        checks = {f.check for f in reportlint.lint(self.brief(standing="- [real] Next: do it (`ticketbunker/viewer.py:1`)."))}
        self.assertIn("standing", checks)
        two = good + "\n- [speculation] grounded — Verdict: in progress — also this (`ticketbunker/viewer.py:1`)."
        self.assertIn("standing", {f.check for f in reportlint.lint(self.brief(standing=two))})
        stray = good + "\n- [real] An extra sentence (`ticketbunker/viewer.py:1`)."
        self.assertIn("standing", {f.check for f in reportlint.lint(self.brief(standing=stray))})
        bad_value = "- [speculation] grounded — Verdict: stuck — nope (`ticketbunker/viewer.py:1`)."
        self.assertIn("standing", {f.check for f in reportlint.lint(self.brief(standing=bad_value))})

    def test_a_defect_is_always_verified_and_anchored(self) -> None:
        good = self.brief(what="- [speculation] grounded — one file changed (diff).\n- [real] Defect: the Pos3 constructor is defined twice (`ticketbunker/viewer.py:1`).")
        self.assertNotIn("defect", {f.check for f in reportlint.lint(good)})
        speculative = self.brief(what="- [speculation] grounded — Defect: probably defined twice (`ticketbunker/viewer.py:1`).")
        self.assertIn("defect", {f.check for f in reportlint.lint(speculative)})
        unanchored = self.brief(what="- [real] Defect: defined twice somewhere.")
        checks = {f.check for f in reportlint.lint(unanchored)}
        self.assertIn("defect", checks)
        two = self.brief(what="- [real] Defect: one (`ticketbunker/viewer.py:1`).\n- [real] Defect: two (`ticketbunker/viewer.py:1`).")
        self.assertTrue(any("Defect: claims" in f.message for f in reportlint.lint(two)))

    def test_plan_is_one_to_seven_ordered_steps(self) -> None:
        good = "### Plan\n\n- [real] Step: remove the duplicate constructor (`ticketbunker/viewer.py:1`).\n- [speculation] grounded — Step: run the build (check).\n\n"
        text = self.brief().replace("\n## Who\n", "\n" + good + "## Who\n", 1)
        ok, findings = reportlint.gate(text)
        self.assertTrue(ok, [str(f) for f in findings])
        unlabeled = text.replace("- [real] Step: remove", "- [real] Remove")
        self.assertIn("plan", {f.check for f in reportlint.lint(unlabeled)})
        too_many = text.replace(good, "### Plan\n\n" + "- [real] Step: x (`ticketbunker/viewer.py:1`).\n" * 8 + "\n")
        self.assertTrue(any("max 7" in f.message for f in reportlint.lint(too_many)))
        empty = text.replace(good, "### Plan\n\nNothing to do.\n\n")
        self.assertIn("plan", {f.check for f in reportlint.lint(empty)})

    def test_checklist_marks_each_item(self) -> None:
        good = (
            "- [real] Done: CommonTypes (`ticketbunker/viewer.py:1`).\n"
            "- [speculation] grounded — Partial: Coordinates for general Pos3 (`ticketbunker/viewer.py:1`).\n"
            "- [real] Missing: WorldPos3 and LocalPos3 (ticket)."
        )
        ok, findings = reportlint.gate(self.brief(checklist=good))
        self.assertTrue(ok, [str(f) for f in findings])
        unmarked = good + "\n- [real] Something else (`ticketbunker/viewer.py:1`)."
        self.assertIn("checklist", {f.check for f in reportlint.lint(self.brief(checklist=unmarked))})
        self.assertIn("checklist", {f.check for f in reportlint.lint(self.brief(checklist="- [real] Done: x (`ticketbunker/viewer.py:1`).\n" * 13))})

    def test_scaffold_offers_standing_and_a_checklist_from_the_ticket(self) -> None:
        ticket = {
            "id": "ECS-2",
            "title": "Position Component Implementation",
            "description": "[8/21/26]\n- Implemented:\n  - CommonTypes\n  - Coordinates for general Pos3, not yet WorldPos3 and LocalPos3 which is todo\n  - Implemented basic components\n",
            "acceptance_criteria": ["Position component compiles"],
        }
        form = scaffold.build(ticket, {}, "bunker/ECS-2")
        brief = form.split("\n## Who\n", 1)[0]
        self.assertLess(brief.index("### Standing"), brief.index("### Who"))
        self.assertIn("Verdict:", brief)
        self.assertIn("### Checklist", brief)
        self.assertIn("1) Position component compiles; 2) Implemented › CommonTypes", brief)
        self.assertIn("### Plan", brief)
        self.assertLess(brief.index("### Checklist"), brief.index("### Plan"))
        self.assertIn("### Plan", scaffold.build({"id": "X-1"}, {}, "b"))
        self.assertNotIn("1) [8/21/26]", brief)
        self.assertEqual(scaffold.checklist_items({"description": "Just a sentence here."}), ["Just a sentence here."])
        self.assertEqual(scaffold.checklist_items({"description": ""}), [])
        self.assertNotIn("### Checklist", scaffold.build({"id": "X-1"}, {}, "b"))
