from __future__ import annotations

import json
import os
import stat
import threading
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from ticketbunker import agent_runtime, application, bunkerdb, commands, config, credentials, projectconfig
from ticketbunker.integrations import github
from ticketbunker.service import server as service_server
from ticketbunker.service import storage as service_storage


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
        subprocess.run(
            [
                "git", "-C", str(self.source), "remote", "add", "origin",
                "git@github.com:Example/coal.git",
            ],
            check=True,
        )
        context = commands.CommandContext(self.root, transport="desktop")
        created = commands.dispatch(
            "application.bunker.create",
            {"name": "ECS", "project_key": "ECS"},
            context,
        )["bunker"]
        project_id = created["projects"][0]["id"]
        configuration = commands.dispatch(
            "application.bunker.configuration.get",
            {
                "bunker_id": created["id"],
                "project_id": project_id,
            },
            context,
        )
        configured = commands.dispatch(
            "application.bunker.repository.replace",
            {
                "bunker_id": created["id"],
                "project_id": project_id,
                "expected_revision": configuration["repositories"]["revision"],
                "repositories": [
                    {
                        "name": "Coal",
                        "canonical_remote": "github.com/Example/coal",
                        "default_branch": "main",
                    }
                ],
            },
            context,
        )
        binding_id = configured["configuration"]["repositories"]["items"][0]["id"]
        with mock.patch.object(bunkerdb, "_fetch_repository"):
            attached = commands.dispatch(
                "application.bunker.repository.attach",
                {
                    "bunker_id": created["id"],
                    "project_id": project_id,
                    "binding_id": binding_id,
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


class SharedBunkerTests(unittest.TestCase):
    """The owner and member flows against the in-process reference service."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.store = service_storage.ServiceStore(self.root / "service" / "service.sqlite3")
        self.store.initialize()
        self.setup_token = self.store.issue_setup_token()
        self.server = service_server.serve(self.store, host="127.0.0.1", port=0, quiet=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = self.server.origin
        self.context = commands.CommandContext(self.root, transport="desktop")
        self.patches: list[Any] = []

    def tearDown(self) -> None:
        self._stop_patches()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tempdir.cleanup()

    def _stop_patches(self) -> None:
        while self.patches:
            self.patches.pop().stop()

    def as_user(self, name: str) -> None:
        """Switch to another computer: its own settings file and data home."""
        self._stop_patches()
        home = self.root / "computers" / name
        home.mkdir(parents=True, exist_ok=True)
        settings = home / ".tbconfig"
        for patch in (
            mock.patch.object(config, "GLOBAL_CONFIG", settings),
            mock.patch.dict(os.environ, {"TB_DATA_HOME": str(home / "data")}),
        ):
            patch.start()
            self.patches.append(patch)
        if not settings.exists():
            self.dispatch(
                "application.profile.update",
                {"name": name.title(), "email": f"{name}@example.test", "branch_user": name},
            )

    def dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return commands.dispatch(action, payload, self.context)

    def test_owner_shares_invites_and_publishes_through_the_service(self) -> None:
        self.as_user("tim")
        created = self.dispatch(
            "application.bunker.create",
            {"name": "Engineering", "project_key": "ECS", "project_name": "Entity Component System"},
        )["bunker"]
        project_id = created["projects"][0]["id"]
        configuration = self.dispatch(
            "application.bunker.configuration.get",
            {"bunker_id": created["id"], "project_id": project_id},
        )
        self.dispatch(
            "application.bunker.repository.replace",
            {
                "bunker_id": created["id"],
                "project_id": project_id,
                "expected_revision": configuration["repositories"]["revision"],
                "repositories": [
                    {"name": "Coal", "canonical_remote": "github.com/example/coal", "default_branch": "main"}
                ],
            },
        )

        with self.assertRaises(commands.CommandRejected) as missing:
            self.dispatch(
                "application.bunker.share", {"bunker_id": created["id"], "server_url": self.origin}
            )
        self.assertEqual(missing.exception.code, "service_setup_required")

        shared = self.dispatch(
            "application.bunker.share",
            {"bunker_id": created["id"], "server_url": self.origin, "setup_token": self.setup_token},
        )["bunker"]

        self.assertEqual(shared["mode"], "remote")
        self.assertEqual(shared["remote"]["server_url"], self.origin)
        self.assertTrue(shared["sharing"]["is_owner"])
        self.assertTrue(shared["sharing"]["can_invite"])
        self.assertEqual(shared["membership"]["role"], "owner")
        credential_path = credentials.store_path()
        self.assertTrue(credential_path.is_file())
        self.assertEqual(stat.S_IMODE(credential_path.stat().st_mode), 0o600)
        owner_secret = credentials.resolve(credentials.find(self.origin))
        self.assertTrue(owner_secret)
        self.assertNotIn(owner_secret, json.dumps(shared))
        self.assertNotIn(owner_secret, json.dumps(application.state()))
        self.assertNotIn(owner_secret, bunkerdb.database_path(created["id"]).read_bytes().decode("latin-1"))

        with self.assertRaises(commands.CommandRejected) as again:
            self.dispatch(
                "application.bunker.share",
                {"bunker_id": created["id"], "server_url": self.origin, "setup_token": "x"},
            )
        self.assertEqual(again.exception.code, "bunker_already_shared")

        # The owner keeps configuring; every shared change is accepted by the
        # service before it is written locally.
        bunker_config = self.dispatch(
            "application.bunker.configuration.get", {"bunker_id": created["id"]}
        )
        self.assertTrue(bunker_config["permission"]["can_write"])
        fields = bunker_config["documents"]["ticket-fields"]
        updated = self.dispatch(
            "application.bunker.configuration.replace",
            {
                "bunker_id": created["id"],
                "project_id": "",
                "kind": "ticket-fields",
                "expected_revision": fields["revision"],
                "content": fields["content"],
            },
        )
        self.assertTrue(updated["documents"]["ticket-fields"]["revision"].startswith("shared:"))
        with self.assertRaises(commands.CommandRejected) as stale:
            self.dispatch(
                "application.bunker.configuration.replace",
                {
                    "bunker_id": created["id"],
                    "project_id": "",
                    "kind": "ticket-fields",
                    "expected_revision": fields["revision"],
                    "content": fields["content"],
                },
            )
        self.assertIn("Reload", stale.exception.message)
        added = self.dispatch(
            "application.bunker.project.create",
            {"bunker_id": created["id"], "key": "REN", "name": "Rendering"},
        )["bunker"]
        self.assertEqual({item["key"] for item in added["projects"]}, {"ECS", "REN"})
        render_id = next(item["id"] for item in added["projects"] if item["key"] == "REN")
        render_config = self.dispatch(
            "application.bunker.configuration.get",
            {"bunker_id": created["id"], "project_id": render_id},
        )
        self.assertEqual(
            render_config["documents"]["ticket-fields"]["content"], fields["content"]
        )
        self.dispatch(
            "application.bunker.repository.replace",
            {
                "bunker_id": created["id"],
                "project_id": render_id,
                "expected_revision": render_config["repositories"]["revision"],
                "repositories": [{"name": "Flux", "canonical_remote": "github.com/example/flux"}],
            },
        )
        self.assertEqual(bunkerdb.remote_connection(created["id"])["sync_cursor"], "change-5")

        invitation = self.dispatch(
            "application.bunker.invitation.create", {"bunker_id": created["id"]}
        )["invitation"]
        self.assertTrue(invitation["link"].startswith("ticketbunker://join?"))
        self.assertEqual(invitation["role"], "member")
        listed = self.dispatch("application.bunker.invitations", {"bunker_id": created["id"]})
        self.assertEqual([item["state"] for item in listed["invitations"]], ["pending"])
        self.assertNotIn(invitation["link"], json.dumps(listed))

        # A member joins from another computer and lands fully configured.
        self.as_user("ada")
        joined = self.dispatch("application.bunker.join", {"invitation": invitation["link"]})["bunker"]
        self.assertEqual(joined["mode"], "remote")
        self.assertEqual(joined["sharing"]["role"], "member")
        self.assertFalse(joined["sharing"]["can_invite"])
        self.assertFalse(joined["sharing"]["can_share"])
        self.assertEqual({item["key"] for item in joined["projects"]}, {"ECS", "REN"})
        repositories = {item["name"]: item for item in joined["repositories"]}
        self.assertEqual(set(repositories), {"Coal", "Flux"})
        self.assertIsNone(repositories["Coal"]["local_path"])
        member_config = self.dispatch(
            "application.bunker.configuration.get", {"bunker_id": joined["id"]}
        )
        self.assertFalse(member_config["permission"]["can_write"])
        self.assertEqual(
            member_config["documents"]["ticket-fields"]["revision"],
            updated["documents"]["ticket-fields"]["revision"],
        )
        with self.assertRaises(commands.CommandRejected) as forbidden:
            self.dispatch(
                "application.bunker.configuration.replace",
                {
                    "bunker_id": joined["id"],
                    "project_id": "",
                    "kind": "ticket-fields",
                    "expected_revision": member_config["documents"]["ticket-fields"]["revision"],
                    "content": fields["content"],
                },
            )
        self.assertIn("only a Bunker owner", forbidden.exception.message)
        with self.assertRaises(commands.CommandRejected) as cannot_invite:
            self.dispatch("application.bunker.invitation.create", {"bunker_id": joined["id"]})
        self.assertIn("Only the Bunker owner", cannot_invite.exception.message)
        members = self.dispatch("application.bunker.members", {"bunker_id": joined["id"]})
        self.assertEqual(sorted(item["role"] for item in members["members"]), ["member", "owner"])
        member_secret = credentials.resolve(credentials.find(self.origin))
        self.assertTrue(member_secret)
        self.assertNotEqual(member_secret, owner_secret)

        # The link was single use.
        self.as_user("eve")
        with self.assertRaises(commands.CommandRejected) as reused:
            self.dispatch("application.bunker.join", {"invitation": invitation["link"]})
        self.assertIn("already used", reused.exception.message)

        # The owner connects the Project's tracker board and syncs it; every
        # ticket is accepted by the service before it lands locally.
        self.as_user("tim")
        inspection = {"kind": "project", "title": "Voxel ECS", "url": "https://github.com/users/example/projects/8", "items": 2, "fields": [{"name": "Status", "type": "SINGLE_SELECT", "options": ["Todo", "Done"]}], "labels": []}
        with mock.patch.dict(os.environ, {"GITHUB_ACCESS_TOKEN": "ghp_owner"}):
            with mock.patch.object(github, "inspect_board", return_value=inspection):
                linked = self.dispatch(
                    "application.project.link",
                    {"bunker_id": created["id"], "project_id": project_id, "board": {"kind": "project", "owner": "example", "number": 8, "repository": "", "title": "Voxel ECS", "url": ""}},
                )
            self.assertEqual(linked["configuration"]["documents"]["tracker-source"]["content"]["title"], "Voxel ECS")
            with mock.patch.object(github, "collect_entries", return_value=[github_entry(4), github_entry(5)]):
                synced = self.dispatch(
                    "application.project.sync", {"bunker_id": created["id"], "project_id": project_id}
                )["sync"]
        self.assertEqual(synced["created"], 2)
        # fields, mapping, board, then two tickets, after the five earlier changes
        self.assertEqual(bunkerdb.remote_connection(created["id"])["sync_cursor"], "change-10")

        # A member joining afterwards receives the tracker and its tickets, and
        # may sync too because members hold tickets:write.
        late_link = self.dispatch(
            "application.bunker.invitation.create", {"bunker_id": created["id"]}
        )["invitation"]["link"]
        self.as_user("lin")
        late = self.dispatch("application.bunker.join", {"invitation": late_link})["bunker"]
        self.assertEqual(late["projects"][0]["tracker"]["title"], "Voxel ECS")
        late_fields = self.dispatch("application.bunker.configuration.get", {"bunker_id": late["id"], "project_id": late["projects"][0]["id"]})
        self.assertEqual({f["key"]: f for f in late_fields["documents"]["ticket-fields"]["content"]["fields"]}["status"]["values"], ["todo", "done"])
        self.assertEqual(late["projects"][0]["ticket_count"], 2)
        late_project = late["projects"][0]["id"]
        with mock.patch.dict(os.environ, {"GITHUB_ACCESS_TOKEN": "ghp_member"}):
            with mock.patch.object(github, "collect_entries", return_value=[github_entry(4), github_entry(5, "Done"), github_entry(6)]):
                member_sync = self.dispatch(
                    "application.project.sync", {"bunker_id": late["id"], "project_id": late_project}
                )["sync"]
        self.assertEqual((member_sync["created"], member_sync["updated"], member_sync["unchanged"]), (1, 1, 1))

        # Ada's replica joined before any of this. Sync now brings the tracker,
        # every ticket, and the configuration without a new invitation.
        self.as_user("ada")
        before = self.dispatch("application.ticket.list", {"bunker_id": joined["id"], "project_id": joined["projects"][0]["id"], "status": "all"})
        self.assertEqual(before["total"], 0)
        pulled = self.dispatch("application.bunker.sync", {"bunker_id": joined["id"]})
        self.assertEqual(pulled["sync"]["tickets"], 3)
        self.assertEqual(pulled["bunker"]["projects"][0]["tracker"]["title"], "Voxel ECS")
        self.assertEqual(pulled["bunker"]["projects"][0]["ticket_count"], 3)
        self.assertTrue(pulled["bunker"]["remote"]["last_synced_at"])
        after = self.dispatch("application.ticket.list", {"bunker_id": joined["id"], "project_id": joined["projects"][0]["id"], "status": "all"})
        self.assertEqual({item["id"] for item in after["tickets"]}, {"ECS-4", "ECS-5", "ECS-6"})
        self.assertEqual({item["id"]: item["status"] for item in after["tickets"]}["ECS-5"], "done")

        # Only the owner revokes invitations.
        self.as_user("tim")
        second = self.dispatch(
            "application.bunker.invitation.create", {"bunker_id": created["id"]}
        )["invitation"]
        revoked = self.dispatch(
            "application.bunker.invitation.revoke",
            {"bunker_id": created["id"], "invitation_id": second["id"]},
        )["invitations"]
        self.assertEqual({item["id"]: item["state"] for item in revoked}[second["id"]], "revoked")
        self.assertEqual(
            sorted(item["role"] for item in self.dispatch("application.bunker.members", {"bunker_id": created["id"]})["members"]),
            ["member", "member", "owner"],
        )


def github_entry(number: int, status: str = "Todo", *, keyed: bool = False) -> dict[str, Any]:
    title = f"ECS-{number} Mock ticket {number}" if keyed else f"Mock ticket {number}"
    return {
        "issue": {
            "node_id": f"I_{number}",
            "number": number,
            "title": title,
            "body": f"Description {number}",
            "state": "open",
            "html_url": f"https://github.com/example/voxel/issues/{number}",
            "created_at": "2026-08-20T12:00:00Z",
            "updated_at": "2026-08-21T12:00:00Z",
            "user": {"login": "Tim"},
            "assignee": {"login": "Ada"},
            "assignees": [{"login": "Ada"}],
            "labels": [{"name": "ecs"}],
        },
        "comments": [],
        "project": {
            "owner": "example",
            "number": 8,
            "title": "Voxel ECS",
            "url": "https://github.com/users/example/projects/8",
            "item_id": f"PVTI_{number}",
            "fields": {"Status": status},
        },
    }


class TrackerSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        # The credential file lives in the user's home, never inside the
        # working tree the command runs from.
        (self.root / "home").mkdir()
        (self.root / "work").mkdir()
        self.patches = [
            mock.patch.object(config, "GLOBAL_CONFIG", self.root / "home" / ".tbconfig"),
            mock.patch.dict(os.environ, {"TB_DATA_HOME": str(self.root / "home" / "app-data")}),
            mock.patch.object(
                projectconfig, "credential_file", return_value=self.root / "home" / "credentials.env"
            ),
        ]
        for patch in self.patches:
            patch.start()
        os.environ.pop("GITHUB_ACCESS_TOKEN", None)
        self.context = commands.CommandContext(self.root / "work", transport="desktop")

    def tearDown(self) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.tempdir.cleanup()

    def dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return commands.dispatch(action, payload, self.context)

    def test_a_project_syncs_its_tracker_board_into_tickets(self) -> None:
        created = self.dispatch(
            "application.bunker.create",
            {"name": "Engineering", "project_key": "ECS", "project_name": "Entity Component System"},
        )["bunker"]
        project_id = created["projects"][0]["id"]
        self.assertFalse(application.state()["trackers"]["github"])

        with self.assertRaises(commands.CommandRejected) as no_tracker:
            self.dispatch("application.project.sync", {"bunker_id": created["id"], "project_id": project_id})
        self.assertEqual(no_tracker.exception.code, "tracker_required")

        configured = self.dispatch(
            "application.bunker.configuration.replace",
            {
                "bunker_id": created["id"],
                "project_id": project_id,
                "kind": "tracker-source",
                "expected_revision": "",
                "content": {"provider": "github", "kind": "project", "owner": "example", "number": 8},
            },
        )
        self.assertEqual(configured["documents"]["tracker-source"]["content"]["owner"], "example")
        self.assertEqual(
            bunkerdb.get_bunker(created["id"])["projects"][0]["tracker"]["number"], 8
        )

        with self.assertRaises(commands.CommandRejected) as no_token:
            self.dispatch("application.project.sync", {"bunker_id": created["id"], "project_id": project_id})
        self.assertEqual(no_token.exception.code, "tracker_token_required")
        self.assertIn("Settings", no_token.exception.message)

        saved = self.dispatch("credentials.update", {"provider": "github", "token": "ghp_test"})
        self.assertTrue(saved["credentials"]["github"])
        self.assertTrue(application.state()["trackers"]["github"])
        self.assertNotIn("ghp_test", json.dumps(application.state()))

        # Issue #12 carries a deliberate "ECS-2" title key while issue #2 has
        # none. The keyed title wins ECS-2; the bare #2 gets the next free
        # number and keeps it on later syncs through its source identity.
        stale = github_entry(12)
        stale["issue"]["title"] = "ECS-2 Renamed later"
        with mock.patch.object(
            github, "collect_entries",
            return_value=[github_entry(4), github_entry(5, "In Progress"), github_entry(9, keyed=True), github_entry(2), stale],
        ) as collect:
            result = self.dispatch(
                "application.project.sync", {"bunker_id": created["id"], "project_id": project_id}
            )
        source, token = collect.call_args.args
        self.assertEqual((source["owner"], source["number"], token), ("example", 8, "ghp_test"))
        self.assertEqual((result["sync"]["created"], result["sync"]["updated"], result["sync"]["unchanged"]), (5, 0, 0))
        self.assertEqual(result["sync"]["source"], "GitHub Project #8 · example")
        self.assertEqual(result["bunker"]["projects"][0]["ticket_count"], 5)

        listed = self.dispatch(
            "application.ticket.list", {"bunker_id": created["id"], "project_id": project_id}
        )
        self.assertEqual({item["id"] for item in listed["tickets"]}, {"ECS-2", "ECS-4", "ECS-5", "ECS-9", "ECS-10"})
        by_id = {item["id"]: item for item in listed["tickets"]}
        self.assertEqual(by_id["ECS-4"]["title"], "Mock ticket 4")
        self.assertEqual(by_id["ECS-9"]["title"], "Mock ticket 9")
        self.assertEqual(by_id["ECS-2"]["title"], "Renamed later")
        self.assertEqual(by_id["ECS-10"]["title"], "Mock ticket 2")
        self.assertEqual(by_id["ECS-5"]["status"], "in-progress")
        self.assertEqual(by_id["ECS-4"]["assignee"], "Ada")
        detail = self.dispatch(
            "application.ticket.get", {"bunker_id": created["id"], "ticket_id": "ECS-4"}
        )
        self.assertEqual(detail["ticket"]["source_kind"], "github")
        self.assertEqual(detail["ticket"]["source_reference"], "https://github.com/example/voxel/issues/4")

        with mock.patch.object(
            github, "collect_entries",
            return_value=[github_entry(2), github_entry(4), github_entry(5, "Done"), github_entry(9, keyed=True), stale],
        ):
            second = self.dispatch(
                "application.project.sync", {"bunker_id": created["id"], "project_id": project_id}
            )["sync"]
        self.assertEqual((second["created"], second["updated"], second["unchanged"]), (0, 1, 4))
        after = self.dispatch(
            "application.ticket.list",
            {"bunker_id": created["id"], "project_id": project_id, "status": "all"},
        )
        self.assertEqual({item["id"] for item in after["tickets"]}, {"ECS-2", "ECS-4", "ECS-5", "ECS-9", "ECS-10"})
        self.assertEqual({item["id"]: item["status"] for item in after["tickets"]}["ECS-5"], "done")
        self.assertEqual({item["id"]: item["title"] for item in after["tickets"]}["ECS-10"], "Mock ticket 2")

        with mock.patch.object(github, "collect_entries", side_effect=github.GitHubError("GitHub returned HTTP 401: Bad credentials")):
            with self.assertRaises(commands.CommandRejected) as failed:
                self.dispatch("application.project.sync", {"bunker_id": created["id"], "project_id": project_id})
        self.assertEqual(failed.exception.code, "tracker_unreachable")
        self.assertIn("Settings → Trackers", failed.exception.message)


def discovery() -> dict[str, Any]:
    return {
        "provider": "github",
        "account": {"login": "example", "name": "Example Person"},
        "boards": [
            {"kind": "project", "owner": "example", "number": 8, "repository": "", "title": "Voxel ECS", "url": "https://github.com/users/example/projects/8"},
            {"kind": "issues", "owner": "example", "number": 0, "repository": "voxel", "title": "example/voxel", "url": "https://github.com/example/voxel", "open_issues": 3, "private": False},
        ],
        "notes": [],
    }


class TrackerConnectionTests(TrackerSyncTests):
    def test_connecting_a_token_verifies_it_and_records_the_account(self) -> None:
        created = self.dispatch(
            "application.bunker.create",
            {"name": "Engineering", "project_key": "ECS", "project_name": "Entity Component System"},
        )["bunker"]
        self.dispatch(
            "application.profile.update",
            {"name": "Tim", "email": "tim@example.test", "branch_user": "tim"},
        )

        with mock.patch.object(github, "discover", side_effect=github.GitHubError("GitHub returned HTTP 401: Bad credentials")):
            with self.assertRaises(commands.CommandRejected) as rejected:
                self.dispatch("application.tracker.connect", {"bunker_id": created["id"], "provider": "github", "token": "bad"})
        self.assertEqual(rejected.exception.code, "tracker_token_rejected")
        self.assertFalse(application.state()["trackers"]["github"])

        with mock.patch.object(github, "discover", return_value=discovery()) as discover:
            result = self.dispatch(
                "application.tracker.connect",
                {"bunker_id": created["id"], "provider": "github", "token": "ghp_good"},
            )
        discover.assert_called_once_with("ghp_good")
        self.assertEqual(result["connection"]["account"], "example")
        self.assertTrue(result["connection"]["recorded"])
        self.assertTrue(result["trackers"]["github"])
        self.assertEqual(result["bunker"]["connections"]["github"]["account"], "example")
        self.assertEqual(result["bunker"]["connections"]["github"]["connected_by"], "Tim")
        self.assertNotIn("ghp_good", json.dumps(result))
        self.assertEqual(
            bunkerdb.get_configuration(created["id"])["documents"]["connections"]["revision"], "local:1"
        )

        with mock.patch.object(github, "discover", return_value=discovery()):
            boards = self.dispatch("application.tracker.boards", {"provider": "github"})
        self.assertEqual([board["title"] for board in boards["boards"]], ["Voxel ECS", "example/voxel"])

        project_id = created["projects"][0]["id"]
        chosen = boards["boards"][0]
        linked = self.dispatch(
            "application.bunker.configuration.replace",
            {
                "bunker_id": created["id"],
                "project_id": project_id,
                "kind": "tracker-source",
                "expected_revision": "",
                "content": {**chosen, "provider": "github", "include_comments": True},
            },
        )
        self.assertEqual(linked["documents"]["tracker-source"]["content"]["title"], "Voxel ECS")
        self.assertEqual(
            bunkerdb.tracker_summary(linked["documents"]["tracker-source"]["content"]), "GitHub · Voxel ECS"
        )

    def test_linking_a_board_scans_it_and_builds_the_project_model(self) -> None:
        created = self.dispatch(
            "application.bunker.create",
            {"name": "Engineering", "project_key": "ECS", "project_name": "Entity Component System"},
        )["bunker"]
        project_id = created["projects"][0]["id"]
        board = discovery()["boards"][0]
        inspection = {
            "kind": "project", "title": "Voxel ECS", "url": board["url"], "items": 12,
            "fields": [
                {"name": "Status", "type": "SINGLE_SELECT", "options": ["Todo", "In Progress", "Done"]},
                {"name": "Priority", "type": "SINGLE_SELECT", "options": ["P0", "P1"]},
                {"name": "Sprint", "type": "ITERATION", "options": []},
            ],
            "labels": [],
        }

        with self.assertRaises(commands.CommandRejected) as missing:
            self.dispatch("application.project.link", {"bunker_id": created["id"], "project_id": project_id, "board": board})
        self.assertEqual(missing.exception.code, "tracker_token_required")

        self.dispatch("credentials.update", {"provider": "github", "token": "ghp_test"})
        with mock.patch.object(github, "inspect_board", return_value=inspection) as inspect:
            result = self.dispatch(
                "application.project.link",
                {"bunker_id": created["id"], "project_id": project_id, "board": board},
            )
        inspect.assert_called_once()
        documents = result["configuration"]["documents"]
        self.assertEqual(documents["tracker-source"]["content"]["title"], "Voxel ECS")
        by_key = {field["key"]: field for field in documents["ticket-fields"]["content"]["fields"]}
        self.assertEqual(by_key["status"]["values"], ["todo", "in-progress", "done"])
        self.assertEqual(by_key["priority"]["values"], ["P0", "P1"])
        self.assertEqual(by_key["sprint"]["label"], "Sprint")
        mapping = documents["integrations"]["content"]["providers"]["github"]["fields"]
        self.assertEqual(mapping["sprint"], "project.fields.Sprint.title")
        self.assertEqual(result["link"]["items"], 12)
        self.assertIn("Status: Todo, In Progress, Done", result["link"]["summary"])
        self.assertEqual(result["bunker"]["projects"][0]["tracker"]["title"], "Voxel ECS")

        entry = github_entry(4, "In Progress")
        entry["project"]["fields"]["Priority"] = "P1"
        entry["project"]["fields"]["Sprint"] = {"title": "Sprint 3", "start_date": "2026-09-01", "duration": 14}
        with mock.patch.object(github, "collect_entries", return_value=[entry]):
            self.dispatch("application.project.sync", {"bunker_id": created["id"], "project_id": project_id})
        detail = self.dispatch("application.ticket.get", {"bunker_id": created["id"], "ticket_id": "ECS-4"})
        self.assertEqual(detail["ticket"]["priority"], "P1")
        self.assertEqual(detail["ticket"]["sprint"], "Sprint 3")
        self.assertEqual(detail["ticket"]["status"], "in-progress")

    def test_boards_need_a_token_first(self) -> None:
        with self.assertRaises(commands.CommandRejected) as missing:
            self.dispatch("application.tracker.boards", {"provider": "github"})
        self.assertEqual(missing.exception.code, "tracker_token_required")

