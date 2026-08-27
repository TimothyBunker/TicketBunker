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
