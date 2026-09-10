"""Disposable filesystem scenarios for the shared editor on Linux and Windows."""
import hashlib
import json
import os
from pathlib import Path
import random
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ticketbunker import commands, config, editor


class EditorBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tb-editor-simulation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        subprocess.run(["git", "init", "-b", "main", str(self.root)], check=True, capture_output=True)
        setting = patch.object(config, "GLOBAL_CONFIG", self.root / "settings.json")
        setting.start(); self.addCleanup(setting.stop)
        env = patch.dict(os.environ, {"TB_EDITOR": "", "VISUAL": "", "EDITOR": "", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        env.start(); self.addCleanup(env.stop)

    def file(self, name="file.py", content=b"first\nsecond\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def reject(self, code, function, *args):
        with self.assertRaises(commands.CommandRejected) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(caught.exception.message)

    def test_spaces_unicode_and_bom_round_trip(self):
        path = self.file("space folder/日本語 café.py", b"\xef\xbb\xbfprint('hello')\r\n")
        read = editor.read_source(self.root, path.relative_to(self.root).as_posix(), 100)
        self.assertEqual(read["line"], 2)
        self.assertEqual(read["branch"], "main")
        saved = editor.save_source(self.root, str(path), read["content"].replace("hello", "goodbye"), read["digest"])
        self.assertTrue(saved["saved"])
        self.assertEqual(path.read_bytes(), b"\xef\xbb\xbfprint('goodbye')\r\n")

    def test_real_worker_jsonl_read_save_and_read_remain_in_one_process(self):
        name = "simulation files/café_日本.py"
        path = self.file(name, b"first\r\nsecond\n")
        requests = [
            {"id": 1, "action": "editor.read", "payload": {"path": name}},
            {"id": 2, "action": "editor.save", "payload": {"path": name, "content": "first\nedited\n", "expected_digest": hashlib.sha256(path.read_bytes()).hexdigest()}},
            {"id": 3, "action": "editor.read", "payload": {"path": name}},
        ]
        result = subprocess.run(
            [sys.executable, "-c", "from ticketbunker.cli import main; raise SystemExit(main())", "_app-worker"],
            cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "TB_CONFIG": str(self.root / "settings.json"), "TB_DATA_HOME": str(self.root / "app-data")},
            input="".join(json.dumps(request) + "\n" for request in requests).encode("utf-8"),
            capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([reply["id"] for reply in replies], [1, 2, 3])
        self.assertTrue(all(reply["ok"] for reply in replies), replies)
        self.assertEqual(replies[2]["content"], "first\nedited\n")
        self.assertEqual(path.read_bytes(), b"first\r\nedited\n")

    def test_all_line_endings_noop_and_one_line_edit_preserve_bytes(self):
        for ending in (b"\n", b"\r\n", b"\r"):
            for final in (b"", ending):
                with self.subTest(ending=ending, final=final):
                    original = b"one" + ending + b"two" + final
                    path = self.file(content=original)
                    read = editor.read_source(self.root, "file.py")
                    self.assertFalse(editor.save_source(self.root, "file.py", read["content"], read["digest"])["saved"])
                    self.assertEqual(path.read_bytes(), original)
                    editor.save_source(self.root, "file.py", read["content"].replace("two", "edited"), read["digest"])
                    self.assertEqual(path.read_bytes(), original.replace(b"two", b"edited"))

    def test_mixed_line_endings_preserve_unrelated_lines(self):
        original = b"one\r\ntwo\nthree\rfour\r\n"
        path = self.file(content=original)
        read = editor.read_source(self.root, "file.py")
        self.assertFalse(editor.save_source(self.root, "file.py", read["content"], read["digest"])["saved"])
        self.assertEqual(path.read_bytes(), original)
        editor.save_source(self.root, "file.py", read["content"].replace("two", "edited"), read["digest"])
        self.assertEqual(path.read_bytes(), original.replace(b"two", b"edited"))

    def test_mixed_endings_keep_matching_lines_when_inserting_or_deleting(self):
        original = b"one\r\ntwo\nthree\rfour\r\n"
        path = self.file(content=original)
        read = editor.read_source(self.root, "file.py")
        editor.save_source(self.root, "file.py", read["content"].replace("two\n", "inserted\ntwo\n"), read["digest"])
        self.assertEqual(path.read_bytes(), b"one\r\ninserted\r\ntwo\nthree\rfour\r\n")
        read = editor.read_source(self.root, "file.py")
        editor.save_source(self.root, "file.py", read["content"].replace("inserted\ntwo\n", ""), read["digest"])
        self.assertEqual(path.read_bytes(), b"one\r\nthree\rfour\r\n")

    def test_seeded_mixed_endings_never_change_requested_text(self):
        generator = random.Random(20260909)
        normalize = lambda value: value.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        for scenario in range(100):
            lines = [generator.choice(["repeat", "", "日本語 café", f"line-{scenario}"]) for _ in range(generator.randint(1, 70))]
            original = "".join(line + generator.choice(["\r\n", "\r", "\n"]) for line in lines).encode("utf-8")
            self.assertEqual(editor._encode_source(normalize(original), original), original)
            for _ in range(5):
                start = generator.randrange(len(lines) + 1)
                stop = generator.randrange(start, len(lines) + 1)
                lines[start:stop] = [generator.choice(["inserted", "", "edited λ"]) for _ in range(generator.randrange(5))]
            content = "\n".join(lines) + generator.choice(["", "\n"])
            with self.subTest(scenario=scenario):
                self.assertEqual(normalize(editor._encode_source(content, original)), content)

    def test_emptying_a_mixed_ending_line_does_not_collapse_two_newlines(self):
        original = b"one\rtwo\n"
        path = self.file(content=original)
        read = editor.read_source(self.root, "file.py")
        saved = editor.save_source(self.root, "file.py", "one\n\n", read["digest"])
        self.assertEqual(saved["content"], "one\n\n")
        self.assertEqual(editor.read_source(self.root, "file.py")["content"], "one\n\n")
        self.assertEqual(path.read_bytes(), b"one\r\r\n")

    def test_near_limit_periodic_edits_do_not_run_unbounded_sequence_matching(self):
        lines = [f"constant_{index % 100:03d}" for index in range(140000)]
        original = "".join(line + ("\r\n" if index % 2 else "\n") for index, line in enumerate(lines)).encode()
        self.assertLessEqual(len(original), editor._SOURCE_LIMIT)
        cases = []
        one_edit = list(lines); one_edit[len(lines) // 2] = "edited"
        cases.append(one_edit)
        # Large unresolved middle, unequal lengths; common edges are retained
        # but line-ending alignment must use the bounded positional fallback.
        broad_edit = ["new beginning", *lines[1:-1], "new ending", "extra line"]
        cases.append(broad_edit)
        with patch.object(editor.difflib, "SequenceMatcher", side_effect=AssertionError("unbounded alignment")):
            for changed in cases:
                content = "\n".join(changed) + "\n"
                encoded = editor._encode_source(content, original)
                self.assertEqual(encoded.decode().replace("\r\n", "\n").replace("\r", "\n"), content)
        self.assertEqual(editor._encode_source("\n".join(lines) + "\n", original), original)

    @unittest.skipIf(os.name == "nt", "Windows normalizes trailing spaces in filenames")
    def test_real_leading_and_trailing_spaces_in_filename(self):
        path = self.file(" leading and trailing.py ")
        read = editor.read_source(self.root, path.name)
        editor.save_source(self.root, path.name, "edited\n", read["digest"])
        self.assertEqual(path.read_bytes(), b"edited\n")

    def test_binary_invalid_encoding_and_large_files_are_unchanged(self):
        for raw, code in ((b"x\x00y", "source_binary"), (b"\xff\xfe", "source_encoding"), (b"x" * (editor._SOURCE_LIMIT + 1), "source_too_large")):
            path = self.file(content=raw)
            self.reject(code, editor.read_source, self.root, "file.py")
            self.assertEqual(path.read_bytes(), raw)
        path = self.file()
        read = editor.read_source(self.root, "file.py")
        self.reject("source_too_large", editor.save_source, self.root, "file.py", "é" * editor._SOURCE_LIMIT, read["digest"])
        self.reject("source_binary", editor.save_source, self.root, "file.py", "accidental\x00text", read["digest"])
        self.assertEqual(path.read_bytes(), b"first\nsecond\n")

    def test_read_only_files_refuse_replacement(self):
        path = self.file()
        read = editor.read_source(self.root, "file.py")
        path.chmod(stat.S_IREAD)
        self.addCleanup(path.chmod, stat.S_IREAD | stat.S_IWRITE)
        self.reject("source_save_failed", editor.save_source, self.root, "file.py", "changed", read["digest"])
        self.assertEqual(path.read_bytes(), b"first\nsecond\n")

    def test_save_failure_cleans_temporary_files_and_keeps_original(self):
        path = self.file()
        read = editor.read_source(self.root, "file.py")
        with patch.object(editor.os, "replace", side_effect=PermissionError("simulation read-only folder")):
            self.reject("source_save_failed", editor.save_source, self.root, "file.py", "changed", read["digest"])
        self.assertEqual(path.read_bytes(), b"first\nsecond\n")
        self.assertEqual(list(self.root.glob(".file.py.tb-*")), [])

    def test_revalidation_rejection_cleans_temporary_draft(self):
        path = self.file()
        read = editor.read_source(self.root, "file.py")
        with patch.object(editor, "_source_bytes", side_effect=[path.read_bytes(), commands.CommandRejected("source_binary", "Changed externally to binary")]):
            self.reject("source_binary", editor.save_source, self.root, "file.py", "private draft", read["digest"])
        self.assertEqual(list(self.root.glob(".file.py.tb-*")), [])

    def test_replaced_staging_file_is_never_published_as_the_users_draft(self):
        path = self.file()
        original = path.read_bytes()
        read = editor.read_source(self.root, "file.py")
        source_bytes = editor._source_bytes
        calls = 0
        def replace_staging(source):
            nonlocal calls
            calls += 1
            value = source_bytes(source)
            if calls == 2:
                staged = next(self.root.glob(".file.py.tb-*"))
                replacement = self.root / "foreign-stage"
                replacement.write_bytes(b"Not the user's draft")
                os.replace(replacement, staged)
            return value
        with patch.object(editor, "_source_bytes", side_effect=replace_staging):
            self.reject("source_changed", editor.save_source, self.root, "file.py", "The real draft", read["digest"])
        self.assertEqual(path.read_bytes(), original)
        # A staging name replaced by another writer is not ours to delete.
        self.assertEqual([item.read_bytes() for item in self.root.glob(".file.py.tb-*")], [b"Not the user's draft"])

    def test_read_permission_error_is_actionable(self):
        self.file()
        with patch.object(Path, "read_bytes", side_effect=PermissionError("simulation unreadable")):
            self.reject("source_read_failed", editor.read_source, self.root, "file.py")

    def test_path_errors_and_escaping_symlinks(self):
        self.file()
        self.reject("source_path_outside_project", editor.read_source, self.root, "../outside.py")
        self.reject("source_path_missing", editor.read_source, self.root, "missing.py")
        self.reject("source_path_invalid", editor.read_source, self.root, "bad\0path")
        outside = tempfile.TemporaryDirectory(prefix="tb-editor-outside-")
        self.addCleanup(outside.cleanup)
        external = Path(outside.name) / "private.py"
        external.write_bytes(b"private")
        try:
            (self.root / "escape.py").symlink_to(external)
        except OSError:
            self.skipTest("Native symlink creation requires additional OS privileges")
        self.reject("source_path_outside_project", editor.read_source, self.root, "escape.py")
        self.reject("source_path_outside_project", editor.save_source, self.root, "escape.py", "changed", hashlib.sha256(b"private").hexdigest())
        self.assertEqual(external.read_bytes(), b"private")


if __name__ == "__main__":
    unittest.main()
