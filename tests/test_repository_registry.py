"""Shared administration and device-local checkout selection."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import application, bunkerdb, commands, remote
from ticketbunker.integrations import github
import test_service
import test_application as application_tests
from test_application import github_entry


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.environment = mock.patch.dict(os.environ, {"TB_DATA_HOME": self.tmp.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.context = commands.CommandContext(Path(self.tmp.name))
        self.remote = "github.com/example/navigation"
        self.rows = [{"name": "Navigation", "canonical_remote": self.remote, "default_branch": "main"}]
        self.bunker = self.call("application.bunker.create", name="Dummy", project_key="NAV", repositories=self.rows)["bunker"]
        self.bid, self.pid = self.bunker["id"], self.bunker["projects"][0]["id"]

    def call(self, action, **payload):
        return commands.dispatch(action, payload, self.context)

    def select(self, rows=None):
        cfg = bunkerdb.get_configuration(self.bid, project_id=self.pid)
        return self.call("application.project.repositories.select", bunker_id=self.bid, project_id=self.pid,
            expected_revision=cfg["repositories"]["revision"], repositories=self.rows if rows is None else rows)

    def test_creation_records_shared_registry_without_checkout_paths(self):
        self.assertEqual(self.bunker["repository_registry"]["items"][0]["canonical_remote"], self.remote)
        self.assertEqual(self.bunker["repositories"], [])
        self.assertIsNone(self.bunker["repository_registry"]["items"][0]["local_path"])

    def test_projects_reference_registry_and_keep_distinct_binding_ids(self):
        first = self.select()["bunker"]["repositories"][0]
        second = self.call("application.bunker.project.create", bunker_id=self.bid, key="TOOLS", name="Tools", repository_remotes=[self.remote])["bunker"]
        self.assertEqual(len(second["repository_registry"]["items"]), 1)
        self.assertEqual(len(second["repositories"]), 2)
        self.assertEqual(len({row["id"] for row in second["repositories"]}), 2)
        self.assertIn(first["id"], {row["id"] for row in second["repositories"]})

    def test_unapproved_remote_and_wrong_scope_are_rejected(self):
        with self.assertRaises(commands.CommandRejected):
            self.select([{**self.rows[0], "canonical_remote": "github.com/example/other"}])
        with self.assertRaises(commands.CommandRejected):
            self.call("application.project.repositories.select", bunker_id=self.bid, repositories=[])
        with self.assertRaises(commands.CommandRejected):
            self.call("application.project.link", bunker_id=self.bid, board={"kind": "issues"})
        with self.assertRaises(commands.CommandRejected):
            self.call("application.bunker.configuration.replace", bunker_id=self.bid, project_id=self.pid,
                kind="repository-registry", expected_revision="", content={"items": self.rows})
        self.assertEqual(bunkerdb.get_bunker(self.bid)["repositories"], [])

    def test_invalid_project_selection_does_not_create_a_project(self):
        with self.assertRaises(commands.CommandRejected):
            self.call("application.bunker.project.create", bunker_id=self.bid, key="BAD", name="Bad", repository_remotes=["github.com/example/other"])
        self.assertEqual(bunkerdb.get_bunker(self.bid)["project_count"], 1)

    def test_first_project_selection_and_canonical_remote_forms(self):
        created = self.call("application.bunker.create", name="Other", project_key="NAV",
            repositories=[{**self.rows[0], "canonical_remote": "git@github.com:example/navigation.git"}],
            first_repository="https://github.com/example/navigation.git")["bunker"]
        self.assertEqual(created["repositories"][0]["canonical_remote"], self.remote)
        self.assertIsNone(created["repositories"][0]["local_path"])
        before = len(bunkerdb.list_bunkers())
        with self.assertRaises(commands.CommandRejected):
            self.call("application.bunker.create", name="Invalid", project_key="BAD",
                repositories=self.rows, first_repository="github.com/example/other")
        self.assertEqual(len(bunkerdb.list_bunkers()), before)
        with self.assertRaises(commands.CommandRejected):
            self.call("application.bunker.create", name="Invalid name", project_key="BAD",
                repositories=[{**self.rows[0], "name": "x" * 121}], first_repository=self.remote)
        self.assertEqual(len(bunkerdb.list_bunkers()), before)

    def test_local_checkout_is_validated_and_never_in_the_registry(self):
        from test_bunkerruntime import git
        checkout = Path(self.tmp.name) / "checkout"
        checkout.mkdir()
        git(checkout, "init")
        git(checkout, "remote", "add", "origin", "git@github.com:example/navigation.git")
        binding = self.select()["bunker"]["repositories"][0]
        with mock.patch.object(bunkerdb, "_fetch_repository"):
            attached = self.call("application.bunker.repository.attach", bunker_id=self.bid,
                project_id=self.pid, binding_id=binding["id"], path=str(checkout))["bunker"]
        self.assertEqual(attached["repositories"][0]["local_path"], str(checkout))
        publication = bunkerdb.local_publication(self.bid)
        self.assertNotIn(str(checkout), str(publication))
        git(checkout, "remote", "set-url", "origin", "git@github.com:example/unapproved.git")
        with self.assertRaises(commands.CommandRejected):
            self.call("application.bunker.repository.attach", bunker_id=self.bid,
                project_id=self.pid, binding_id=binding["id"], path=str(checkout))

    def test_in_use_remote_cannot_be_removed_even_by_stale_local_proposal(self):
        cfg = bunkerdb.get_configuration(self.bid)
        proposal = bunkerdb.propose_configuration(self.bid, project_id=None, kind="repository-registry",
            expected_revision=cfg["repositories"]["revision"], content={"items": []})
        self.select()
        with self.assertRaises(bunkerdb.BunkerDatabaseError):
            bunkerdb.apply_configuration_change(self.bid, proposal)
        self.assertEqual(len(bunkerdb.get_configuration(self.bid)["repositories"]["items"]), 1)

    def test_link_automatically_syncs_and_preserves_link_on_sync_failure(self):
        board = {"kind": "issues", "owner": "example", "repository": "navigation"}
        with mock.patch.object(application.projectconfig, "credential_value", return_value="dummy"), \
             mock.patch.object(github, "inspect_board", return_value={"fields": [], "labels": [], "items": 0}), \
             mock.patch.object(github, "collect_entries", return_value=[github_entry(4)]) as collect:
            value = self.call("application.project.link", bunker_id=self.bid, project_id=self.pid, board=board)
            collect.assert_called_once()
            self.assertEqual(value["sync"]["status"], "complete")
            self.assertEqual(value["sync"]["created"], 1)
            self.assertEqual(value["bunker"]["ticket_count"], 1)
            collect.side_effect = github.GitHubError("offline")
            failed = self.call("application.project.link", bunker_id=self.bid, project_id=self.pid, board=board)
            self.assertEqual(failed["sync"]["status"], "failed")
            self.assertIn("offline", failed["sync"]["message"])
            self.assertEqual(len(failed["bunker"]["projects"][0]["trackers"]), 1)
            self.assertEqual(failed["bunker"]["ticket_count"], 1)
            collect.side_effect = None
            retried = self.call("application.project.sync", bunker_id=self.bid, project_id=self.pid)
            self.assertEqual(retried["sync"]["unchanged"], 1)


class RegistryReplicaTests(unittest.TestCase):
    def test_owner_setup_reaches_a_second_device_without_granting_admin(self):
        fixture = application_tests.SharedBunkerTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.as_user("admin")
        identity = "github.com/example/navigation"
        created = fixture.dispatch("application.bunker.create", {
            "name": "Dummy team", "project_key": "NAV",
            "repositories": [{"canonical_remote": identity}], "first_repository": identity,
        })["bunker"]
        bid = created["id"]
        fixture.dispatch("application.bunker.share", {"bunker_id": bid, "server_url": fixture.origin, "setup_token": fixture.setup_token})
        configured = fixture.dispatch("application.bunker.project.create", {
            "bunker_id": bid, "key": "TOOLS", "name": "Tools", "repository_remotes": [identity],
        })["bunker"]
        link = fixture.dispatch("application.bunker.invitation.create", {"bunker_id": bid})["invitation"]["link"]
        fixture.as_user("member")
        joined = fixture.dispatch("application.bunker.join", {"invitation": link})["bunker"]
        self.assertEqual(joined["repository_registry"]["items"][0]["canonical_remote"], identity)
        self.assertEqual({row["id"] for row in joined["repositories"]}, {row["id"] for row in configured["repositories"]})
        self.assertEqual(len(joined["repositories"]), 2)
        self.assertTrue(all(row["local_path"] is None for row in joined["repositories"]))
        self.assertFalse(bunkerdb.get_configuration(joined["id"])["permission"]["can_write"])
        with self.assertRaises(commands.CommandRejected):
            fixture.dispatch("application.bunker.project.create", {"bunker_id": joined["id"], "key": "BAD", "name": "Forbidden"})


class ServiceAdministrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_service.ReferenceServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.owner, _ = self.fixture.owner()
        self.snapshot = self.owner.publish(test_service.publication())
        invitation = self.owner.create_invitation(self.snapshot.bunker["id"])
        joined = self.fixture.adapter().enroll(self.fixture.invitation_link(self.snapshot.bunker["id"], invitation["token"]), {"display_name": "Member"})
        self.member = self.fixture.adapter(joined.issued_credential)

    def test_members_cannot_create_shared_bunkers(self):
        with self.assertRaisesRegex(remote.RemoteContractError, "service administrator"):
            self.member.publish(test_service.publication("other"))
        self.assertEqual(self.owner.publish(test_service.publication("other" )).bunker["id"], "other")
        # Even a Bunker owner invitation confers no service-wide admin role.
        bid = self.snapshot.bunker["id"]
        invitation = self.owner.create_invitation(bid, role="owner")
        invited = self.fixture.adapter().enroll(self.fixture.invitation_link(bid, invitation["token"]), {"display_name": "Other owner"})
        with self.assertRaisesRegex(remote.RemoteContractError, "service administrator"):
            self.fixture.adapter(invited.issued_credential).publish(test_service.publication("owner-not-admin"))

    def test_registry_validation_and_authority_are_enforced_by_server(self):
        bid = self.snapshot.bunker["id"]
        rows = [{"name": "Coal", "canonical_remote": "github.com/example/coal", "default_branch": "main"}]
        change = remote.change_record(key="registry", kind="config-document", record_id=f"bunker:{bid}:repository-registry",
            expected_revision="", revision="1", payload={"scope_type": "bunker", "scope_id": bid, "kind": "repository-registry", "content": {"items": rows}})
        with self.assertRaises(remote.RemoteContractError):
            self.member.push(bid, "", (change,))
        self.owner.push(bid, "", (change,))
        bindings = [{**self.snapshot.repository_bindings[0], "canonical_remote": "github.com/example/unapproved"}]
        forged = remote.change_record(key="forged", kind="repository-bindings", record_id="ecs",
            expected_revision=remote.repository_list_revision(list(self.snapshot.repository_bindings)),
            revision=remote.repository_list_revision(bindings), payload=bindings)
        with self.assertRaises(remote.RemoteContractError):
            self.owner.push(bid, "", (forged,))
        self.assertEqual(self.member.pull(bid, "").repository_bindings[0]["canonical_remote"], rows[0]["canonical_remote"])

    def test_member_cannot_create_project_with_a_forged_command(self):
        change = remote.change_record(key="project", kind="project", record_id="forged",
            expected_revision="", revision="1", payload={"id": "forged", "key": "BAD", "name": "Forbidden", "parent_id": ""})
        with self.assertRaises(remote.RemoteContractError):
            self.member.push(self.snapshot.bunker["id"], "", (change,))
        self.assertEqual(len(self.owner.pull(self.snapshot.bunker["id"], "").projects), 2)

    def test_legacy_service_promotes_only_setup_user_once(self):
        # Model an old service schema, including users created in the same second.
        store = self.fixture.store
        connection = store._connect()
        try:
            with connection:
                connection.execute("DROP TABLE service_admins")
                connection.execute("UPDATE users SET created_at='2026-01-01T00:00:00+00:00'")
            store.initialize()
            administrators = connection.execute("SELECT user_id FROM service_admins").fetchall()
            original_user = connection.execute("SELECT id FROM users ORDER BY rowid LIMIT 1").fetchone()
            self.assertEqual([row["user_id"] for row in administrators], [original_user["id"]])
            with connection:
                connection.execute("DELETE FROM service_admins")
            store.initialize()
            self.assertEqual(connection.execute("SELECT count(*) FROM service_admins").fetchone()[0], 0)
            with self.assertRaises(remote.RemoteContractError):
                self.member.publish(test_service.publication("not-promoted"))
        finally:
            connection.close()

    def test_initial_sharing_rejects_registry_that_excludes_a_project(self):
        value = test_service.publication("bad-registry")
        value["config_documents"].append({"scope_type": "bunker", "scope_id": "bad-registry", "kind": "repository-registry",
            "revision": "1", "content": {"items": []}})
        with self.assertRaises(remote.RemoteContractError):
            self.owner.publish(value)
