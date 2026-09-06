from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ticketbunker.ledger import Ledger
from ticketbunker.processors.stages import run_bootstrap


class RepositoryOwnedDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        (self.repo / "src").mkdir()
        (self.repo / "src" / "main.cpp").write_text("int main() {}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "src/main.cpp"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "initial"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "switch", "-qc", "tim/TB-1-work"], check=True)
        bunker = self.repo / ".tb" / "TB-1_bunker"
        bunker.mkdir(parents=True)
        (bunker / "ticket.json").write_text('{"id":"TB-1","key":"TB-1"}\n', encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", ".tb"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "add ticket database"], check=True)
        self.observations = bunker / "observations"
        self.ledger = Ledger(bunker / "ledger.jsonl")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self) -> dict:
        return run_bootstrap(
            repo=self.repo,
            ticket_key="TB-1",
            baseline="main",
            obs_dir=self.observations,
            ledger=self.ledger,
        )

    def test_database_only_commit_is_not_product_work(self) -> None:
        result = self._run()
        branch = "tim/TB-1-work"

        self.assertEqual(result["diff"][branch]["file_count"], 0)
        self.assertEqual(result["work_separation"][branch]["work_commits"], [])
        self.assertEqual(result["merge_history"][branch]["ahead_of_merge_base"], 0)
        self.assertTrue(result["merge_history"][branch]["merged_back"])

    def test_code_commit_remains_visible_beside_database_commit(self) -> None:
        (self.repo / "src" / "main.cpp").write_text("int main() { return 1; }\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "src/main.cpp"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "implement ticket"], check=True)

        result = self._run()
        branch = "tim/TB-1-work"

        self.assertEqual([item["path"] for item in result["diff"][branch]["files"]], ["src/main.cpp"])
        commits = result["work_separation"][branch]["work_commits"]
        self.assertEqual([commit["subject"] for commit in commits], ["implement ticket"])
        self.assertEqual(result["merge_history"][branch]["ahead_of_merge_base"], 1)
        self.assertFalse(result["merge_history"][branch]["merged_back"])

    def test_configured_base_falls_back_to_fetched_remote_ref(self) -> None:
        root_commit = subprocess.run(
            ["git", "-C", str(self.repo), "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(self.repo), "update-ref", "refs/remotes/origin/ecs", root_commit],
            check=True,
        )

        result = run_bootstrap(
            repo=self.repo,
            ticket_key="TB-1",
            baseline="ecs",
            obs_dir=self.observations,
            ledger=self.ledger,
        )

        saved = json.loads((self.observations / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(result["baseline"], "origin/ecs")
        self.assertEqual(saved["baseline"], "origin/ecs")
        self.assertEqual(saved["configured_baseline"], "ecs")


if __name__ == "__main__":
    unittest.main()


class PeopleAndRiskTests(unittest.TestCase):
    """The who-else and hazard stages answer mechanically what the agent
    used to guess at."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "ada@example.com")
        self.git("config", "user.name", "Ada")
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "src" / "parse.py").write_text("def parse():\n    return 1\n", encoding="utf-8")
        (self.repo / "src" / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        # Grace touched the parser on main before the ticket branch existed.
        self.git("config", "user.name", "Grace")
        (self.repo / "src" / "parse.py").write_text("def parse():\n    return 2\n", encoding="utf-8")
        self.git("commit", "-qam", "tune parser")
        self.git("config", "user.name", "Ada")
        self.git("switch", "-qc", "tim/TB-7-parser")
        (self.repo / "src" / "parse.py").write_text("def parse():\n    # TODO handle blank lines\n    return 3\n", encoding="utf-8")
        self.git("commit", "-qam", "start parser work")
        # Another branch for the same ticket, with a different last committer.
        self.git("switch", "-qc", "lin/TB-7-spike", "main")
        self.git("config", "user.name", "Lin")
        (self.repo / "src" / "other.py").write_text("OTHER = 2\n", encoding="utf-8")
        self.git("commit", "-qam", "spike")
        # main moved on and touched the same parser file after the merge-base.
        self.git("switch", "-q", "main")
        self.git("config", "user.name", "Grace")
        (self.repo / "src" / "parse.py").write_text("def parse():\n    return 2  # main\n", encoding="utf-8")
        self.git("commit", "-qam", "parser on main")
        self.git("switch", "-q", "tim/TB-7-parser")
        self.observations = self.repo / "obs" / "observations"
        self.ledger = Ledger(self.repo / "obs" / "ledger.jsonl")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def test_people_names_file_authors_and_other_branch_committers(self) -> None:
        result = run_bootstrap(repo=self.repo, ticket_key="TB-7", baseline="main", obs_dir=self.observations, ledger=self.ledger)
        people = result["people"]["tim/TB-7-parser"]
        self.assertEqual(people["touched_paths"], 1)
        self.assertEqual(set(people["file_authors"]), {"Ada", "Grace"})
        self.assertEqual(people["file_authors"]["Grace"]["commits"], 2)
        others = {entry["branch"]: entry for entry in people["other_branches"]}
        self.assertEqual(others["lin/TB-7-spike"]["author"], "Lin")
        self.assertEqual(others["lin/TB-7-spike"]["commits"], 1)
        self.assertTrue((self.observations / "people.json").is_file())

    def test_risk_flags_baseline_overlap_markers_and_missing_tests(self) -> None:
        result = run_bootstrap(repo=self.repo, ticket_key="TB-7", baseline="main", obs_dir=self.observations, ledger=self.ledger)
        risk = result["risk"]["tim/TB-7-parser"]
        self.assertEqual(risk["baseline_overlap"], ["src/parse.py"])
        self.assertEqual(risk["added_markers"], {"src/parse.py": 1})
        self.assertEqual(risk["tests_touched"], [])
        self.assertTrue(risk["source_touched_without_tests"])
        spike = result["risk"]["lin/TB-7-spike"]
        self.assertEqual(spike["baseline_overlap"], [])
        self.assertEqual(spike["added_markers"], {})

    def test_locate_places_ticket_terms_on_the_baseline(self) -> None:
        from ticketbunker.processors.stages import ticket_terms

        self.assertEqual(
            ticket_terms("Position Component Implementation", "Coordinates for general Pos3, not yet WorldPos3 and LocalPos3; see Type::Pos3 and parse_lines"),
            ["WorldPos3", "LocalPos3", "Type::Pos3", "parse_lines", "Position", "Component", "Coordinates", "general"],
        )
        result = run_bootstrap(
            repo=self.repo, ticket_key="TB-7", baseline="main", obs_dir=self.observations, ledger=self.ledger,
            ticket_text={"title": "Parse blank lines", "description": "The parse helper skips blank lines"},
        )
        locate = result["locate"]
        self.assertIn("parse", [term.lower() for term in locate["terms"]])
        hit = next(item for item in locate["files"] if item["path"] == "src/parse.py")
        self.assertIn("parse", [term.lower() for term in hit["terms"]])
        self.assertGreaterEqual(hit["matches"], 1)
        self.assertTrue((self.observations / "locate.json").is_file())

    def test_check_runs_configured_commands_in_a_scratch_worktree(self) -> None:
        import sys

        failing = f"{sys.executable} -c \"import sys; print('compiling'); print('error: boom at parse.py:2'); sys.exit(2)\""
        result = run_bootstrap(
            repo=self.repo, ticket_key="TB-7", baseline="main", obs_dir=self.observations, ledger=self.ledger,
            checks={"build": failing, "test": "echo never", "timeout_seconds": 60}, check_branch="tim/TB-7-parser",
        )
        outcome = result["check"]["tim/TB-7-parser"]
        self.assertFalse(outcome["build"]["passed"])
        self.assertEqual(outcome["build"]["exit"], 2)
        self.assertEqual(outcome["build"]["first_error"], "error: boom at parse.py:2")
        self.assertNotIn("test", outcome, "a failed build skips the test command")
        self.assertTrue((self.observations.parent / outcome["build"]["log"]).is_file())
        self.assertFalse((self.observations.parent / "checks" / "tim__TB-7-parser").exists(), "the worktree is removed")
        listed = subprocess.run(["git", "-C", str(self.repo), "worktree", "list"], capture_output=True, text=True, check=True).stdout
        self.assertNotIn("tim__TB-7-parser", listed)

        passing = run_bootstrap(
            repo=self.repo, ticket_key="TB-7", baseline="main", obs_dir=self.observations, ledger=self.ledger,
            checks={"test": f"{sys.executable} -c \"import pathlib; assert pathlib.Path('src/parse.py').exists(); print('ok')\""},
            check_branch="tim/TB-7-parser",
        )
        self.assertTrue(passing["check"]["tim/TB-7-parser"]["test"]["passed"])

        unconfigured = run_bootstrap(repo=self.repo, ticket_key="TB-7", baseline="main", obs_dir=self.observations, ledger=self.ledger)
        self.assertEqual(unconfigured["check"], {})

    def test_scaffold_reports_check_outcomes_and_asks_for_a_blocked_verdict(self) -> None:
        from ticketbunker.builders import scaffold

        observations = {
            "check": {"b": {"build": {"command": "make", "exit": 2, "seconds": 3.1, "passed": False, "timed_out": False, "first_error": "error: boom", "log": "observations/checks/b.build.log"}}},
        }
        form = scaffold.build({"id": "TB-7"}, observations, "b")
        self.assertIn("- [observation] Build check failed (exit 2) in 3.1s: `make` — first error: error: boom (check, observations/checks/b.build.log).", form)
        self.assertIn("verdict is blocked", form.split("### Who", 1)[0])
        clean = scaffold.build({"id": "TB-7"}, {"check": {"b": {"test": {"command": "pytest", "exit": 0, "seconds": 1.0, "passed": True}}}}, "b")
        self.assertIn("- [observation] Test check passed in 1.0s: `pytest` (check).", clean)
        self.assertNotIn("verdict is blocked", clean)

    def test_scaffold_prefills_people_and_risk_evidence(self) -> None:
        from ticketbunker.builders import fragments, scaffold

        ticket = {"id": "TB-7", "title": "Parse blank lines", "assignee": "Tim", "reporter": "Tim", "comments": [{"author": "Grace", "text": "Careful with blank lines"}]}
        run_bootstrap(repo=self.repo, ticket_key="TB-7", baseline="main", obs_dir=self.observations, ledger=self.ledger, ticket_text=ticket)
        observations = fragments.load_observations(self.observations)
        form = scaffold.build(ticket, observations, "tim/TB-7-parser")
        who = form.split("\n## Who\n", 1)[1].split("\n## What\n", 1)[0]
        self.assertIn("Commenter: Grace (1 comment(s) on the ticket) (ticket).", who)
        self.assertIn("Recent author of the touched files on main: Grace — 2 commit(s)", who)
        self.assertIn("Other branch for this ticket: lin/TB-7-spike — last work commit by Lin", who)
        self.assertIn("a source path alone is never a Who claim", who)
        what = form.split("\n## What\n", 1)[1].split("\n## Where\n", 1)[0]
        self.assertIn("Added 1 TODO/FIXME-style marker(s) in src/parse.py (risk).", what)
        self.assertIn("No test file changed alongside 1 source file(s) (risk).", what)
        where = form.split("\n## Where\n", 1)[1].split("\n## Why\n", 1)[0]
        self.assertIn("Also changed on main since the merge-base: src/parse.py", where)
        self.assertIn("Ticket terms found on main: src/parse.py", where)
        self.assertIn("start from the locate hits above", where)
        self.assertIn("'Risk:' claim", form.split("### Who", 1)[1].split("### What", 1)[0])


class DescriptionItemTests(unittest.TestCase):
    def test_nested_bullets_flatten_to_one_item_per_line(self) -> None:
        from ticketbunker.builders import scaffold

        text = "[8/21/26]\n- Implemented:\n  - CommonTypes\n  - Coordinates for general Pos3\n- Todo:\n  - WorldPos3\n"
        self.assertEqual(
            scaffold.description_items(text),
            ["[8/21/26]", "Implemented", "Implemented › CommonTypes", "Implemented › Coordinates for general Pos3", "Todo", "Todo › WorldPos3"],
        )
        self.assertEqual(scaffold.description_items("One plain line."), ["One plain line."])
        form = scaffold.build({"id": "TB-1", "description": text}, {}, "tim/TB-1")
        why = form.split("\n## Why\n", 1)[1].split("\n## State\n", 1)[0]
        self.assertIn("- [observation] Ticket description item: Implemented › CommonTypes (ticket).", why)
        self.assertNotIn("\n- Implemented:", why)
        self.assertNotIn("Ticket description item: [8/21/26]", why)
        self.assertNotIn("Ticket description item: Implemented (ticket)", why)
        for line in why.splitlines():
            if line.startswith("- "):
                self.assertTrue(line.startswith("- [observation]"), line)
