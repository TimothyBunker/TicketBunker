from __future__ import annotations

import http.client
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from ticketbunker import commands, config, config_viewer


def run_git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


class ConfigViewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "source"
        self.root.mkdir()
        run_git(self.root, "init", "-b", "ecs")
        run_git(self.root, "config", "user.name", "Tim")
        run_git(self.root, "config", "user.email", "tim@example.com")
        (self.root / "README.md").write_text("voxel\n", encoding="utf-8")
        run_git(self.root, "add", "README.md")
        run_git(self.root, "commit", "-m", "initial")
        self.old_global_config = config.GLOBAL_CONFIG
        config.GLOBAL_CONFIG = Path(self.tmp.name) / "tbconfig.json"
        self.server = config_viewer.serve(self.root, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        config.GLOBAL_CONFIG = self.old_global_config
        self.tmp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None, token: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=3)
        headers = {}
        raw = None
        if body is not None:
            raw = json.dumps(body)
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["X-TicketBunker-Token"] = token
        connection.request(method, path, body=raw, headers=headers)
        response = connection.getresponse()
        value = response.read()
        connection.close()
        return response.status, response.getheader("Content-Type"), value

    def test_setup_page_is_local_direct_and_contains_no_credential_value(self) -> None:
        status, content_type, body = self.request("GET", "/repository")
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("Initialize TicketBunker", text)
        self.assertIn("ticketbunker/config", text)
        self.assertNotIn("GITHUB_ACCESS_TOKEN=", text)

    def test_http_and_cli_share_identity_command(self) -> None:
        token = self.server.RequestHandlerClass.token
        status, content_type, body = self.request(
            "POST",
            "/api/commands",
            {
                "command": "identity.update",
                "payload": {"name": "T Bunker", "email": "tb@example.com", "branch_user": "bunker"},
            },
            token,
        )
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        self.assertTrue(result["ok"])
        self.assertEqual(run_git(self.root, "config", "--get", "user.name"), "T Bunker")
        self.assertEqual(config.load()["user"], "bunker")

    def test_http_rejects_missing_transport_token(self) -> None:
        status, _content_type, body = self.request(
            "POST", "/api/commands", {"command": "identity.update", "payload": {}}
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["code"], "forbidden")

    def test_credentials_cannot_be_saved_inside_source_repository(self) -> None:
        unsafe = self.root / "credentials.env"
        config.save({"credentials_file": str(unsafe)})
        with self.assertRaises(commands.CommandRejected) as caught:
            commands.dispatch(
                "credentials.update",
                {"provider": "github", "token": "secret"},
                commands.CommandContext(self.root, transport="http"),
            )
        self.assertEqual(caught.exception.code, "credential_path_unsafe")
        self.assertFalse(unsafe.exists())


if __name__ == "__main__":
    unittest.main()
