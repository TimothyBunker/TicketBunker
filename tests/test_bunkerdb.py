from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import bunkerdb, remote


class BunkerDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.environment = mock.patch.dict(
            os.environ, {"TB_DATA_HOME": str(self.root / "app-data")}
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def test_local_bunker_owns_one_database_and_recursive_projects(self) -> None:
        created = bunkerdb.create_bunker(
            "Engineering", project_key="SYS", project_name="Systems"
        )
        root_project = created["projects"][0]
        updated = bunkerdb.create_project(
            created["id"],
            key="ECS",
            name="Entity Component System",
            parent_id=root_project["id"],
        )

        self.assertEqual(updated["name"], "Engineering")
        self.assertEqual(updated["project_count"], 2)
        self.assertEqual(updated["projects"][0]["children"][0]["key"], "ECS")
        self.assertEqual(Path(updated["database"]), bunkerdb.database_path(created["id"]))
        self.assertEqual(len(bunkerdb.list_bunkers()), 1)

    def test_repository_attachment_keeps_the_local_path_out_of_git(self) -> None:
        source = self.root / "coal"
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "remote",
                "add",
                "origin",
                "git@github.com:Example/coal.git",
            ],
            check=True,
        )
        created = bunkerdb.create_bunker("ECS", project_key="ECS")
        project_id = created["projects"][0]["id"]

        attached = bunkerdb.attach_repository(
            created["id"], project_id=project_id, local_path=str(source)
        )

        repository = attached["repositories"][0]
        self.assertEqual(repository["canonical_remote"], "github.com/Example/coal")
        self.assertEqual(repository["local_path"], str(source.resolve()))
        self.assertFalse((source / ".ticketbunker.json").exists())
        self.assertFalse((source / ".tb").exists())

        child = bunkerdb.create_project(
            created["id"], key="CHILD", name="Child", parent_id=project_id
        )
        child_id = child["projects"][0]["children"][0]["id"]
        with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "already attached"):
            bunkerdb.attach_repository(
                created["id"], project_id=child_id, local_path=str(source)
            )

    def test_remote_relationship_names_a_service_not_a_database(self) -> None:
        created = bunkerdb.create_bunker("Engineering", project_key="ENG")

        updated = bunkerdb.configure_remote(
            created["id"],
            server_url="https://tickets.example.test",
            remote_bunker_id="engineering",
        )

        self.assertEqual(updated["mode"], "remote")
        self.assertEqual(updated["remote"]["server_url"], "https://tickets.example.test")
        self.assertEqual(updated["remote"]["state"], "pending-auth")

    def test_same_remote_bunker_cannot_be_connected_twice(self) -> None:
        first = bunkerdb.create_bunker("First", project_key="ONE")
        second = bunkerdb.create_bunker("Second", project_key="TWO")
        bunkerdb.configure_remote(
            first["id"],
            server_url="https://tickets.example.test",
            remote_bunker_id="engineering",
        )

        with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "already connected"):
            bunkerdb.configure_remote(
                second["id"],
                server_url="https://tickets.example.test",
                remote_bunker_id="engineering",
            )

        updated = bunkerdb.configure_remote(
            first["id"],
            server_url="https://tickets.example.test",
            remote_bunker_id="engineering",
            state="offline",
        )
        self.assertEqual(updated["remote"]["state"], "offline")

    def test_authenticated_enrollment_precedes_local_replica_creation(self) -> None:
        invitation = remote.JoinInvitation.parse(
            "ticketbunker://join?server=https%3A%2F%2Ftickets.example.test"
            "&bunker=engineering&invite=secret-value"
        )
        snapshot = remote.RemoteSnapshot(
            bunker={"id": "engineering", "name": "Engineering"},
            projects=(
                {"id": "systems", "key": "SYS", "name": "Systems"},
                {
                    "id": "ecs",
                    "parent_id": "systems",
                    "key": "ECS",
                    "name": "Entity Component System",
                },
            ),
            repository_bindings=(
                {
                    "id": "coal",
                    "project_id": "ecs",
                    "name": "Coal",
                    "canonical_remote": "github.com/Example/coal",
                },
            ),
            config_documents=(
                {
                    "scope_type": "bunker",
                    "scope_id": "engineering",
                    "kind": "fields",
                    "revision": "42",
                    "content": {"fields": ["title", "status"]},
                },
            ),
            tickets=(
                {
                    "id": "ECS-4",
                    "project_id": "ecs",
                    "payload": {"id": "ECS-4", "title": "Position System"},
                },
            ),
            reports=(
                {
                    "ticket_id": "ECS-4",
                    "scope": "combined",
                    "content": "# Combined\n",
                },
            ),
            members=(
                {
                    "id": "tim",
                    "display_name": "Tim",
                    "role": "member",
                    "capabilities": ["tickets:read"],
                },
            ),
            current_member_id="tim",
            capabilities=("tickets:read",),
            revision="42",
            cursor="cursor-1",
        )

        created = bunkerdb.create_replica(
            invitation,
            remote.Enrollment(snapshot=snapshot, credential_reference="vault:item-1"),
        )

        self.assertEqual(invitation.public_dict(), {
            "server": "https://tickets.example.test",
            "bunker_id": "engineering",
        })
        self.assertEqual(created["remote"]["config_revision"], "42")
        self.assertEqual(created["projects"][0]["children"][0]["key"], "ECS")
        self.assertEqual(created["repositories"][0]["local_path"], None)
        self.assertEqual(created["ticket_count"], 1)
        self.assertEqual(created["membership"]["display_name"], "Tim")
        self.assertEqual(created["remote"]["capabilities"], ["tickets:read"])
        self.assertNotIn("secret-value", json.dumps(created))

    def test_companion_import_is_read_only_and_copies_tickets_and_reports(self) -> None:
        source = self.root / "legacy"
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        locator = {
            "schema_version": 1,
            "project": "ECS",
            "bunker_repository": "git@example.test:team/ecs-ticketbunker.git",
            "base_branch": "main",
        }
        (source / ".ticketbunker.json").write_text(json.dumps(locator))
        config_root = source / ".tb" / "config" / "projects" / "ECS"
        config_root.mkdir(parents=True)
        (config_root / "project.json").write_text(
            json.dumps({"key": "ECS", "name": "Entity Component System"})
        )
        (config_root / "composition.json").write_text(
            json.dumps({"field_sets": [{"id": "ticket.standard", "version": 1}]})
        )
        ticket = source / ".tb" / "data" / "projects" / "ECS" / "tickets" / "ECS-4"
        (ticket / "reports" / "branches").mkdir(parents=True)
        (ticket / "ticket.json").write_text(
            json.dumps({"id": "ECS-4", "title": "Position System"})
        )
        (ticket / "reports" / "master.md").write_text("# Combined\n")
        (ticket / "reports" / "branches" / "tim__ECS-4.md").write_text("# Branch\n")
        before = (source / ".ticketbunker.json").read_bytes()

        result = bunkerdb.import_companion(str(source))

        self.assertEqual(result["migration"]["tickets"], ["ECS-4"])
        self.assertEqual(result["migration"]["report_count"], 2)
        self.assertEqual(len(result["migration"]["source_digest"]), 64)
        self.assertTrue(result["migration"]["source_unchanged"])
        self.assertEqual((source / ".ticketbunker.json").read_bytes(), before)
        self.assertTrue((ticket / "ticket.json").exists())
        self.assertIn(str(source.resolve()), bunkerdb.imported_companion_sources())
        with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "already been imported"):
            bunkerdb.import_companion(str(source))


