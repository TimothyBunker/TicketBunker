"""Malformed local messages must not end the persistent application worker."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ticketbunker import app_worker, commands


class WorkerFailureCampaignTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tb-worker-boundary-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def stream(self, raw):
        output = io.BytesIO()
        last = b'{"id":2,"action":"application.state"}\n'
        with patch.object(commands, "dispatch", return_value={"ok": True, "command": "application.state"}):
            self.assertEqual(app_worker.serve(io.BytesIO(raw + last), output, default_root=self.root), 0)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(replies[-1]["id"], 2)
        self.assertTrue(replies[-1]["ok"])
        return replies[0]

    def test_invalid_project_roots_do_not_end_worker(self):
        for path in ("bad\0path", "bad\ud800path"):
            with self.subTest(path=repr(path)):
                raw = json.dumps({"id": 1, "action": "application.state", "project_root": path}).encode() + b"\n"
                self.assertEqual(self.stream(raw)["code"], "project_root_invalid")

    def test_invalid_utf8_request_id_does_not_break_response_encoding(self):
        raw = b'{"id":"\\ud800","action":"application.state"}\n'
        self.assertEqual(self.stream(raw)["code"], "request_id_invalid")

    def test_excessively_nested_json_does_not_end_worker(self):
        self.assertEqual(self.stream(b"[" * 10000 + b"0" + b"]" * 10000 + b"\n")["code"], "request_invalid")

    def test_oversized_line_is_drained_before_the_next_request(self):
        with patch.object(app_worker, "MAX_REQUEST_BYTES", 100):
            self.assertEqual(self.stream(b"x" * 250 + b"\n")["code"], "request_too_large")

    def test_closed_output_pipe_ends_quietly(self):
        class ClosedOutput(io.BytesIO):
            def write(self, _value):
                raise BrokenPipeError("the app closed its pipe")
        with patch.object(commands, "dispatch", return_value={"ok": True}):
            self.assertEqual(app_worker.serve(io.BytesIO(b'{"id":1,"action":"application.state"}\n'), ClosedOutput(), default_root=self.root), 0)

    def test_escaped_surrogate_in_a_response_does_not_break_the_worker_stream(self):
        output = io.BytesIO()
        source = io.BytesIO(b'{"id":1,"action":"application.state"}\n{"id":2,"action":"application.state"}\n')
        with patch.object(commands, "dispatch", return_value={"ok": True, "text": "saved\ud800"}):
            self.assertEqual(app_worker.serve(source, output, default_root=self.root), 0)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([row["id"] for row in replies], [1, 2])


if __name__ == "__main__":
    unittest.main()
