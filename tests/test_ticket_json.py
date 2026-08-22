from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from ticketbunker import cli, layout


class TicketJsonIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_complete_json_payload_enters_the_regular_gate_and_context(self) -> None:
        payload = {
            "id": "ECS-4",
            "title": "Position System Implementation",
            "description": "Move entities from velocity.",
            "status": "in-progress",
            "type": "task",
            "acceptance_criteria": ["Position updates are deterministic"],
            "comments": [
                {
                    "author": "TimothyBunker",
                    "text": "Keep the hot loop direct.",
                    "when": "2026-08-21T12:00:00Z",
                    "source": "https://github.com/TimothyBunker/coal/issues/4#issuecomment-1",
                }
            ],
            "metadata": {
                "tracker": "github",
                "source_url": "https://github.com/TimothyBunker/coal/issues/4",
                "github": {"project": {"number": 8, "fields": {"Status": "In Progress"}}},
            },
        }
        source = self.root / "ticket.json"
        source.write_text(json.dumps(payload), encoding="utf-8")

        code, stdout, stderr = self._run(
            ["ticket", "--root", str(self.root / "bunkers"), "--from-json", str(source)]
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("initialized", stdout)
        bunker = self.root / "bunkers" / "ECS-4_bunker"
        stored = json.loads(layout.paths(bunker)["ticket"].read_text(encoding="utf-8"))
        self.assertEqual(stored["key"], "ECS-4")
        self.assertEqual(stored["metadata"]["github"]["project"]["number"], 8)
        context = (layout.paths(bunker)["context"] / "20_ticket.md").read_text(encoding="utf-8")
        self.assertIn("Position updates are deterministic", context)
        self.assertIn("issuecomment-1", context)
        self.assertIn('"tracker": "github"', context)
        ledger = layout.paths(bunker)["ledger"].read_text(encoding="utf-8")
        self.assertIn('"intake": "json"', ledger)

    def test_positional_id_cannot_silently_override_json(self) -> None:
        source = self.root / "ticket.json"
        source.write_text(json.dumps({"id": "ECS-4", "key": "ECS-4"}), encoding="utf-8")

        code, _stdout, stderr = self._run(
            ["ticket", "ECS-3", "--root", str(self.root / "bunkers"), "--from-json", str(source)]
        )

        self.assertEqual(code, 1)
        self.assertIn("disagrees", stderr)
        self.assertFalse((self.root / "bunkers" / "ECS-3_bunker").exists())

    def test_json_is_a_complete_source_not_an_implicit_override_layer(self) -> None:
        source = self.root / "ticket.json"
        source.write_text(json.dumps({"id": "ECS-4"}), encoding="utf-8")

        code, _stdout, stderr = self._run(
            [
                "ticket", "--root", str(self.root / "bunkers"), "--from-json", str(source),
                "--status", "done",
            ]
        )

        self.assertEqual(code, 1)
        self.assertIn("complete ticket payload", stderr)


if __name__ == "__main__":
    unittest.main()
