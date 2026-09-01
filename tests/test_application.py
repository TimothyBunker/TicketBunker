from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import agent_runtime, application, bunkerdb, commands, config


class ApplicationStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.settings = self.root / ".tbconfig"
        subprocess.run(["git", "init", "-q", str(self.root / "source")], check=True)
        self.source = self.root / "source"
        self.config_patch = mock.patch.object(config, "GLOBAL_CONFIG", self.settings)
        self.config_patch.start()
        self.data_patch = mock.patch.dict(
            os.environ, {"TB_DATA_HOME": str(self.root / "app-data")}
        )
        self.data_patch.start()

    def tearDown(self) -> None:
        self.data_patch.stop()
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
        self.assertIn(value["platform"]["family"], {"linux", "windows", "macos"})
        self.assertEqual(
            value["platform"]["data_home"], str(self.root / "app-data")
        )
        self.assertEqual(
            value["diagnostics"]["application"],
            str(self.root / "app-data" / "diagnostics" / "app-worker.jsonl"),
        )
        self.assertEqual(value["release"]["current"], application.release_updates.__version__)

    def test_profile_update_is_machine_local_and_validated(self) -> None:
        result = commands.dispatch(
            "application.profile.update",
            {"name": "Ada", "email": "ada@example.test", "branch_user": "ada dev"},
            commands.CommandContext(self.root, transport="desktop"),
        )

        self.assertEqual(result["profile"]["branch_user"], "ada-dev")
        saved = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(saved["profile"]["email"], "ada@example.test")

    def test_release_check_uses_the_shared_command_and_returns_typed_state(self) -> None:
        with mock.patch.object(
            application.release_updates,
            "check",
            return_value={"current": "0.5.0", "latest": "0.5.1", "available": True},
        ) as checked:
            result = commands.dispatch(
                "application.release.check",
                {},
                commands.CommandContext(self.root, transport="desktop"),
            )

        self.assertTrue(result["release"]["available"])
        checked.assert_called_once_with()

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

    def test_desktop_creates_bunker_before_attaching_a_repository(self) -> None:
        context = commands.CommandContext(self.root, transport="desktop")
        created = commands.dispatch(
            "application.bunker.create",
            {"name": "Engineering", "project_key": "SYS", "project_name": "Systems"},
            context,
        )

        self.assertEqual(created["bunker"]["name"], "Engineering")
        self.assertEqual(created["bunker"]["repository_count"], 0)
        self.assertEqual(application.state()["bunkers"][0]["project_count"], 1)
        self.assertEqual(application.state()["legacy_projects"], [])

    def test_desktop_repository_attachment_does_not_write_a_locator(self) -> None:
        context = commands.CommandContext(self.root, transport="desktop")
        created = commands.dispatch(
            "application.bunker.create",
            {"name": "ECS", "project_key": "ECS"},
            context,
        )["bunker"]
        attached = commands.dispatch(
            "application.bunker.repository.attach",
            {
                "bunker_id": created["id"],
                "project_id": created["projects"][0]["id"],
                "path": str(self.source),
            },
            context,
        )["bunker"]

        self.assertEqual(attached["repositories"][0]["local_path"], str(self.source))
        self.assertFalse((self.source / ".ticketbunker.json").exists())

    def test_invitation_inspection_never_returns_the_bearer_value(self) -> None:
        result = commands.dispatch(
            "application.invitation.inspect",
            {
                "invitation": (
                    "ticketbunker://join?server=https%3A%2F%2Ftickets.example.test"
                    "&bunker=engineering&invite=secret"
                )
            },
            commands.CommandContext(self.root, transport="desktop"),
        )

        self.assertEqual(result["invitation"]["bunker_id"], "engineering")
        self.assertNotIn("secret", json.dumps(result))

    def test_ticket_read_commands_share_the_desktop_registry(self) -> None:
        created = bunkerdb.create_bunker("Engineering", project_key="ENG")
        with self.assertRaises(commands.CommandRejected) as rejected:
            commands.dispatch(
                "application.ticket.get",
                {"bunker_id": created["id"], "ticket_id": "ENG-1"},
                commands.CommandContext(self.root, transport="desktop"),
            )

        self.assertEqual(rejected.exception.code, "bunker_action_rejected")

    def test_configuration_editor_uses_the_same_revision_checked_commands(self) -> None:
        context = commands.CommandContext(self.root, transport="desktop")
        bunker = commands.dispatch(
            "application.bunker.create",
            {"name": "Engineering", "project_key": "ENG"},
            context,
        )["bunker"]
        project_id = bunker["projects"][0]["id"]
        before = commands.dispatch(
            "application.bunker.configuration.get",
            {"bunker_id": bunker["id"], "project_id": project_id},
            context,
        )
        document = before["documents"]["ticket-fields"]

        after = commands.dispatch(
            "application.bunker.configuration.replace",
            {
                "bunker_id": bunker["id"],
                "project_id": project_id,
                "kind": "ticket-fields",
                "expected_revision": document["revision"],
                "content": document["content"],
            },
            context,
        )

        self.assertEqual(after["scope_type"], "project")
        self.assertEqual(after["documents"]["ticket-fields"]["revision"], "local:2")

    def test_desktop_payload_can_travel_over_stdin_instead_of_process_arguments(self) -> None:
        invitation = (
            "ticketbunker://join?server=https%3A%2F%2Ftickets.example.test"
            "&bunker=engineering&invite=process-private"
        )
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                "from ticketbunker.cli import main; raise SystemExit(main())",
                "_app-command",
                "application.invitation.inspect",
                "--payload",
                "-",
            ],
            cwd=self.root,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
            input=json.dumps({"invitation": invitation}),
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertNotIn("process-private", process.stdout)
        self.assertEqual(json.loads(process.stdout)["invitation"]["bunker_id"], "engineering")

    def test_join_creates_a_replica_only_after_remote_enrollment(self) -> None:
        snapshot = application.remote.RemoteSnapshot(
            bunker={"id": "engineering", "name": "Engineering"},
            projects=({"id": "systems", "key": "SYS", "name": "Systems"},),
            repository_bindings=(),
            config_documents=(),
            members=({"id": "tim", "display_name": "Tim"},),
            current_member_id="tim",
            capabilities=("tickets:read",),
            revision="7",
        )
        enrollment = application.remote.Enrollment(snapshot, "vault:item-7")
        adapter = mock.Mock()
        adapter.enroll.return_value = enrollment
        with mock.patch.object(
            application.remote, "HTTPRemoteAdapter", return_value=adapter
        ):
            result = commands.dispatch(
                "application.bunker.join",
                {
                    "invitation": (
                        "ticketbunker://join?server=https%3A%2F%2Ftickets.example.test"
                        "&bunker=engineering&invite=one-use"
                    )
                },
                commands.CommandContext(self.root, transport="desktop"),
            )

        self.assertEqual(result["bunker"]["remote"]["config_revision"], "7")
        self.assertEqual(result["bunker"]["projects"][0]["key"], "SYS")
        adapter.enroll.assert_called_once()

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
