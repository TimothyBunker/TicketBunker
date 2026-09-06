from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import agent_runtime, commands, coworker, layout, project, truth
from ticketbunker.builders import master
from ticketbunker.ledger import Ledger


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "TB_AGENT_DISABLE": "1"},
    )
    return completed.stdout.strip()


REPORT = """## Reader brief

### Who

- [speculation] grounded: Ada maintains this branch (observations/authorship.json).

### What

- [speculation] grounded: The branch documents its current behavior (observations/diff.json).

### Where

- [real] Start with `source.py:1`.

### Why

- [speculation] grounded: A concise report keeps the ticket readable (observations/diff.json).

## Who

- [observation] Ada authored the branch.

## What

- [observation] The branch has one source change.

## Where

- [real] The implementation is at `source.py:1`.

## Why

- [speculation] grounded: The ticket needs a checkable summary (observations/diff.json).

## State

- [observation] The branch report is published.
"""


class CoworkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Ada")
        git(self.root, "config", "user.email", "ada@example.com")
        (self.root / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(self.root, "add", "source.py")
        git(self.root, "commit", "-m", "Initial source")
        self.branch = "Ada/TB-9-report"
        git(self.root, "switch", "-c", self.branch)
        self.head = git(self.root, "rev-parse", "HEAD")

        self.bunker = layout.bunker_path(self.root, "TB-9")
        layout.init_dirs(self.bunker)
        paths = layout.paths(self.bunker)
        paths["ticket"].write_text(
            json.dumps(
                {
                    "id": "TB-9",
                    "key": "TB-9",
                    "title": "Keep one ticket conversation",
                    "status": "in-progress",
                    "repo": str(self.root),
                    "assigned_branches": [self.branch],
                }
            ),
            encoding="utf-8",
        )
        paths["branch_resolution"].write_text(
            json.dumps(
                {
                    "branches": [self.branch],
                    "assigned_missing": [],
                    "match_rules": {self.branch: "explicit assignment"},
                }
            ),
            encoding="utf-8",
        )
        (paths["observations"] / "baseline.json").write_text(
            json.dumps(
                {
                    "baseline": self.head,
                    "per_branch": {self.branch: {"merge_base": self.head}},
                }
            ),
            encoding="utf-8",
        )
        (paths["observations"] / "diff.json").write_text(
            json.dumps({"per_branch": {self.branch: {}}}), encoding="utf-8"
        )
        paths["authorship"].write_text(
            json.dumps({"per_branch": {self.branch: {"authors": {"Ada": {}}}}}),
            encoding="utf-8",
        )
        layout.child_report(self.bunker, self.branch).write_text(REPORT, encoding="utf-8")
        master.regenerate(self.bunker, Ledger(paths["ledger"]))
        self.config_path = self.root / "local-config.json"
        self.config_path.write_text("{}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def readiness() -> dict[str, object]:
        return {
            "ready": True,
            "enabled": True,
            "summary": "Pi ready",
            "action": "",
            "provider": "openai-codex",
            "model": "gpt-5.6-sol",
            "runtime": {"path": "/managed/pi"},
        }

    def test_four_views_share_one_local_ticket_session_and_propose_without_writing(self) -> None:
        before_report = layout.child_report(self.bunker, self.branch).read_text(encoding="utf-8")
        before_source = (self.root / "source.py").read_text(encoding="utf-8")
        response = {
            "reply": "The current summary is accurate, but it can state the boundary more directly.",
            "proposal": {
                "category": "what",
                "changes": [
                    {
                        "branch": self.branch,
                        "proposed_synthesis": (
                            "- [speculation] grounded: The coworker maintains only the ticket report "
                            "(observations/diff.json)."
                        ),
                    }
                ],
            },
        }
        context = commands.CommandContext(self.root, transport="test")
        with mock.patch.dict(os.environ, {"TB_CONFIG": str(self.config_path)}):
            with mock.patch.object(coworker, "_run_pi", return_value=(response, {"harness": "pi"})):
                with mock.patch.object(agent_runtime, "state", return_value=self.readiness()):
                    result = commands.dispatch(
                        "coworker.chat",
                        {
                            "ticket": "TB-9",
                            "category": "what",
                            "scope": "combined",
                            "message": "Make the work boundary clearer.",
                        },
                        context,
                    )
                    who = coworker.state(self.root, "", "TB-9", category="who", scope="combined")

        self.assertEqual(len(result["messages"]), 2)
        self.assertEqual(who["messages"], [])
        identity = coworker._user(self.root)
        self.assertEqual(result["title"], f"{identity} · TB-9")
        self.assertEqual([scope["value"] for scope in result["scopes"]], ["combined", layout.branch_slug(self.branch)])
        self.assertEqual(len(result["proposals"]), 1)
        self.assertEqual((self.root / "source.py").read_text(encoding="utf-8"), before_source)
        self.assertEqual(layout.child_report(self.bunker, self.branch).read_text(encoding="utf-8"), before_report)
        session = coworker._root(self.root, "", "TB-9", identity)
        self.assertTrue((session / "messages.jsonl").is_file())
        self.assertEqual(
            session.relative_to(coworker._git_dir(self.root)).parts[0],
            "ticketbunker",
        )

    def test_selected_synthesis_reference_is_validated_persisted_and_sent_to_pi(self) -> None:
        reference = {
            "category": "where",
            "scope": "combined",
            "branch": self.branch,
            "text": "Start with @[source.py]:1.",
        }
        response = {"reply": "That location contains the current implementation.", "proposal": None}
        context = commands.CommandContext(self.root, transport="test")
        with mock.patch.object(
            coworker, "_run_pi", return_value=(response, {"harness": "pi"})
        ) as run_pi:
            with mock.patch.object(agent_runtime, "state", return_value=self.readiness()):
                result = commands.dispatch(
                    "coworker.chat",
                    {
                        "ticket": "TB-9",
                        "category": "where",
                        "scope": "combined",
                        "message": "Why is this the first place to look?",
                        "reference": reference,
                    },
                    context,
                )

        self.assertEqual(result["messages"][0]["reference"], reference)
        self.assertEqual(run_pi.call_args.args[-1], reference)

    def test_selected_source_reference_is_guarded_persisted_and_sent_to_pi(self) -> None:
        reference = {
            "kind": "source",
            "category": "what",
            "scope": "combined",
            "branch": "",
            "path": "source.py",
            "line_start": 1,
            "line_end": 1,
            "unsaved": False,
            "text": "VALUE = 1",
        }
        response = {"reply": "That selection defines the current value.", "proposal": None}
        with mock.patch.object(
            coworker, "_run_pi", return_value=(response, {"harness": "pi"})
        ) as run_pi:
            with mock.patch.object(agent_runtime, "state", return_value=self.readiness()):
                result = commands.dispatch(
                    "coworker.chat",
                    {
                        "ticket": "TB-9",
                        "category": "what",
                        "scope": "combined",
                        "message": "Explain this source selection.",
                        "reference": reference,
                    },
                    commands.CommandContext(self.root, transport="test"),
                )

        self.assertEqual(result["messages"][0]["reference"], reference)
        self.assertEqual(run_pi.call_args.args[-1], reference)

    def test_rejected_generated_proposal_keeps_validator_details_out_of_chat(self) -> None:
        response = {
            "reply": "I prepared a report correction.",
            "proposal": {
                "category": "what",
                "changes": [
                    {
                        "branch": self.branch,
                        "proposed_synthesis": "- [real] The report correction is complete.",
                    }
                ],
            },
        }
        context = commands.CommandContext(self.root, transport="test")
        with mock.patch.object(coworker, "_run_pi", return_value=(response, {"harness": "pi"})):
            with mock.patch.object(agent_runtime, "state", return_value=self.readiness()):
                result = commands.dispatch(
                    "coworker.chat",
                    {
                        "ticket": "TB-9",
                        "category": "what",
                        "scope": "combined",
                        "message": "Correct the report.",
                    },
                    context,
                )

        visible = result["messages"][-1]["text"]
        self.assertIn("did not pass TicketBunker's report checks", visible)
        self.assertIn("No report changed", visible)
        self.assertNotIn("real-anchor", visible)
        self.assertNotIn("[error]", visible)
        self.assertEqual(result["proposals"], [])
        session = coworker._root(self.root, "", "TB-9")
        diagnostics_path = session / "diagnostics.jsonl"
        diagnostics = diagnostics_path.read_text(encoding="utf-8")
        self.assertIn("real-anchor", diagnostics)
        if os.name != "nt":
            self.assertEqual(diagnostics_path.stat().st_mode & 0o777, 0o600)

    def test_selected_synthesis_reference_cannot_cross_topic_or_scope(self) -> None:
        with self.assertRaises(commands.CommandRejected) as caught:
            commands.dispatch(
                "coworker.chat",
                {
                    "ticket": "TB-9",
                    "category": "why",
                    "scope": "combined",
                    "message": "Explain this.",
                    "reference": {
                        "category": "what",
                        "scope": "combined",
                        "text": "The viewer reads one snapshot.",
                    },
                },
                commands.CommandContext(self.root, transport="test"),
            )

        self.assertEqual(caught.exception.code, "coworker_reference_scope_changed")

    def test_accept_updates_only_the_child_brief_and_regenerates_combined(self) -> None:
        response = {
            "reply": "I prepared a narrower What synthesis.",
            "proposal": {
                "category": "what",
                "changes": [
                    {
                        "branch": self.branch,
                        "proposed_synthesis": (
                            "- [speculation] grounded: The accepted report update stays inside the "
                            "ticket layer (observations/diff.json)."
                        ),
                    }
                ],
            },
        }
        context = commands.CommandContext(self.root, transport="test")
        with mock.patch.dict(os.environ, {"TB_CONFIG": str(self.config_path)}):
            with mock.patch.object(coworker, "_run_pi", return_value=(response, {"harness": "pi"})):
                with mock.patch.object(agent_runtime, "state", return_value=self.readiness()):
                    chat = commands.dispatch(
                        "coworker.chat",
                        {
                            "ticket": "TB-9",
                            "category": "what",
                            "scope": "combined",
                            "message": "Narrow the What summary.",
                        },
                        context,
                    )
            proposal_id = chat["proposals"][0]["id"]
            before = layout.child_report(self.bunker, self.branch).read_text(encoding="utf-8")
            evidence_before = coworker._section(before, "What")
            with mock.patch.object(project, "publish_data", return_value={"published": True}):
                accepted = commands.dispatch(
                    "coworker.proposal.accept",
                    {"ticket": "TB-9", "proposal": proposal_id},
                    context,
                )

        after = layout.child_report(self.bunker, self.branch).read_text(encoding="utf-8")
        combined = layout.paths(self.bunker)["master"].read_text(encoding="utf-8")
        self.assertIn("accepted report update stays inside", coworker._subsection(after, "Reader brief", "What"))
        self.assertEqual(coworker._section(after, "What"), evidence_before)
        self.assertIn("accepted report update stays inside", combined)
        self.assertEqual(accepted["proposal"]["status"], "accepted")
        self.assertTrue(accepted["combined_regenerated"])
        self.assertNotIn("source.py", git(self.root, "status", "--porcelain"))

    def test_rejected_proposal_edit_keeps_validator_details_out_of_command_error(self) -> None:
        response = {
            "reply": "I prepared a narrower What synthesis.",
            "proposal": {
                "category": "what",
                "changes": [
                    {
                        "branch": self.branch,
                        "proposed_synthesis": (
                            "- [speculation] grounded: The report stays evidence bounded "
                            "(observations/diff.json)."
                        ),
                    }
                ],
            },
        }
        context = commands.CommandContext(self.root, transport="test")
        with mock.patch.object(coworker, "_run_pi", return_value=(response, {"harness": "pi"})):
            with mock.patch.object(agent_runtime, "state", return_value=self.readiness()):
                chat = commands.dispatch(
                    "coworker.chat",
                    {
                        "ticket": "TB-9",
                        "category": "what",
                        "scope": "combined",
                        "message": "Prepare a report update.",
                    },
                    context,
                )

        with self.assertRaises(commands.CommandRejected) as caught:
            commands.dispatch(
                "coworker.proposal.accept",
                {
                    "ticket": "TB-9",
                    "proposal": chat["proposals"][0]["id"],
                    "edits": {self.branch: "- [real] This edit has no checkable anchor."},
                },
                context,
            )

        self.assertIn("did not pass TicketBunker's report checks", caught.exception.message)
        self.assertNotIn("real-anchor", caught.exception.message)
        self.assertNotIn("[error]", caught.exception.message)
        diagnostics = (
            coworker._root(self.root, "", "TB-9") / "diagnostics.jsonl"
        ).read_text(encoding="utf-8")
        self.assertIn("real-anchor", diagnostics)

    def test_pi_runtime_is_read_only_and_reuses_one_session_directory(self) -> None:
        original_run = subprocess.run
        pi_calls: list[tuple[list[str], dict[str, str]]] = []

        def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
            if argv and argv[0] == "/managed/pi":
                environment = dict(kwargs.get("env") or {})
                pi_calls.append((list(argv), environment))
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=b'{"reply":"Here is the ticket context.","proposal":null}',
                    stderr=b"",
                )
            return original_run(argv, **kwargs)

        with mock.patch.dict(os.environ, {"TB_CONFIG": str(self.config_path)}):
            with mock.patch.object(agent_runtime, "state", return_value=self.readiness()):
                with mock.patch.object(agent_runtime, "extension_path", return_value=self.root / "tb.ts"):
                    with mock.patch.object(coworker.subprocess, "run", side_effect=run):
                        coworker._run_pi(
                            self.root,
                            "",
                            coworker._ticket(self.root, "", "TB-9"),
                            "who",
                            "combined",
                            "Who owns this?",
                            {
                                "category": "who",
                                "scope": "combined",
                                "branch": self.branch,
                                "text": "Ada maintains this branch.",
                            },
                        )
                        coworker._run_pi(self.root, "", coworker._ticket(self.root, "", "TB-9"), "why", layout.branch_slug(self.branch), "Why this approach?")

        self.assertEqual(len(pi_calls), 2)
        first, second = (call[0] for call in pi_calls)
        self.assertIn("--continue", first)
        self.assertEqual(first[first.index("--session-dir") + 1], second[second.index("--session-dir") + 1])
        self.assertEqual(first[first.index("--tools") + 1], "read,grep,find,ls")
        self.assertNotIn("bash", first[first.index("--tools") + 1].split(","))
        self.assertIn("[USER-SELECTED-SYNTHESIS]", first[-1])
        self.assertIn("Ada maintains this branch.", first[-1])
        self.assertEqual(pi_calls[0][1]["TB_BUNKER"], pi_calls[1][1]["TB_BUNKER"])
        self.assertNotEqual(pi_calls[0][1]["TB_BRANCH"], pi_calls[1][1]["TB_BRANCH"])

    def test_failed_companion_publish_keeps_the_accepted_local_state_actionable(self) -> None:
        response = {
            "reply": "I prepared the correction.",
            "proposal": {
                "category": "why",
                "changes": [
                    {
                        "branch": self.branch,
                        "proposed_synthesis": (
                            "- [speculation] grounded: The ticket needs an evidence-bounded reason "
                            "(observations/diff.json)."
                        ),
                    }
                ],
            },
        }
        context = commands.CommandContext(self.root, transport="test")
        with mock.patch.object(coworker, "_run_pi", return_value=(response, {"harness": "pi"})):
            with mock.patch.object(agent_runtime, "state", return_value=self.readiness()):
                chat = commands.dispatch(
                    "coworker.chat",
                    {
                        "ticket": "TB-9",
                        "category": "why",
                        "scope": "combined",
                        "message": "Correct the reason.",
                    },
                    context,
                )
        failure = commands.CommandRejected(
            "data_publish_rejected",
            "The accepted report is committed locally but did not reach the companion remote.\n"
            "Next: Run tb sync when remote access is restored.\n"
            "Git said: credential detail that must stay local",
        )
        with mock.patch.object(project, "publish_data", side_effect=failure):
            accepted = commands.dispatch(
                "coworker.proposal.accept",
                {"ticket": "TB-9", "proposal": chat["proposals"][0]["id"]},
                context,
            )

        visible = truth.state(self.root, "", "TB-9")
        self.assertEqual(accepted["proposal"]["status"], "accepted")
        self.assertTrue(accepted["publication"]["pending"])
        self.assertNotIn("credential detail", accepted["publication_warning"])
        self.assertEqual(visible["companion"]["label"], "Companion publish pending")
        self.assertIn("tb sync", visible["companion"]["action"])


if __name__ == "__main__":
    unittest.main()


class StandingTopicTests(unittest.TestCase):
    BRIEF = (
        "## Reader brief\n\n### Standing\n\n- [speculation] grounded — Verdict: blocked — it does not build (a.cpp:1).\n\n"
        "### Who\n\n- [real] Tim (ticket).\n\n### What\n\n- [real] Defect: dup (a.cpp:21).\n\n### Where\n\n- [real] Start (a.cpp:1).\n\n"
        "### Why\n\n- [speculation] grounded — because (ticket).\n\n### Checklist\n\n- [real] Missing: x (ticket).\n\n"
        "### Plan\n\n- [real] Step: fix dup (a.cpp:21).\n\n## Who\n\n- [observation] Tim (ticket config).\n\n"
        "## What\n\n- [real] Defect: dup (a.cpp:21).\n\n## Where\n\n- [observation] none (diff).\n\n## Why\n\n- [observation] t (ticket).\n\n## State\n\n- [observation] s (merge_history).\n"
    )

    def test_standing_proposals_name_their_part_and_replace_only_it(self) -> None:
        self.assertEqual(coworker._proposal_part("standing", None), "Standing")
        self.assertEqual(coworker._proposal_part("standing", "plan"), "Plan")
        self.assertIsNone(coworker._proposal_part("standing", "who"))
        self.assertEqual(coworker._proposal_part("what", "plan"), "What")
        patched = coworker._replace_brief(self.BRIEF, "Plan", "- [real] Step: fix dup (a.cpp:21).\n- [speculation] grounded — Step: rebuild (check).")
        self.assertIn("Step: rebuild (check)", coworker._subsection(patched, "Reader brief", "Plan"))
        self.assertEqual(coworker._subsection(patched, "Reader brief", "Standing"), coworker._subsection(self.BRIEF, "Reader brief", "Standing"))
        self.assertIn("standing", coworker.CATEGORIES)


class FileThreadTests(unittest.TestCase):
    def test_file_threads_are_keyed_by_path_and_listed(self) -> None:
        self.assertIn("file", coworker.CATEGORIES)
        self.assertEqual(coworker._thread_path({"path": "./coal/a.cpp"}, "file"), "coal/a.cpp")
        self.assertEqual(coworker._thread_path({"path": "coal/a.cpp"}, "what"), "")
        with self.assertRaises(commands.CommandRejected):
            coworker._thread_path({}, "file")
        self.assertIsNone(coworker._proposal_part("file", "Plan"))
        proposal, warning, details = coworker._prepare_proposal(Path("/tmp"), mock.Mock(reports=()), "file", "combined", {"category": "what", "changes": []}, {})
        self.assertEqual((proposal, warning, details), (None, "", []))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            session = coworker._root(root, "ECS", "ECS-4", user="Ada")
            session.mkdir(parents=True)
            rows = [
                {"role": "user", "category": "file", "scope": "combined", "path": "coal/a.cpp", "text": "why twice?", "created_at": "t"},
                {"role": "user", "category": "file", "scope": "combined", "path": "coal/b.h", "text": "hm", "created_at": "t"},
                {"role": "user", "category": "what", "scope": "combined", "text": "general", "created_at": "t"},
            ]
            (session / "messages.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            with mock.patch.object(coworker, "_user", return_value="Ada"), mock.patch.object(coworker, "_ticket", return_value=mock.Mock(id="ECS-4", data={}, reports=())), mock.patch.object(coworker.agent_runtime, "state", return_value={"ready": False}):
                value = coworker.state(root, "ECS", "ECS-4", category="file", scope="combined", path="coal/a.cpp")
        self.assertEqual([m["text"] for m in value["messages"]], ["why twice?"])
        self.assertEqual(value["files"], ["coal/a.cpp", "coal/b.h"])
        self.assertEqual(value["path"], "coal/a.cpp")
