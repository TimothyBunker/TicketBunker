from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import commands, config, editor


class EditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("first\nsecond\n", encoding="utf-8")
        self.old_global_config = config.GLOBAL_CONFIG
        config.GLOBAL_CONFIG = self.root / "tbconfig.json"

    def tearDown(self) -> None:
        config.GLOBAL_CONFIG = self.old_global_config
        self.temp.cleanup()

    def test_automatic_editor_prefers_vim_then_nano(self) -> None:
        git_result = mock.Mock(returncode=1, stdout="")
        with mock.patch.dict(os.environ, {"TB_EDITOR": "", "VISUAL": "", "EDITOR": ""}):
            with mock.patch.object(editor.subprocess, "run", return_value=git_result):
                with mock.patch.object(
                    editor.shutil,
                    "which",
                    side_effect=lambda name: f"/usr/bin/{name}" if name in {"vim", "nano"} else None,
                ) as find:
                    value = editor.resolve_editor(self.root)

        self.assertEqual(value, ("vim", "automatic"))
        find.assert_called_once_with("vim")

    def test_automatic_editor_uses_nano_when_vim_is_unavailable(self) -> None:
        git_result = mock.Mock(returncode=1, stdout="")
        with mock.patch.dict(os.environ, {"TB_EDITOR": "", "VISUAL": "", "EDITOR": ""}):
            with mock.patch.object(editor.subprocess, "run", return_value=git_result):
                with mock.patch.object(
                    editor.shutil,
                    "which",
                    side_effect=lambda name: "/usr/bin/nano" if name == "nano" else None,
                ):
                    value = editor.resolve_editor(self.root)

        self.assertEqual(value, ("nano", "automatic"))

    def test_windows_git_bundled_vim_is_available_to_the_viewer(self) -> None:
        git = self.root / "Git" / "cmd" / "git.exe"
        vim = self.root / "Git" / "usr" / "bin" / "vim.exe"
        git.parent.mkdir(parents=True)
        vim.parent.mkdir(parents=True)
        git.write_text("git", encoding="utf-8")
        vim.write_text("vim", encoding="utf-8")

        candidates = editor._windows_git_editor_paths("vim", str(git))

        self.assertIn(vim, candidates)

    def test_editor_launch_materializes_the_discovered_executable(self) -> None:
        with mock.patch.object(editor, "_find_editor_executable", return_value="/found/vim"):
            argv, terminal = editor._editor_argv("vim", self.root / "src" / "main.py", 2)

        self.assertEqual(argv[0], "/found/vim")
        self.assertEqual(argv[1], "+2")
        self.assertTrue(terminal)

    def test_local_editor_command_persists_and_auto_clears_it(self) -> None:
        with mock.patch.object(editor.shutil, "which", return_value="/usr/bin/nano"):
            saved = commands.dispatch(
                "local.editor.update",
                {"editor": "nano"},
                commands.CommandContext(self.root, transport="test"),
            )
        self.assertEqual(config.load()["editor"], "nano")
        self.assertEqual(saved["editor_source"], "local setting")

        git_result = mock.Mock(returncode=1, stdout="")
        with mock.patch.dict(os.environ, {"TB_EDITOR": "", "VISUAL": "", "EDITOR": ""}):
            with mock.patch.object(editor.subprocess, "run", return_value=git_result):
                with mock.patch.object(editor.shutil, "which", return_value="/usr/bin/vim"):
                    automatic = commands.dispatch(
                        "local.editor.update",
                        {"editor": "auto"},
                        commands.CommandContext(self.root, transport="test"),
                    )
        self.assertNotIn("editor", config.load())
        self.assertEqual(automatic["effective_editor"], "vim")
        self.assertEqual(automatic["editor_source"], "automatic")

    def test_missing_editor_command_does_not_replace_the_saved_choice(self) -> None:
        config.save({"editor": "vim"})
        with mock.patch.object(editor.shutil, "which", return_value=None):
            with self.assertRaises(commands.CommandRejected) as caught:
                commands.dispatch(
                    "local.editor.update",
                    {"editor": "missing-editor"},
                    commands.CommandContext(self.root, transport="test"),
                )

        self.assertEqual(caught.exception.code, "local_editor_missing")
        self.assertEqual(config.load()["editor"], "vim")

    def test_shared_command_opens_an_inside_path_at_the_requested_line(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"TB_EDITOR": "code --goto {file}:{line}"},
        ):
            with mock.patch.object(editor, "_find_editor_executable", return_value="/found/code"):
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
            ["/found/code", "--goto", f"{self.root / 'src' / 'main.py'}:2"],
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
        with mock.patch.object(editor, "_find_editor_executable", return_value="/found/code"):
            argv, terminal = editor._editor_argv("code", self.root / "src" / "main.py", 2)

        self.assertEqual(argv[-2:], ["--goto", f"{self.root / 'src' / 'main.py'}:2"])
        self.assertEqual(argv[0], "/found/code")
        self.assertFalse(terminal)

    def test_templated_vim_still_requests_a_terminal(self) -> None:
        with mock.patch.object(editor, "_find_editor_executable", return_value="/found/vim"):
            argv, terminal = editor._editor_argv(
                "vim +{line} {file}", self.root / "src" / "main.py", 2
            )

        self.assertEqual(argv, ["/found/vim", "+2", str(self.root / "src" / "main.py")])
        self.assertTrue(terminal)


if __name__ == "__main__":
    unittest.main()
