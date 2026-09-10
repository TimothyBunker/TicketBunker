"""Owner/member repository workflows against a disposable HTTP Bunker service."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_service as service_fixture
from ticketbunker import bunkerdb, commands, config, credentials, remote


class WorkspaceMembershipTests(unittest.TestCase):
    def setUp(self):
        self.service = service_fixture.ReferenceServiceTests()
        self.service.setUp()
        self.addCleanup(self.service.tearDown)
        self.root = Path(self.service.temporary.name).resolve()
        self.owner, owner_secret = self.service.owner()
        publication = service_fixture.publication()
        publication["reports"] = []
        publication["config_documents"].append({"scope_type": "bunker", "scope_id": "local-bunker", "kind": "repository-registry", "revision": "registry:1", "content": {"items": [{"name": "Simulation repository", "canonical_remote": "github.com/example/coal", "default_branch": "main"}]}})
        owner_snapshot = self.owner.publish(publication)
        self.remote_id = str(owner_snapshot.bunker["id"])
        invitation = self.owner.create_invitation(self.remote_id)
        self.invitation = self.service.invitation_link(self.remote_id, invitation["token"])
        enrollment = self.service.adapter().enroll(self.invitation, {"display_name": "Simulation Member", "email": "member@example.test"})
        self.member = self.service.adapter(enrollment.issued_credential)
        self.ids = {}
        for participant, snapshot, secret in (("owner", owner_snapshot, owner_secret), ("member", enrollment.snapshot, enrollment.issued_credential)):
            with self.participant(participant):
                reference = credentials.store(self.service.origin, secret)
                replica = bunkerdb.create_replica(self.invitation, remote.Enrollment(snapshot=snapshot, credential_reference=reference))
                self.ids[participant] = replica["id"]
        self.repo = self.root / "simulation source"
        self.repo.mkdir()
        self.git(self.repo, "init", "-b", "main")
        self.git(self.repo, "config", "user.name", "Simulation")
        self.git(self.repo, "config", "user.email", "simulation@example.test")
        (self.repo / "README.md").write_text("workspace membership simulation\n", encoding="utf-8")
        self.git(self.repo, "add", "README.md")
        self.git(self.repo, "commit", "-m", "simulation baseline")
        self.origin = self.root / "simulation-origin.git"
        self.git(self.repo, "clone", "--bare", str(self.repo), str(self.origin))
        self.git(self.repo, "remote", "add", "origin", "https://github.com/example/coal.git")
        self.git(self.repo, "config", f"url.{self.origin.as_posix()}.insteadOf", "https://github.com/example/coal.git")

    def git(self, root, *args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "TB_AGENT_DISABLE": "1"}).stdout.strip()

    @contextmanager
    def participant(self, name):
        area = self.root / name
        area.mkdir(exist_ok=True)
        with patch.dict(os.environ, {"TB_DATA_HOME": str(area / "data"), "TB_CONFIG": str(area / "settings.json"), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "TB_AGENT_DISABLE": "1"}), patch.object(config, "GLOBAL_CONFIG", area / "settings.json"):
            yield

    def command(self, participant, action, **payload):
        with self.participant(participant):
            return commands.dispatch(action, {"bunker_id": self.ids[participant], **payload}, commands.CommandContext(self.repo, transport="test"))

    def configuration(self, participant, project_id="ecs"):
        with self.participant(participant):
            return bunkerdb.get_configuration(self.ids[participant], project_id=project_id)

    def test_member_connects_and_disconnects_local_folder_without_shared_writes(self):
        before = self.owner.pull(self.remote_id, "")
        self.assertFalse(self.configuration("member")["permission"]["can_write"])
        result = self.command("member", "application.bunker.repository.attach", project_id="ecs", path=str(self.repo))
        self.assertEqual(result["bunker"]["repositories"][0]["local_path"], str(self.repo))
        self.assertIsNone(self.configuration("owner")["repositories"]["items"][0]["local_path"])
        self.assertIn("origin/main", self.git(self.repo, "branch", "-r"))
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "main")
        self.command("member", "application.bunker.repository.detach", project_id="ecs", binding_id="coal")
        self.assertIsNone(self.configuration("member")["repositories"]["items"][0]["local_path"])
        after = self.owner.pull(self.remote_id, "")
        self.assertEqual(after.cursor, before.cursor)
        self.assertEqual(after.repository_bindings, before.repository_bindings)
        self.assertNotIn(str(self.repo), json.dumps(after.repository_bindings))

    def test_member_shared_configuration_and_project_creation_are_rejected(self):
        current = self.configuration("member")
        before = self.owner.pull(self.remote_id, "")
        cases = [
            ("application.bunker.configuration.replace", {"project_id": "ecs", "kind": "checks", "expected_revision": "", "content": {"build": "make"}}),
            ("application.bunker.repository.replace", {"project_id": "ecs", "expected_revision": current["repositories"]["revision"], "repositories": []}),
            ("application.bunker.project.create", {"key": "ROGUE", "name": "Unauthorized Project"}),
            ("application.bunker.repository.replace", {"expected_revision": "", "repositories": []}),
        ]
        for action, payload in cases:
            with self.subTest(action=action, scope=payload.get("project_id")):
                with self.assertRaises(commands.CommandRejected):
                    self.command("member", action, **payload)
        after = self.owner.pull(self.remote_id, "")
        self.assertEqual(after.cursor, before.cursor)
        self.assertEqual(after.projects, before.projects)
        self.assertEqual(after.config_documents, before.config_documents)

    def test_local_capability_tampering_cannot_authorize_a_remote_write(self):
        before = self.owner.pull(self.remote_id, "")
        with self.participant("member"), bunkerdb._database(self.ids["member"]) as connection:
            connection.execute("UPDATE remote_connection SET capabilities_json = ?", (json.dumps(["config:read", "config:write"]),))
        self.assertTrue(self.configuration("member")["permission"]["can_write"])
        with self.assertRaises(commands.CommandRejected):
            self.command("member", "application.bunker.configuration.replace", project_id="ecs", kind="checks", expected_revision="", content={"build": "make unauthorized"})
        self.assertEqual(self.owner.pull(self.remote_id, "").config_documents, before.config_documents)
        self.assertNotIn("checks", self.configuration("member")["documents"])

    def test_owner_shared_change_arrives_without_transferring_local_attachment(self):
        self.command("owner", "application.bunker.repository.attach", project_id="ecs", path=str(self.repo))
        result = self.command("owner", "application.bunker.configuration.replace", project_id="ecs", kind="checks", expected_revision="", content={"build": "make simulation", "test": "", "timeout_seconds": 60, "discover": False})
        self.assertEqual(result["documents"]["checks"]["content"]["build"], "make simulation")
        snapshot = self.member.pull(self.remote_id, "")
        with self.participant("member"):
            bunkerdb.apply_snapshot(self.ids["member"], snapshot)
        member = self.configuration("member")
        self.assertEqual(member["documents"]["checks"]["content"]["build"], "make simulation")
        self.assertIsNone(member["repositories"]["items"][0]["local_path"])
        self.assertEqual(self.configuration("owner")["repositories"]["items"][0]["local_path"], str(self.repo))

    def test_wrong_project_binding_and_remote_keep_existing_attachment(self):
        self.command("member", "application.bunker.repository.attach", project_id="ecs", path=str(self.repo))
        before = self.configuration("member")
        for payload in ({"project_id": "systems", "binding_id": "coal"}, {"project_id": "ecs", "binding_id": "missing"}):
            with self.assertRaises(commands.CommandRejected):
                self.command("member", "application.bunker.repository.attach", path=str(self.repo), **payload)
        replacement = self.root / "second clone"
        self.git(self.repo, "clone", str(self.origin), str(replacement))
        self.git(replacement, "remote", "set-url", "origin", "https://github.com/example/wrong.git")
        with self.assertRaises(commands.CommandRejected):
            self.command("member", "application.bunker.repository.attach", project_id="ecs", binding_id="coal", path=str(replacement))
        self.assertEqual(self.configuration("member")["repositories"], before["repositories"])
        self.git(replacement, "remote", "set-url", "origin", "https://github.com/example/coal.git")
        self.git(replacement, "config", f"url.{self.origin.as_posix()}.insteadOf", "https://github.com/example/coal.git")
        self.command("member", "application.bunker.repository.attach", project_id="ecs", binding_id="coal", path=str(replacement))
        self.assertEqual(self.configuration("member")["repositories"]["items"][0]["local_path"], str(replacement))
        self.assertEqual((self.repo / "README.md").read_text(), "workspace membership simulation\n")
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "")

    def test_owner_cannot_bypass_bunker_registry_through_project_binding_command(self):
        configuration = self.configuration("owner")
        before = self.owner.pull(self.remote_id, "")
        for action in ("application.bunker.repository.replace", "application.project.repositories.select"):
            with self.subTest(action=action), self.assertRaises(commands.CommandRejected):
                self.command("owner", action, project_id="ecs", expected_revision=configuration["repositories"]["revision"], repositories=[{"name": "Unregistered", "canonical_remote": "github.com/example/not-enabled", "default_branch": "main"}])
        self.assertEqual(self.configuration("owner")["repositories"], configuration["repositories"])
        self.assertEqual(self.owner.pull(self.remote_id, "").cursor, before.cursor)

    def test_allowlist_changed_during_fetch_does_not_attach_stale_binding(self):
        def revoke_while_fetching(_root):
            # A freshly accepted remote configuration can arrive while Git
            # fetch runs. Simulate that already-authorized cache update.
            with bunkerdb._database(self.ids["member"]) as connection:
                connection.execute("DELETE FROM repository_bindings WHERE id = 'coal'")
        with patch.object(bunkerdb, "_fetch_repository", side_effect=revoke_while_fetching):
            with self.assertRaisesRegex(commands.CommandRejected, "changed while origin was fetching"):
                self.command("member", "application.bunker.repository.attach", project_id="ecs", path=str(self.repo))
        with self.participant("member"), bunkerdb._database(self.ids["member"]) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM local_repositories").fetchone()[0], 0)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "main")

    def test_wrong_project_detach_cannot_remove_member_checkout(self):
        self.command("member", "application.bunker.repository.attach", project_id="ecs", path=str(self.repo))
        with self.assertRaises(commands.CommandRejected):
            self.command("member", "application.bunker.repository.detach", project_id="systems", binding_id="coal")
        self.assertEqual(self.configuration("member")["repositories"]["items"][0]["local_path"], str(self.repo))


if __name__ == "__main__":
    unittest.main()
