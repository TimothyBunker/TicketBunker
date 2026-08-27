from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import commands, editor


class EditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("first\nsecond\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_shared_command_opens_an_inside_path_at_the_requested_line(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TB_EDITOR": "code --goto {file}:{line}"},
        ):
            with mock.patch.object(editor.subprocess, "Popen") as opened:
                value = commands.dispatch(
                    "editor.open",
                    {"path": "src/main.py", "line": 2},
                    commands.CommandContext(self.root, transport="test"),
                )

        self.assertTrue(value["opened"])
        self.assertEqual(value["path"], "src/main.py")
        self.assertEqual(value["line"], 2)
        self.assertEqual(
            opened.call_args.args[0],
            ["code", "--goto", f"{self.root / 'src' / 'main.py'}:2"],
        )
        self.assertFalse(opened.call_args.kwargs.get("shell", False))

    def test_shared_command_rejects_paths_outside_the_checkout(self) -> None:
        outside = self.root.parent / "outside.py"
        outside.write_text("no\n", encoding="utf-8")
        self.addCleanup(outside.unlink, missing_ok=True)

        with self.assertRaises(commands.CommandRejected) as caught:
            commands.dispatch(
                "editor.open",
                {"path": str(outside), "line": 1},
                commands.CommandContext(self.root, transport="test"),
            )

        self.assertEqual(caught.exception.code, "source_path_outside_project")

    def test_code_editor_gets_its_native_goto_arguments(self) -> None:
        argv, terminal = editor._editor_argv("code", self.root / "src" / "main.py", 2)

        self.assertEqual(argv[-2:], ["--goto", f"{self.root / 'src' / 'main.py'}:2"])
        self.assertFalse(terminal)

    def test_templated_vim_still_requests_a_terminal(self) -> None:
        argv, terminal = editor._editor_argv(
            "vim +{line} {file}", self.root / "src" / "main.py", 2
        )

        self.assertEqual(argv, ["vim", "+2", str(self.root / "src" / "main.py")])
        self.assertTrue(terminal)


if __name__ == "__main__":
    unittest.main()
