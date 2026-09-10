from __future__ import annotations

import json
import http.client
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

    def test_json_response_with_legacy_surrogate_remains_readable(self) -> None:
        with mock.patch.object(app_server.app_worker, "dispatch_request", return_value={"ok": True, "text": "saved\ud800"}):
            status, value = self.post("/api/action", {"action": "application.state"}, token=self.server.token)
        self.assertEqual(status, 200)
        self.assertEqual(value["text"], "saved\ud800")

    def test_deeply_nested_request_gets_a_readable_rejection(self) -> None:
        request = Request(f"{self.base}/api/action", data=b"[" * 10000 + b"0" + b"]" * 10000,
                          headers={"Content-Type": "application/json", "X-TicketBunker-Token": self.server.token}, method="POST")
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 400)
        self.assertFalse(json.loads(caught.exception.read())["ok"])
        self.assertEqual(self.get("/_viewer/health")[0], 200)

    def test_foreign_host_cannot_read_the_launch_token_or_local_data(self) -> None:
        port = self.server.server_address[1]
        for host in (f"example.test:{port}", f"localhost.example.test:{port}", "localhost:1", f"user@localhost:{port}"):
            for path in ("/", "/api/bootstrap", "/_viewer/health"):
                with self.subTest(host=host, path=path):
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    connection.request("GET", path, headers={"Host": host})
                    response = connection.getresponse()
                    body = response.read()
                    connection.close()
                    self.assertEqual(response.status, 403)
                    self.assertNotIn(self.server.token.encode(), body)
                    self.assertNotIn(str(self.source).encode(), body)

    def test_foreign_origin_cannot_invoke_local_actions_even_with_a_token(self) -> None:
        for origin in ("https://example.test", "null", "http://localhost:1"):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
            connection.request("POST", "/api/action", body=b'{"action":"application.state"}', headers={
                "Content-Type": "application/json", "Origin": origin, "X-TicketBunker-Token": self.server.token,
            })
            response = connection.getresponse()
            response.read(); connection.close()
            self.assertEqual(response.status, 403)

    def test_loopback_host_and_same_origin_remain_supported(self) -> None:
        port = self.server.server_address[1]
        for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("POST", "/api/action", body=b'{"action":"application.state"}', headers={
                "Host": host, "Origin": f"http://{host}", "Content-Type": "application/json", "X-TicketBunker-Token": self.server.token,
            })
            response = connection.getresponse()
            value = json.loads(response.read()); connection.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(value["ok"])


if __name__ == "__main__":
    unittest.main()


class BrowserPresenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.server = app_server.serve(self.root, 0, launch_id="presence")

    def tearDown(self) -> None:
        self.server.server_close()
        self.temporary.cleanup()

    def test_hidden_tab_throttling_does_not_stop_the_server(self) -> None:
        start = self.server._started_at
        self.server.note_session("tab-one-abcdefgh")

        # A hidden tab may heartbeat only once a minute; the session must survive
        # well beyond that, and the old thirty-second window must be gone.
        self.assertEqual(self.server.idle_reason(start + 45), "")
        self.assertEqual(self.server.idle_reason(start + 100), "")
        self.assertEqual(self.server.idle_reason(start + 170), "")
        expiry = start + app_server.SESSION_TTL_SECONDS + 5
        self.assertEqual(self.server.idle_reason(expiry), "")  # grace after expiry
        # One second past each boundary: a large monotonic clock on a
        # long-running host rounds (start + n) - start a hair under n.
        self.assertEqual(
            self.server.idle_reason(expiry + app_server.CLOSE_GRACE_SECONDS + 1),
            "no browser tab reported in for 3 minutes",
        )

    def test_closing_the_last_tab_stops_after_a_short_grace(self) -> None:
        start = self.server._started_at
        self.server.note_session("tab-one-abcdefgh")
        self.server.note_session("tab-one-abcdefgh", closed=True)

        self.assertEqual(self.server.idle_reason(start + 1), "")
        self.assertEqual(
            self.server.idle_reason(start + app_server.CLOSE_GRACE_SECONDS + 1),
            "the last browser tab closed",
        )

    def test_a_returning_tab_revives_presence(self) -> None:
        start = self.server._started_at
        self.server.note_session("tab-one-abcdefgh")
        self.server._sessions["tab-one-abcdefgh"] = start - app_server.SESSION_TTL_SECONDS
        self.assertEqual(self.server.idle_reason(start + 1), "")  # inside the grace window
        self.server.note_session("tab-one-abcdefgh")
        self.assertEqual(self.server.idle_reason(start + 60), "")

    def test_no_tab_at_all_stops_after_the_first_tab_timeout(self) -> None:
        start = self.server._started_at
        self.assertEqual(self.server.idle_reason(start + 30), "")
        self.assertEqual(
            self.server.idle_reason(start + app_server.FIRST_TAB_TIMEOUT_SECONDS + 1),
            "no browser tab opened within 60 seconds",
        )
