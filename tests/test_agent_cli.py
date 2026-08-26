from __future__ import annotations

import contextlib
import io
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


if __name__ == "__main__":
    unittest.main()
