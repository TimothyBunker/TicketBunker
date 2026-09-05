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

    def test_scaffold_prefills_people_and_risk_evidence(self) -> None:
        from ticketbunker.builders import fragments, scaffold

        run_bootstrap(repo=self.repo, ticket_key="TB-7", baseline="main", obs_dir=self.observations, ledger=self.ledger)
        observations = fragments.load_observations(self.observations)
        ticket = {"id": "TB-7", "assignee": "Tim", "reporter": "Tim", "comments": [{"author": "Grace", "text": "Careful with blank lines"}]}
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
        self.assertIn("'Risk:' claim", form.split("### Who", 1)[1].split("### What", 1)[0])
