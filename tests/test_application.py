from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import agent_runtime, application, commands, config


class ApplicationStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.settings = self.root / ".tbconfig"
        subprocess.run(["git", "init", "-q", str(self.root / "source")], check=True)
        self.source = self.root / "source"
        self.config_patch = mock.patch.object(config, "GLOBAL_CONFIG", self.settings)
        self.config_patch.start()

    def tearDown(self) -> None:
        self.config_patch.stop()
        self.tempdir.cleanup()

    def test_state_defaults_the_local_profile_from_git(self) -> None:
        with mock.patch.object(
            application,
            "_git_value",
            side_effect=lambda key: {"user.name": "Ada Lovelace", "user.email": "ada@example.test"}[key],
        ):
            value = application.state()

        self.assertEqual(value["profile"]["name"], "Ada Lovelace")
        self.assertEqual(value["profile"]["branch_user"], "Ada-Lovelace")
        self.assertTrue(value["profile"]["complete"])

    def test_profile_update_is_machine_local_and_validated(self) -> None:
        result = commands.dispatch(
            "application.profile.update",
            {"name": "Ada", "email": "ada@example.test", "branch_user": "ada dev"},
            commands.CommandContext(self.root, transport="desktop"),
        )

        self.assertEqual(result["profile"]["branch_user"], "ada-dev")
        saved = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(saved["profile"]["email"], "ada@example.test")

    def test_add_project_normalizes_the_git_root_and_deduplicates(self) -> None:
        nested = self.source / "src"
        nested.mkdir()
        context = commands.CommandContext(self.root, transport="desktop")
        first = commands.dispatch(
            "application.project.add", {"path": str(nested)}, context
        )
        second = commands.dispatch(
            "application.project.add", {"path": str(self.source)}, context
        )

        self.assertEqual(first["project"]["path"], str(self.source))
        self.assertFalse(first["project"]["initialized"])
        self.assertEqual(len(second["projects"]), 1)

    def test_project_storage_defaults_from_the_source_remote(self) -> None:
        subprocess.run(
            [
                "git",
                "-C",
                str(self.source),
                "remote",
                "add",
                "origin",
                "git@github.com:TimothyBunker/coal.git",
            ],
            check=True,
        )

        storage = application.inspect_project(str(self.source))["storage"]

        self.assertEqual(
            storage,
            {
                "backend": "git",
                "transport": "ssh",
                "host": "github.com",
                "namespace": "TimothyBunker",
                "repository": "coal-ticketbunker",
            },
        )

    def test_https_source_remote_preserves_nested_gitlab_group(self) -> None:
        subprocess.run(
            [
                "git",
                "-C",
                str(self.source),
                "remote",
                "add",
                "origin",
                "https://gitlab.example.test/platform/render/voxel.git",
            ],
            check=True,
        )

        storage = application.inspect_project(str(self.source))["storage"]

        self.assertEqual(storage["transport"], "https")
        self.assertEqual(storage["host"], "gitlab.example.test")
        self.assertEqual(storage["namespace"], "platform/render")
        self.assertEqual(storage["repository"], "voxel-ticketbunker")

    def test_desktop_connect_composes_structured_storage_in_the_backend(self) -> None:
        with mock.patch.object(
            application.commands,
            "dispatch",
            return_value={"project": "ECS"},
        ) as dispatch:
            application.command_project_connect(
                commands.CommandContext(self.root, transport="desktop"),
                {
                    "path": str(self.source),
                    "project": "ECS",
                    "storage": {
                        "backend": "git",
                        "transport": "ssh",
                        "host": "github.com",
                        "namespace": "TimothyBunker",
                        "repository": "coal-ticketbunker",
                    },
                },
            )

        dispatched_payload = dispatch.call_args.args[1]
        self.assertEqual(
            dispatched_payload["bunker_repo"],
            "git@github.com:TimothyBunker/coal-ticketbunker.git",
        )

    def test_desktop_connect_rejects_a_host_that_contains_git_syntax(self) -> None:
        with self.assertRaisesRegex(commands.CommandRejected, "only the Git host"):
            application._storage_remote(
                {
                    "transport": "ssh",
                    "host": "github.com:team/repo",
                    "namespace": "team",
                    "repository": "repo",
                }
            )

    def test_non_git_directory_is_rejected_actionably(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        with self.assertRaisesRegex(commands.CommandRejected, "Git working tree"):
            application.inspect_project(str(outside))

    def test_agent_decision_is_distinct_from_model_configuration(self) -> None:
        self.assertFalse(application.state()["agent"]["decided"])
        config.save({"agent": {"enabled": False}})

        value = application.state()["agent"]

        self.assertTrue(value["decided"])
        self.assertFalse(value["enabled"])
        self.assertFalse(value["configured"])

    def test_agent_prepare_returns_only_runtime_paths_and_non_secret_state(self) -> None:
        runtime = agent_runtime.Runtime(
            str(self.root / "pi"), "managed", version="0.84.2", ready=True
        )
        status = {
            "ready": False,
            "enabled": True,
            "status": "selection-required",
            "provider": "",
            "model": "",
        }
        with mock.patch.object(agent_runtime, "ensure_runtime", return_value=runtime):
            with mock.patch.object(
                agent_runtime, "auth_extension_path", return_value=self.root / "tb_auth.ts"
            ):
                with mock.patch.object(agent_runtime, "state", return_value=status):
                    result = commands.dispatch(
                        "application.agent.prepare",
                        {},
                        commands.CommandContext(self.root, transport="desktop"),
                    )

        self.assertEqual(result["runtime"], runtime.path)
        self.assertEqual(result["agent"], status)
        self.assertNotIn("credential", json.dumps(result).lower())

    def test_opening_agent_setup_does_not_make_an_agent_decision(self) -> None:
        runtime = agent_runtime.Runtime(
            str(self.root / "pi"), "managed", version="0.84.2", ready=True
        )

        def installed_runtime():
            config.save({"agent": {"enabled": True, "pi_bin": runtime.path}})
            return runtime

        with mock.patch.object(agent_runtime, "ensure_runtime", side_effect=installed_runtime):
            with mock.patch.object(
                agent_runtime, "auth_extension_path", return_value=self.root / "tb_auth.ts"
            ):
                with mock.patch.object(
                    agent_runtime,
                    "state",
                    return_value={"ready": False, "status": "selection-required"},
                ):
                    commands.dispatch(
                        "application.agent.prepare",
                        {},
                        commands.CommandContext(self.root, transport="desktop"),
                    )

        self.assertNotIn("agent", config.load())

    def test_agent_configuration_supports_explicit_no_model_mode(self) -> None:
        with mock.patch.object(agent_runtime, "set_core_only") as disabled:
            with mock.patch.object(
                agent_runtime,
                "state",
                return_value={"ready": True, "enabled": False, "status": "disabled"},
            ):
                result = commands.dispatch(
                    "application.agent.configure",
                    {"enabled": False},
                    commands.CommandContext(self.root, transport="desktop"),
                )

        disabled.assert_called_once_with()
        self.assertFalse(result["agent"]["enabled"])

    def test_agent_configuration_uses_the_shared_validated_selection(self) -> None:
        runtime = agent_runtime.Runtime(
            str(self.root / "pi"), "managed", version="0.84.2", ready=True
        )
        configured = {
            "ready": True,
            "enabled": True,
            "status": "ready",
            "provider": "openai-codex",
            "model": "gpt-5.6-sol",
        }
        with mock.patch.object(agent_runtime, "ensure_runtime", return_value=runtime):
            with mock.patch.object(
                agent_runtime, "configure_selection", return_value=configured
            ) as selection:
                result = commands.dispatch(
                    "application.agent.configure",
                    {
                        "enabled": True,
                        "provider": "openai-codex",
                        "model": "gpt-5.6-sol",
                    },
                    commands.CommandContext(self.source, transport="desktop"),
                )

        selection.assert_called_once_with(
            runtime,
            provider="openai-codex",
            model="gpt-5.6-sol",
            repo=self.source,
        )
        self.assertEqual(result["agent"], configured)


if __name__ == "__main__":
    unittest.main()
