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

    def test_built_in_editor_is_the_install_free_default(self) -> None:
        with mock.patch.object(editor, "_editor_argv") as external:
            value = commands.dispatch(
                "editor.open",
                {"path": "src/main.py", "line": 2},
                commands.CommandContext(self.root, transport="test"),
            )

        self.assertEqual(value["mode"], "builtin")
        self.assertEqual(value["editor"], "TicketBunker")
        external.assert_not_called()

    def test_unavailable_external_editor_falls_back_to_the_included_editor(self) -> None:
        config.save({"editor_mode": "external", "editor": "vim"})
        problem = commands.CommandRejected(
            "source_editor_terminal_required",
            "No supported terminal launcher was found.",
        )
        with mock.patch.object(editor, "_find_editor_executable", return_value="/found/vim"):
            with mock.patch.object(editor, "_terminal_argv", side_effect=problem):
                value = commands.dispatch(
                    "editor.open",
                    {"path": "src/main.py", "line": 2},
                    commands.CommandContext(self.root, transport="test"),
                )

        self.assertEqual(value["mode"], "builtin")
        self.assertEqual(value["editor"], "TicketBunker")
        self.assertEqual(value["fallback"], "source_editor_terminal_required")

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
        self.assertEqual(editor.local_state(self.root)["editor_mode"], "builtin")

    def test_builtin_format_settings_do_not_require_the_hidden_external_command(self) -> None:
        config.save({"editor_mode": "builtin", "editor": "missing-editor"})
        with mock.patch.object(editor.shutil, "which", return_value=None):
            saved = commands.dispatch(
                "local.editor.update",
                {
                    "editor_mode": "builtin",
                    "editor": "missing-editor",
                    "indent_style": "spaces",
                    "tab_size": 3,
                },
                commands.CommandContext(self.root, transport="test"),
            )

        self.assertEqual(saved["editor_mode"], "builtin")
        self.assertEqual(saved["tab_size"], 3)
        self.assertEqual(config.load()["editor"], "missing-editor")

    def test_builtin_read_and_save_preserve_newlines_and_apply_opted_in_cleanup(self) -> None:
        source = self.root / "src" / "main.py"
        source.write_bytes(b"first  \r\nsecond")
        config.save(
            {
                "editor_mode": "builtin",
                "indent_style": "spaces",
                "tab_size": 2,
                "trim_trailing_whitespace": True,
                "ensure_final_newline": True,
            }
        )

        opened = commands.dispatch(
            "editor.read",
            {"path": "src/main.py", "line": 2},
            commands.CommandContext(self.root, transport="test"),
        )
        saved = commands.dispatch(
            "editor.save",
            {
                "path": "src/main.py",
                "content": "first  \nchanged  ",
                "expected_digest": opened["digest"],
            },
            commands.CommandContext(self.root, transport="test"),
        )

        self.assertEqual(opened["content"], "first  \nsecond")
        self.assertEqual(opened["newline"], "crlf")
        self.assertEqual(opened["settings"]["tab_size"], 2)
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["content"], "first\nchanged\n")
        self.assertEqual(source.read_bytes(), b"first\r\nchanged\r\n")

    def test_builtin_save_refuses_to_overwrite_a_file_changed_on_disk(self) -> None:
        opened = editor.read_source(self.root, "src/main.py", 1)
        source = self.root / "src" / "main.py"
        source.write_text("newer\n", encoding="utf-8")

        with self.assertRaises(commands.CommandRejected) as caught:
            editor.save_source(self.root, "src/main.py", "draft\n", opened["digest"])

        self.assertEqual(caught.exception.code, "source_changed")
        self.assertEqual(source.read_text(encoding="utf-8"), "newer\n")

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
