from __future__ import annotations

import json
import os
import sqlite3
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

    def test_database_context_releases_its_file_handle(self) -> None:
        path = self.root / "managed.sqlite3"
        connection = bunkerdb._connect(path)
        with connection:
            connection.execute("CREATE TABLE sample(value TEXT)")

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_version_three_migrates_project_scoped_repositories_and_reports(self) -> None:
        created = bunkerdb.create_bunker("Engineering", project_key="ENG")
        path = bunkerdb.database_path(created["id"])
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            PRAGMA foreign_keys = OFF;
            DROP INDEX repositories_project_remote_idx;
            CREATE UNIQUE INDEX repositories_remote_idx
                ON repository_bindings(bunker_id, canonical_remote)
                WHERE canonical_remote <> '';
            ALTER TABLE local_repositories RENAME TO local_repositories_v3;
            CREATE TABLE local_repositories (
                binding_id TEXT PRIMARY KEY REFERENCES repository_bindings(id) ON DELETE CASCADE,
                local_path TEXT NOT NULL UNIQUE,
                attached_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            INSERT INTO local_repositories SELECT * FROM local_repositories_v3;
            DROP TABLE local_repositories_v3;
            ALTER TABLE reports RENAME TO reports_v3;
            CREATE TABLE reports (
                ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                scope TEXT NOT NULL,
                content TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (ticket_id, scope)
            );
            INSERT INTO reports(ticket_id, scope, content, updated_at)
                SELECT ticket_id, scope, content, updated_at FROM reports_v3;
            DROP TABLE reports_v3;
            """
        )
        connection.commit()
        connection.close()

        bunkerdb.get_bunker(created["id"])

        migrated = sqlite3.connect(path)
        local_sql = migrated.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'local_repositories'"
        ).fetchone()[0]
        report_sql = migrated.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'reports'"
        ).fetchone()[0]
        indexes = {
            row[0]
            for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        migrated.close()

        self.assertNotIn("UNIQUE", local_sql.upper())
        self.assertIn("repository_id", report_sql)
        self.assertIn("repositories_project_remote_idx", indexes)
        self.assertNotIn("repositories_remote_idx", indexes)

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

    def test_project_configuration_is_a_pinned_copy_of_current_defaults(self) -> None:
        created = bunkerdb.create_bunker("Engineering", project_key="SYS")
        root_project = created["projects"][0]
        root_config = bunkerdb.get_configuration(
            created["id"], project_id=root_project["id"]
        )
        global_config = bunkerdb.get_configuration(created["id"])
        fields_document = global_config["documents"]["ticket-fields"]
        fields = list(fields_document["content"]["fields"])
        fields.append(
            {
                "key": "charging",
                "label": "Charging",
                "type": "string",
                "required": False,
            }
        )

        published = bunkerdb.replace_configuration(
            created["id"],
            project_id=None,
            kind="ticket-fields",
            expected_revision=fields_document["revision"],
            content={"fields": fields},
        )
        updated = bunkerdb.create_project(created["id"], key="ECS", name="ECS")
        child_id = next(value["id"] for value in updated["projects"] if value["key"] == "ECS")
        child_config = bunkerdb.get_configuration(
            created["id"], project_id=child_id
        )

        self.assertEqual(published["documents"]["ticket-fields"]["revision"], "local:2")
        self.assertNotIn(
            "charging",
            [value["key"] for value in root_config["documents"]["ticket-fields"]["content"]["fields"]],
        )
        self.assertIn(
            "charging",
            [value["key"] for value in child_config["documents"]["ticket-fields"]["content"]["fields"]],
        )
        self.assertEqual(child_config["composition"], "pinned")

    def test_configuration_replacement_is_revision_checked_and_matrix_validated(self) -> None:
        created = bunkerdb.create_bunker("Engineering", project_key="SYS")
        project_id = created["projects"][0]["id"]
        state = bunkerdb.get_configuration(created["id"], project_id=project_id)
        integrations = state["documents"]["integrations"]
        content = json.loads(json.dumps(integrations["content"]))
        content["providers"]["linear"] = {
            "label": "Linear",
            "template": "custom",
            "fields": {"title": "title", "status": "state.name"},
        }

        updated = bunkerdb.replace_configuration(
            created["id"],
            project_id=project_id,
            kind="integrations",
            expected_revision=integrations["revision"],
            content=content,
        )

        self.assertEqual(
            updated["documents"]["integrations"]["content"]["providers"]["linear"]["fields"]["status"],
            "state.name",
        )
        with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "changed"):
            bunkerdb.replace_configuration(
                created["id"],
                project_id=project_id,
                kind="integrations",
                expected_revision=integrations["revision"],
                content=content,
            )

        field_document = updated["documents"]["ticket-fields"]
        remaining = [
            value
            for value in field_document["content"]["fields"]
            if value["key"] != "title"
        ]
        with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "Remove integration mappings"):
            bunkerdb.replace_configuration(
                created["id"],
                project_id=project_id,
                kind="ticket-fields",
                expected_revision=field_document["revision"],
                content={"fields": remaining},
            )

    def test_remote_configuration_snapshot_cannot_authorize_its_own_write(self) -> None:
        created = bunkerdb.create_bunker("Engineering", project_key="SYS")
        bunkerdb.configure_remote(
            created["id"],
            server_url="https://tickets.example.test",
            remote_bunker_id="engineering",
        )
        state = bunkerdb.get_configuration(created["id"])

        self.assertFalse(state["permission"]["can_write"])
        document = state["documents"]["ticket-fields"]
        with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "controlled by"):
            bunkerdb.replace_configuration(
                created["id"],
                project_id=None,
                kind="ticket-fields",
                expected_revision=document["revision"],
                content=document["content"],
            )
        project_id = created["projects"][0]["id"]
        project_configuration = bunkerdb.get_configuration(
            created["id"], project_id=project_id
        )
        with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "controlled by"):
            bunkerdb.replace_repository_bindings(
                created["id"],
                project_id=project_id,
                expected_revision=project_configuration["repositories"]["revision"],
                repositories=[],
            )

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
        configuration = bunkerdb.get_configuration(
            created["id"], project_id=project_id
        )
        configured = bunkerdb.replace_repository_bindings(
            created["id"],
            project_id=project_id,
            expected_revision=configuration["repositories"]["revision"],
            repositories=[
                {
                    "name": "Coal",
                    "canonical_remote": "git@github.com:Example/coal.git",
                    "default_branch": "main",
                }
            ],
        )
        binding_id = configured["repositories"]["items"][0]["id"]

        with mock.patch.object(bunkerdb, "_fetch_repository") as fetched:
            attached = bunkerdb.attach_repository(
                created["id"],
                project_id=project_id,
                binding_id=binding_id,
                local_path=str(source),
            )

        repository = attached["repositories"][0]
        self.assertEqual(repository["canonical_remote"], "github.com/Example/coal")
        self.assertEqual(repository["local_path"], str(source.resolve()))
        fetched.assert_called_once_with(source.resolve())
        self.assertFalse((source / ".ticketbunker.json").exists())
        self.assertFalse((source / ".tb").exists())

        child = bunkerdb.create_project(
            created["id"], key="CHILD", name="Child", parent_id=project_id
        )
        child_id = child["projects"][0]["children"][0]["id"]
        child_config = bunkerdb.get_configuration(created["id"], project_id=child_id)
        child_config = bunkerdb.replace_repository_bindings(
            created["id"],
            project_id=child_id,
            expected_revision=child_config["repositories"]["revision"],
            repositories=[
                {
                    "name": "Coal monorepo",
                    "canonical_remote": "github.com/Example/coal",
                    "default_branch": "main",
                }
            ],
        )
        with mock.patch.object(bunkerdb, "_fetch_repository"):
            bunkerdb.attach_repository(
                created["id"],
                project_id=child_id,
                binding_id=child_config["repositories"]["items"][0]["id"],
                local_path=str(source),
            )
        self.assertEqual(bunkerdb.get_bunker(created["id"])["repository_count"], 2)

    def test_repository_attachment_rejects_a_remote_outside_project_allowlist(self) -> None:
        source = self.root / "other"
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(
            ["git", "-C", str(source), "remote", "add", "origin", "https://github.com/Example/other.git"],
            check=True,
        )
        created = bunkerdb.create_bunker("ECS", project_key="ECS")
        project_id = created["projects"][0]["id"]

        with mock.patch.object(bunkerdb, "_fetch_repository") as fetched:
            with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "does not allow"):
                bunkerdb.attach_repository(
                    created["id"], project_id=project_id, local_path=str(source)
                )

        fetched.assert_not_called()

    def test_failed_fetch_does_not_connect_the_local_checkout(self) -> None:
        source = self.root / "coal"
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(
            ["git", "-C", str(source), "remote", "add", "origin", "https://github.com/Example/coal.git"],
            check=True,
        )
        created = bunkerdb.create_bunker("ECS", project_key="ECS")
        project_id = created["projects"][0]["id"]
        configuration = bunkerdb.get_configuration(created["id"], project_id=project_id)
        configured = bunkerdb.replace_repository_bindings(
            created["id"],
            project_id=project_id,
            expected_revision=configuration["repositories"]["revision"],
            repositories=[{
                "name": "Coal",
                "canonical_remote": "github.com/Example/coal",
                "default_branch": "main",
            }],
        )

        with mock.patch.object(
            bunkerdb,
            "_fetch_repository",
            side_effect=bunkerdb.BunkerDatabaseError("Network unavailable"),
        ):
            with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "Network unavailable"):
                bunkerdb.attach_repository(
                    created["id"],
                    project_id=project_id,
                    binding_id=configured["repositories"]["items"][0]["id"],
                    local_path=str(source),
                )

        repositories = bunkerdb.get_configuration(
            created["id"], project_id=project_id
        )["repositories"]["items"]
        self.assertIsNone(repositories[0]["local_path"])

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
                    "payload": {
                        "id": "ECS-4",
                        "title": "Position System",
                        "status": "Todo",
                        "assignee": {"display_name": "Tim"},
                        "reporter": "Ada",
                        "labels": ["ECS", "C0"],
                        "comments": [{"author": "Ada", "body": "Ready"}],
                    },
                },
                {
                    "id": "ECS-3",
                    "project_id": "ecs",
                    "payload": {
                        "id": "ECS-3",
                        "title": "Finished setup",
                        "status": "Done",
                        "assignee": "Ada",
                    },
                },
            ),
            reports=(
                {
                    "ticket_id": "ECS-4",
                    "scope": "combined",
                    "content": "# Combined\n",
                },
                {
                    "ticket_id": "ECS-4",
                    "scope": "branch:tim/ECS-4-position",
                    "repository_id": "coal",
                    "content": "# Branch\n",
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
        self.assertEqual(created["ticket_count"], 2)
        self.assertEqual(created["membership"]["display_name"], "Tim")
        self.assertEqual(created["remote"]["capabilities"], ["tickets:read"])
        self.assertNotIn("secret-value", json.dumps(created))

        active = bunkerdb.list_tickets(created["id"], project_id="systems")
        self.assertEqual([item["id"] for item in active["tickets"]], ["ECS-4"])
        self.assertEqual(active["tickets"][0]["assignee"], "Tim")
        self.assertEqual(active["facets"]["labels"], ["C0", "ECS"])
        completed = bunkerdb.list_tickets(
            created["id"], project_id="ecs", status="done", assignee="Ada"
        )
        self.assertEqual([item["id"] for item in completed["tickets"]], ["ECS-3"])
        searched = bunkerdb.list_tickets(created["id"], search="position")
        self.assertEqual(searched["total"], 1)

        detail = bunkerdb.get_ticket(created["id"], "ECS-4")
        self.assertEqual(detail["project"]["key"], "ECS")
        self.assertEqual(detail["ticket"]["labels"], ["ECS", "C0"])
        self.assertEqual(detail["comments"][0]["body"], "Ready")
        self.assertEqual(detail["reports"][0]["scope"], "combined")
        self.assertEqual(detail["reports"][1]["repository_id"], "coal")
        self.assertEqual(detail["branches"][0]["repository_id"], "coal")
        self.assertEqual(detail["branches"][0]["repository_name"], "Coal")

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
        (ticket / "reports" / "master.md").write_text("# Combined\n\nUpdated\n")
        refreshed = bunkerdb.get_ticket(result["bunker"]["id"], "ECS-4")
        self.assertIn("Updated", refreshed["reports"][0]["content"])
        with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "already been imported"):
            bunkerdb.import_companion(str(source))

    def test_embedded_repository_import_needs_no_companion_locator(self) -> None:
        source = self.root / "embedded"
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        ticket = source / ".tb" / "TB-1_bunker"
        (ticket / "reports").mkdir(parents=True)
        (ticket / "ticket.json").write_text(
            json.dumps(
                {
                    "id": "TB-1",
                    "title": "Standalone migration",
                    "status": "in-progress",
                }
            )
        )
        (ticket / "reports" / "master.md").write_text("# Combined\n")

        result = bunkerdb.import_companion(str(source))
        detail = bunkerdb.get_ticket(result["bunker"]["id"], "TB-1")

        self.assertEqual(result["migration"]["tickets"], ["TB-1"])
        self.assertEqual(detail["ticket"]["source_kind"], "legacy-embedded")
        self.assertEqual(detail["project"]["key"], "TB")
        self.assertEqual(detail["reports"][0]["content"], "# Combined\n")


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
