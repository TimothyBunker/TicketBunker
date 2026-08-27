from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import agent_runtime, cli, config


class AgentCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_config = config.GLOBAL_CONFIG
        config.GLOBAL_CONFIG = self.root / "tbconfig.json"
        self.runtime = agent_runtime.Runtime(
            "/managed/pi", "managed", version="0.84.2", ready=True, integrity="verified"
        )
        self.ready = {
            "ready": True,
            "enabled": True,
            "status": "ready",
            "runtime": self.runtime.as_dict(),
            "provider": "openai-codex",
            "model": "gpt-5.6-sol",
            "summary": "Pi 0.84.2 · openai-codex / gpt-5.6-sol · oauth",
            "action": "",
        }

    def tearDown(self) -> None:
        config.GLOBAL_CONFIG = self.old_config
        self.temp.cleanup()

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_agent_status_names_the_persistent_selection(self) -> None:
        with mock.patch.object(agent_runtime, "state", return_value=self.ready):
            code, stdout, stderr = self._run(["agent", "status"])

        self.assertEqual(code, 0, stderr)
        self.assertIn("openai-codex / gpt-5.6-sol", stdout)

    def test_agent_model_accepts_scriptable_optional_overrides(self) -> None:
        with mock.patch.object(agent_runtime, "ensure_runtime", return_value=self.runtime):
            with mock.patch.object(agent_runtime, "change_selection", return_value=self.ready) as changed:
                code, stdout, stderr = self._run(
                    [
                        "agent", "model",
                        "--provider", "openai-codex",
                        "--model", "gpt-5.6-sol",
                    ]
                )

        self.assertEqual(code, 0, stderr)
        self.assertIn("gpt-5.6-sol", stdout)
        changed.assert_called_once_with(
            mock.ANY,
            interactive=False,
            provider="openai-codex",
            model="gpt-5.6-sol",
        )

    def test_init_passes_provider_and_model_to_shared_setup(self) -> None:
        connected = {
            "mode": "connected",
            "project": "ECS",
            "bunker_repository": "git@example.test:team/ecs-tb.git",
            "config_branch": "ticketbunker/config",
            "data_branch": "ticketbunker/data",
        }
        with mock.patch.object(cli.project_mod, "find_source_root", return_value=self.root):
            with mock.patch.object(cli.project_mod, "load_locator", return_value=None):
                with mock.patch.object(cli.commands, "dispatch", return_value=connected):
                    with mock.patch.object(cli, "_finish_agent_setup", return_value=0) as finish:
                        code, _stdout, stderr = self._run(
                            [
                                "init",
                                "--provider", "openai-codex",
                                "--model", "gpt-5.6-sol",
                            ]
                        )

        self.assertEqual(code, 0, stderr)
        finish.assert_called_once_with(
            self.root, False, "openai-codex", "gpt-5.6-sol"
        )

    def test_init_explains_the_later_model_command(self) -> None:
        with mock.patch.object(agent_runtime, "ensure_runtime", return_value=self.runtime):
            with mock.patch.object(agent_runtime, "onboard", return_value=self.ready):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = cli._finish_agent_setup(self.root, False)

        self.assertEqual(code, 0)
        self.assertIn("change model: tb agent model", stdout.getvalue())

    def test_core_only_rejects_model_overrides(self) -> None:
        code, _stdout, stderr = self._run(
            ["init", "--core-only", "--model", "gpt-5.6-sol"]
        )

        self.assertEqual(code, 1)
        self.assertIn("--core-only disables the agent", stderr)

    def test_synthesize_cli_dispatches_the_same_command_as_the_viewer(self) -> None:
        response = {
            "queued": True,
            "synthesis": {
                "label": "Synthesizing",
                "detail": "TicketBunker is updating Ada/TB-9-work because you requested it.",
            },
        }
        with mock.patch.object(cli.layout, "find_root", return_value=self.root):
            with mock.patch.object(cli.project_mod, "load_locator", return_value={"project": "TB"}):
                with mock.patch.object(cli.commands, "dispatch", return_value=response) as dispatched:
                    code, stdout, stderr = self._run(["synthesize", "TB-9"])

        self.assertEqual(code, 0, stderr)
        self.assertIn("Synthesizing", stdout)
        self.assertEqual(dispatched.call_args.args[0], "truth.synthesize")
        self.assertEqual(dispatched.call_args.args[1], {"project": "TB", "ticket": "TB-9"})
        self.assertEqual(dispatched.call_args.args[2].transport, "cli")

    def test_open_source_cli_dispatches_the_same_guarded_editor_command(self) -> None:
        response = {
            "opened": True,
            "path": "ticketbunker/viewer.py",
            "line": 42,
            "editor": "code",
        }
        with mock.patch.object(cli.project_mod, "find_source_root", return_value=self.root):
            with mock.patch.object(cli.commands, "dispatch", return_value=response) as dispatched:
                code, stdout, stderr = self._run(
                    ["open", "@[ticketbunker/viewer.py]:42"]
                )

        self.assertEqual(code, 0, stderr)
        self.assertIn("ticketbunker/viewer.py:42", stdout)
        self.assertEqual(dispatched.call_args.args[0], "editor.open")
        self.assertEqual(
            dispatched.call_args.args[1],
            {"path": "ticketbunker/viewer.py", "line": 42},
        )
        self.assertEqual(dispatched.call_args.args[2].transport, "cli")

    def test_open_source_cli_starts_the_owned_editor_when_selected(self) -> None:
        response = {
            "opened": True,
            "mode": "builtin",
            "path": "ticketbunker/viewer.py",
            "line": 42,
            "editor": "TicketBunker",
        }
        with mock.patch.object(cli.project_mod, "find_source_root", return_value=self.root):
            with mock.patch.object(cli.commands, "dispatch", return_value=response):
                with mock.patch.object(cli, "cmd_view", return_value=0) as start_viewer:
                    with mock.patch("webbrowser.open") as opened:
                        code, stdout, stderr = self._run(
                            ["open", "@[ticketbunker/viewer.py]:42"]
                        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("TicketBunker editor", stdout)
        start_viewer.assert_called_once()
        self.assertIn("/source?", opened.call_args.args[0])
        self.assertIn("ticketbunker%2Fviewer.py", opened.call_args.args[0])

    def test_view_opens_the_browser_by_default_and_keeps_foreground_optional(self) -> None:
        seen: list[argparse.Namespace] = []

        def view(args: argparse.Namespace) -> int:
            seen.append(args)
            return 0

        with mock.patch.object(cli, "cmd_view", side_effect=view):
            code, _stdout, stderr = self._run(["view"])

        self.assertEqual(code, 0, stderr)
        self.assertTrue(seen[0].open)
        self.assertFalse(seen[0].foreground)

    def test_background_viewer_passes_dash_prefixed_launch_id_as_one_argument(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"ok": True, "launch": "-launch-id", "root": str(self.root)}
        ).encode()
        process = mock.Mock()
        process.poll.return_value = None
        log_path = self.root / "viewer.log"
        with mock.patch.object(cli.layout, "find_root", return_value=self.root):
            with mock.patch.object(cli.project_mod, "find_source_root", return_value=None):
                with mock.patch.object(cli.project_mod, "load_locator", return_value=None):
                    with mock.patch.object(cli.secrets, "token_urlsafe", return_value="-launch-id"):
                        with mock.patch.object(cli.agent_runtime, "cli_argv", return_value=["tb"]):
                            with mock.patch("ticketbunker.viewer.server_log_path", return_value=log_path):
                                with mock.patch(
                                    "urllib.request.urlopen", side_effect=[OSError(), response]
                                ):
                                    with mock.patch.object(
                                        cli.subprocess, "Popen", return_value=process
                                    ) as opened:
                                        code, stdout, stderr = self._run(
                                            ["view", "--no-open", "--port", "18499"]
                                        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("--launch=-launch-id", opened.call_args.args[0])
        self.assertNotIn("--launch", opened.call_args.args[0])
        self.assertIn("stop after its last browser tab closes", stdout)

    def test_view_and_source_open_do_not_publish_companion_data(self) -> None:
        with mock.patch.object(cli, "cmd_view", return_value=0):
            with mock.patch.object(cli, "cmd_open_source", return_value=0):
                with mock.patch.object(cli.project_mod, "find_source_root", return_value=self.root):
                    with mock.patch.object(
                        cli.project_mod, "load_locator", return_value={"project": "TB"}
                    ):
                        with mock.patch.object(cli.project_mod, "publish_data") as publish:
                            view_code, _stdout, view_stderr = self._run(["view"])
                            open_code, _stdout, open_stderr = self._run(
                                ["open", "ticketbunker/viewer.py"]
                            )

        self.assertEqual(view_code, 0, view_stderr)
        self.assertEqual(open_code, 0, open_stderr)
        publish.assert_not_called()

    def test_local_editor_config_uses_shared_command_without_publishing(self) -> None:
        with mock.patch.object(cli.editor_mod.shutil, "which", return_value="/usr/bin/nano"):
            with mock.patch.object(cli.project_mod, "find_source_root", return_value=self.root):
                with mock.patch.object(
                    cli.project_mod, "load_locator", return_value={"project": "TB"}
                ):
                    with mock.patch.object(cli.project_mod, "publish_data") as publish:
                        code, stdout, stderr = self._run(["config", "--editor", "nano"])

        self.assertEqual(code, 0, stderr)
        self.assertIn("editor: nano (local setting)", stdout)
        self.assertEqual(config.load()["editor"], "nano")
        publish.assert_not_called()

    def test_pi_run_without_command_template_keeps_its_subcommand_name(self) -> None:
        """``tb run --cmd`` must not overwrite argparse's top-level command."""
        with mock.patch.object(cli, "cmd_run", return_value=0):
            with mock.patch.object(cli.project_mod, "find_source_root", return_value=None):
                code, _stdout, stderr = self._run(["run", "--harness", "pi"])

        self.assertEqual(code, 0, stderr)

    def test_agent_pipeline_defers_companion_publication_to_its_worker(self) -> None:
        with mock.patch.dict(os.environ, {"TB_AGENT_ACTIVE": "1"}):
            with mock.patch.object(cli, "cmd_run", return_value=0):
                with mock.patch.object(cli.project_mod, "find_source_root", return_value=self.root):
                    with mock.patch.object(cli.project_mod, "load_locator", return_value={"project": "TB"}):
                        with mock.patch.object(cli.project_mod, "publish_data") as publish:
                            code, _stdout, stderr = self._run(["run", "--harness", "pi"])

        self.assertEqual(code, 0, stderr)
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
