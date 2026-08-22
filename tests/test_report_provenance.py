from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ticketbunker.processors.anchorcheck import annotate, verified_stamp


class ReportProvenanceTests(unittest.TestCase):
    def test_mechanical_stamp_round_trips_generator_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            (repo / "README.md").write_text("test\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)

            text = annotate(
                "## Who\n\n- [observation] Test.\n",
                repo,
                "HEAD",
                "2026-08-21T12:00:00+00:00",
                {"harness": "pi", "provider": "OpenAI Codex", "model": "gpt-5.6-sol"},
            )
            stamp = verified_stamp(text)

            self.assertEqual(stamp["harness"], "pi")
            self.assertEqual(stamp["provider"], "OpenAI Codex")
            self.assertEqual(stamp["model"], "gpt-5.6-sol")
            self.assertNotIn("OpenAI Codex", text)
            self.assertIn("OpenAI%20Codex", text)


if __name__ == "__main__":
    unittest.main()
