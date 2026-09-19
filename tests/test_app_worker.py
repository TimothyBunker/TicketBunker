from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import app_worker, commands, config


class ApplicationWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.config_patch = mock.patch.object(
            config, "GLOBAL_CONFIG", self.root / ".tbconfig"
        )
        self.config_patch.start()
        self.data_patch = mock.patch.dict(
            os.environ, {"TB_DATA_HOME": str(self.root / "app-data")}
        )
        self.data_patch.start()

    def tearDown(self) -> None:
        self.data_patch.stop()
        self.config_patch.stop()
        self.temporary.cleanup()

    def test_dispatch_preserves_the_request_id_and_uses_the_shared_registry(self) -> None:
        result = app_worker.dispatch_request(
            {"id": 7, "action": "application.state", "payload": {}},
            default_root=self.root,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["id"], 7)
        self.assertEqual(result["command"], "application.state")
        self.assertIn("platform", result)

    def test_unavailable_commands_never_reach_the_registry(self) -> None:
        with mock.patch.object(commands, "dispatch") as dispatch:
            result = app_worker.dispatch_request(
                {"id": "unsafe", "action": "ticket.create", "payload": {}},
                default_root=self.root,
            )

        self.assertEqual(result["code"], "command_unavailable")
        dispatch.assert_not_called()

    def test_ticket_and_coworker_actions_are_explicit_desktop_capabilities(self) -> None:
        self.assertIn("application.bunker.pipelines", app_worker.DESKTOP_ACTIONS)
        self.assertIn("application.ticket.get", app_worker.DESKTOP_ACTIONS)
        self.assertIn("application.bunker.configuration.replace", app_worker.DESKTOP_ACTIONS)
        self.assertIn("application.bunker.repository.replace", app_worker.DESKTOP_ACTIONS)
        self.assertIn("application.project.repositories.select", app_worker.DESKTOP_ACTIONS)
        self.assertIn("application.release.check", app_worker.DESKTOP_ACTIONS)
        self.assertIn("application.bunker.share", app_worker.DESKTOP_ACTIONS)
        self.assertIn("application.bunker.invitation.create", app_worker.DESKTOP_ACTIONS)
        self.assertIn("application.bunker.members", app_worker.DESKTOP_ACTIONS)
        self.assertIn("coworker.chat", app_worker.DESKTOP_ACTIONS)
        self.assertIn("editor.save", app_worker.DESKTOP_ACTIONS)
        self.assertNotIn("ticket.create", app_worker.DESKTOP_ACTIONS)

    def test_repository_availability_uses_the_real_worker_and_backend(self) -> None:
        def call(action, **payload):
            value = app_worker.dispatch_request({"id": action, "action": action, "payload": payload}, default_root=self.root)
            self.assertTrue(value["ok"], value)
            return value
        row = {"name": "Dummy", "canonical_remote": "github.com/example/dummy", "default_branch": "main"}
        bunker = call("application.bunker.create", name="Dummy", project_key="NAV", repositories=[row])["bunker"]
        bid, pid = bunker["id"], bunker["projects"][0]["id"]
        cfg = call("application.bunker.configuration.get", bunker_id=bid, project_id=pid)
        selected = call("application.project.repositories.select", bunker_id=bid, project_id=pid,
            expected_revision=cfg["repositories"]["revision"], repositories=[row])
        self.assertEqual(selected["bunker"]["repositories"][0]["canonical_remote"], row["canonical_remote"])

    def test_command_rejections_are_machine_readable(self) -> None:
        with mock.patch.object(
            commands,
            "dispatch",
            side_effect=commands.CommandRejected("needs_choice", "Choose a branch."),
        ):
            result = app_worker.dispatch_request(
                {"id": 8, "action": "application.state", "payload": {}},
                default_root=self.root,
            )

        self.assertEqual(
            result,
            {
                "id": 8,
                "ok": False,
                "code": "needs_choice",
                "message": "Choose a branch.",
            },
        )

    def test_worker_serves_multiple_requests_in_one_process(self) -> None:
        source = io.BytesIO(
            b'{"id":1,"action":"application.state","payload":{}}\n'
            b'{"id":2,"action":"application.state","payload":{}}\n'
        )
        target = io.BytesIO()

        self.assertEqual(app_worker.serve(source, target, default_root=self.root), 0)

        responses = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual([response["id"] for response in responses], [1, 2])
        self.assertTrue(all(response["ok"] for response in responses))

    def test_bad_request_does_not_end_the_worker(self) -> None:
        source = io.BytesIO(
            b"not-json\n"
            b'{"id":2,"action":"application.state","payload":{}}\n'
        )
        target = io.BytesIO()

        app_worker.serve(source, target, default_root=self.root)

        responses = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual(responses[0]["code"], "request_invalid")
        self.assertTrue(responses[1]["ok"])
        self.assertEqual(responses[1]["id"], 2)

    def test_unexpected_failures_write_a_safe_local_diagnostic(self) -> None:
        with mock.patch.object(
            commands,
            "dispatch",
            side_effect=RuntimeError("private-payload-value"),
        ):
            result = app_worker.dispatch_request(
                {
                    "id": 9,
                    "action": "application.state",
                    "payload": {"token": "private-payload-value"},
                },
                default_root=self.root,
            )

        self.assertEqual(result["code"], "internal_error")
        self.assertNotIn("private-payload-value", json.dumps(result))
        log = self.root / "app-data" / "diagnostics" / "app-worker.jsonl"
        self.assertEqual(result["diagnostic_path"], str(log))
        diagnostic = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual(diagnostic["id"], result["diagnostic_id"])
        self.assertEqual(diagnostic["error_type"], "RuntimeError")
        self.assertNotIn("private-payload-value", json.dumps(diagnostic))

    def test_hidden_cli_worker_keeps_the_same_process_alive(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from ticketbunker.cli import main; raise SystemExit(main())",
                "_app-worker",
            ],
            cwd=self.root,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).parents[1]),
                "TB_DATA_HOME": str(self.root / "app-data"),
            },
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        try:
            for request_id in (11, 12):
                process.stdin.write(
                    json.dumps(
                        {
                            "id": request_id,
                            "action": "application.state",
                            "payload": {},
                        }
                    )
                    + "\n"
                )
                process.stdin.flush()
                response = json.loads(process.stdout.readline())
                self.assertEqual(response["id"], request_id)
                self.assertTrue(response["ok"])
        finally:
            process.stdin.close()
            process.wait(timeout=5)
            process.stdout.close()


if __name__ == "__main__":
    unittest.main()
