from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ticketbunker import app_server, config


class StandaloneBrowserServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root / "source")], check=True)
        self.source = self.root / "source"
        (self.source / ".tb").mkdir()
        self.config_patch = mock.patch.object(
            config, "GLOBAL_CONFIG", self.root / ".tbconfig"
        )
        self.config_patch.start()
        self.data_patch = mock.patch.dict(
            os.environ, {"TB_DATA_HOME": str(self.root / "app-data")}
        )
        self.data_patch.start()
        self.server = app_server.serve(self.source, 0, launch_id="test-launch")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.data_patch.stop()
        self.config_patch.stop()
        self.temporary.cleanup()

    def get(self, path: str) -> tuple[int, bytes]:
        with urlopen(f"{self.base}{path}", timeout=2) as response:
            return response.status, response.read()

    def post(self, path: str, value: dict, *, token: str = "") -> tuple[int, dict]:
        request = Request(
            f"{self.base}{path}",
            data=json.dumps(value).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-TicketBunker-Token": token,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_browser_serves_the_same_bundled_app_with_a_per_launch_token(self) -> None:
        status, body = self.get("/")
        document = body.decode("utf-8")

        self.assertEqual(status, 200)
        self.assertIn("<div id=\"root\"></div>", document)
        self.assertNotIn("__TB_TRANSPORT_TOKEN__", document)
        match = re.search(r'name="ticketbunker-token" content="([^"]+)"', document)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1), self.server.token)

        health_status, health_body = self.get("/_viewer/health")
        health = json.loads(health_body)
        self.assertEqual(health_status, 200)
        self.assertEqual(health["kind"], "standalone-app")
        self.assertEqual(health["launch"], "test-launch")

    def test_browser_transport_requires_the_token_and_shared_allowlist(self) -> None:
        status, value = self.post(
            "/api/action", {"action": "application.state", "payload": {}}
        )
        self.assertEqual(status, 403)
        self.assertFalse(value["ok"])

        status, value = self.post(
            "/api/action",
            {"action": "ticket.create", "payload": {}},
            token=self.server.token,
        )
        self.assertEqual(status, 400)
        self.assertEqual(value["code"], "command_unavailable")

        status, value = self.post(
            "/api/action",
            {"action": "application.state", "payload": {}},
            token=self.server.token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(value["ok"])
        self.assertIn("bunkers", value)

    def test_bootstrap_registers_but_does_not_import_the_open_repository(self) -> None:
        status, body = self.get("/api/bootstrap")
        value = json.loads(body)

        self.assertEqual(status, 200)
        self.assertEqual(value["launch"]["repository"], str(self.source))
        self.assertEqual(value["bunkers"], [])
        self.assertEqual(value["legacy_projects"][0]["path"], str(self.source))


if __name__ == "__main__":
    unittest.main()
