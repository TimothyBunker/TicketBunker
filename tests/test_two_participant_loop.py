"""Real process/HTTP/Git collaboration coverage, without external providers."""
from pathlib import Path
import os
import subprocess
import sys
import unittest

import test_bunkerruntime as runtime_tests
from ticketbunker import bunkerdb, bunkerruntime, layout
import json
import importlib.util
import tempfile

ROOT = Path(__file__).resolve().parents[1]


class IsolatedParticipantTests(unittest.TestCase):
    def test_sanitized_environment_has_a_disposable_windows_profile(self):
        spec = importlib.util.spec_from_file_location("two_participant_loop", ROOT / "tools/two_participant_loop.py")
        loop = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loop)
        with tempfile.TemporaryDirectory() as temporary:
            area = Path(temporary) / "participant"
            env = loop.isolated_environment(area)
            self.assertEqual(env["USERPROFILE"], str(area.resolve()))
            self.assertEqual(env["TB_CONFIG"], str(area / "settings.json"))
            self.assertEqual(env["PYTHONIOENCODING"], "utf-8")

    def test_two_participants_share_work_without_sharing_local_state(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/two_participant_loop.py")],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS 17 isolated-participant checks", result.stdout)

    def test_default_ticket_type_can_enter_the_runtime_gate(self):
        # Regression from the two-process run: omitted type must enter the
        # legacy runtime as lowercase task, not the presentation label Task.
        fixture = runtime_tests.BunkerRuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        proposal = bunkerdb.propose_ticket_sync(fixture.bunker_id,
            project_id=fixture.project_id, source_kind="manual",
            tickets=[{"id": "ECS-4", "title": "Default type", "status": "todo"}])
        bunkerdb.apply_ticket_sync_change(fixture.bunker_id, proposal)
        runtime = bunkerruntime.materialize(fixture.bunker_id, "ECS-4")["bunker"]
        ticket = json.loads(layout.paths(runtime)["ticket"].read_text())
        self.assertEqual(ticket["type"], "task")


if __name__ == "__main__":
    unittest.main()