class RemoteContractTests(unittest.TestCase):
    def test_server_origin_rejects_credentials_and_paths(self) -> None:
        with self.assertRaisesRegex(remote.RemoteContractError, "without credentials"):
            remote.normalize_server_url("https://user:secret@example.test")
        with self.assertRaisesRegex(remote.RemoteContractError, "origin only"):
            remote.normalize_server_url("https://example.test/database")

    def test_invitation_requires_ticketbunker_join_scheme(self) -> None:
        with self.assertRaisesRegex(remote.RemoteContractError, "ticketbunker://join"):
            remote.JoinInvitation.parse("https://example.test/join")

    def test_invitation_rejects_extra_or_repeated_parameters(self) -> None:
        prefix = (
            "ticketbunker://join?server=https%3A%2F%2Ftickets.example.test"
            "&bunker=engineering&invite=secret"
        )
        with self.assertRaisesRegex(remote.RemoteContractError, "not valid"):
            remote.JoinInvitation.parse(f"{prefix}&admin=true")
        with self.assertRaisesRegex(remote.RemoteContractError, "not valid"):
            remote.JoinInvitation.parse(f"{prefix}&invite=second")

    def test_http_enrollment_requires_native_authentication_first(self) -> None:
        invitation = remote.JoinInvitation.parse(
            "ticketbunker://join?server=https%3A%2F%2Ftickets.example.test"
            "&bunker=engineering&invite=secret"
        )
        adapter = remote.HTTPRemoteAdapter(invitation.server)

        with self.assertRaisesRegex(remote.RemoteContractError, "Sign in"):
            adapter.enroll(invitation)


if __name__ == "__main__":
    unittest.main()
