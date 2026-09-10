"""Scratch loopback HTTP probes for the browser transport authority boundary."""
from __future__ import annotations

import http.client
import json
import socket
import unittest
from unittest import mock

from ticketbunker import app_server
import test_app_server as _server_fixtures


class LocalTransportBoundaryTests(unittest.TestCase):
    setUp = _server_fixtures.StandaloneBrowserServerTests.setUp
    tearDown = _server_fixtures.StandaloneBrowserServerTests.tearDown

    def request(self, method="GET", path="/", headers=None, body=b""):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=2)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for key, value in headers if headers is not None else [("Host", self.host)]:
                connection.putheader(key, value)
            if body:
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    @property
    def host(self):
        return f"127.0.0.1:{self.server.server_address[1]}"

    def assert_rejected(self, headers, path="/", method="GET"):
        with mock.patch.object(app_server.app_worker, "dispatch_request", return_value={"ok": True}) as worker:
            status, body, response_headers = self.request(method, path, headers, b'{"action":"application.state"}' if method == "POST" else b"")
        self.assertEqual(status, 403)
        self.assertNotIn(self.server.token.encode(), body)
        self.assertNotIn(str(self.source).encode(), body)
        self.assertNotIn("Access-Control-Allow-Origin", response_headers)
        worker.assert_not_called()

    def test_missing_duplicate_and_combined_authority_headers_are_rejected(self):
        for headers in (
            [], [("Host", self.host), ("Host", self.host)],
            [("Host", self.host), ("Host", "foreign.invalid")],
            [("Host", "foreign.invalid"), ("Host", self.host)],
            [("Host", f"{self.host}, foreign.invalid")],
            [("Host", self.host), ("Origin", f"http://{self.host}"), ("Origin", f"http://{self.host}")],
            [("Host", self.host), ("Origin", f"http://{self.host}"), ("Origin", "http://foreign.invalid")],
        ):
            for path in ("/", "/api/bootstrap", "/_viewer/health"):
                with self.subTest(headers=headers, path=path):
                    self.assert_rejected(headers, path)

    def test_loopback_spelling_and_exact_same_origin_remain_usable(self):
        port = self.server.server_address[1]
        for host in (self.host, f"localhost:{port}", f"LOCALHOST:{port}", f"[::1]:{port}"):
            with self.subTest(host=host):
                headers = [("Host", host), ("Origin", f"http://{host}"), ("X-TicketBunker-Token", self.server.token)]
                with mock.patch.object(app_server.app_worker, "dispatch_request", return_value={"ok": True}) as worker:
                    status, body, _ = self.request("POST", "/api/action", headers, b'{"action":"application.state"}')
                self.assertEqual(status, 200)
                self.assertTrue(json.loads(body)["ok"])
                worker.assert_called_once()

    def test_local_but_different_origins_and_foreign_ipv4_spellings_are_rejected(self):
        port = self.server.server_address[1]
        for host in (f"127.1:{port}", f"2130706433:{port}", f"127.0.0.1.foreign.invalid:{port}", f"localhost.:{port}"):
            with self.subTest(host=host):
                self.assert_rejected([("Host", host)])
        for origin in (f"http://localhost:{port}", f"https://{self.host}", "null", "", f"http://{self.host}/", f"http://{self.host} http://foreign.invalid"):
            with self.subTest(origin=origin):
                self.assert_rejected([("Host", self.host), ("Origin", origin)])

    def test_empty_delimiters_and_embedded_controls_do_not_normalize_into_trusted_hosts(self):
        for host in (self.host + "?", self.host + "#", self.host.replace("127.0", "127.\t0")):
            with self.subTest(host=host):
                self.assert_rejected([("Host", host)])

    def test_malformed_origin_is_not_silently_normalized_to_the_local_origin(self):
        for origin in (f"http://{self.host}?", f"http://{self.host}#", f"http://{self.host}".replace("127.0", "127.\t0")):
            with self.subTest(origin=origin):
                self.assert_rejected([("Host", self.host), ("Origin", origin)])

    def test_foreign_authority_in_an_absolute_request_target_is_rejected(self):
        self.assert_rejected([("Host", self.host)], "http://foreign.invalid/api/bootstrap")

    def test_html_and_json_responses_disallow_framing_and_external_connections(self):
        for path in ("/", "/_viewer/health"):
            with self.subTest(path=path):
                status, _, headers = self.request(path=path)
                self.assertEqual(status, 200)
                csp = headers["Content-Security-Policy"]
                self.assertIn("frame-ancestors 'none'", csp)
                self.assertIn("connect-src 'self'", csp)
                self.assertIn("object-src 'none'", csp)
                self.assertIn("base-uri 'none'", csp)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_non_ascii_token_gets_a_readable_rejection_and_the_server_continues(self):
        headers = [("Host", self.host), ("X-TicketBunker-Token", "invalid-é")]
        self.assert_rejected(headers, "/api/action", "POST")
        self.assertEqual(self.request(path="/_viewer/health")[0], 200)

    def test_duplicate_tokens_are_not_resolved_by_whichever_header_arrives_first(self):
        for tokens in ((self.server.token, self.server.token), (self.server.token, "invalid"), ("invalid", self.server.token)):
            with self.subTest(tokens="same" if tokens[0] == tokens[1] else "mixed"):
                self.assert_rejected([("Host", self.host), *[("X-TicketBunker-Token", token) for token in tokens]], "/api/action", "POST")

    def test_rejected_request_body_cannot_continue_as_an_authenticated_action(self):
        port = self.server.server_address[1]
        body = b'{"action":"application.state"}'
        second = f"POST /api/action HTTP/1.1\r\nHost: {self.host}\r\nX-TicketBunker-Token: {self.server.token}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
        first = b"POST /api/action HTTP/1.1\r\nHost: foreign.invalid\r\nContent-Length: 2\r\n\r\n{}"
        with mock.patch.object(app_server.app_worker, "dispatch_request", return_value={"ok": True}) as worker:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
                connection.sendall(first + second)
                chunks = []
                while chunk := connection.recv(8192):
                    chunks.append(chunk)
                response = b"".join(chunks)
            self.assertIn(b"403", response)
            self.assertNotIn(b"200 OK", response)
            worker.assert_not_called()
        self.assertEqual(self.request(path="/_viewer/health")[0], 200)


if __name__ == "__main__":
    unittest.main()
