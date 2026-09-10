"""Independent, disposable regressions for the long QA campaign's boundaries."""
import io
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from dataclasses import asdict, replace
from unittest.mock import Mock, patch

import test_bunker_cutover as cutover_fixture
import test_shared_failure_campaign as shared_fixture
from test_bunker_cutover import git
from ticketbunker import application, app_worker, bunkerdb, commands, config, coworker, editor, platform_services, remote, truth


class CampaignBoundaryReviewTests(unittest.TestCase):
    def setUp(self):
        self.lab = cutover_fixture.BunkerCutoverTests()
        self.lab.setUp()
        self.addCleanup(self.lab.doCleanups)
        self.repo = self.lab.repo

    def call(self, action, **payload):
        return app_worker.dispatch_request({"id": 1, "action": action, "payload": {
            "bunker_id": self.lab.bid, "repository_id": self.lab.rid, **payload}})

    @unittest.skipUnless(os.name == "posix", "POSIX directory-relative staging")
    def test_exclusive_temp_collision_never_deletes_preexisting_file(self):
        read = editor.read_source(self.repo, "README.md")
        collision = self.repo / (".README.md.tb-" + "00" * 12)
        collision.write_bytes(b"unrelated preexisting file")
        with patch.object(editor.os, "urandom", return_value=b"\x00" * 12):
            with self.assertRaises(commands.CommandRejected) as raised:
                editor.save_source(self.repo, "README.md", "new draft\n", read["digest"])
        self.assertEqual(raised.exception.code, "source_save_failed")
        self.assertEqual((self.repo / "README.md").read_text(), read["content"])
        self.assertTrue(collision.exists(), "A failed exclusive create must not delete somebody else's file")
        self.assertEqual(collision.read_bytes(), b"unrelated preexisting file")

    def test_detached_head_change_with_identical_source_requires_reopen(self):
        git(self.repo, "checkout", "--detach", "HEAD")
        read = self.call("workspace.file.read", path="README.md")
        git(self.repo, "commit", "--allow-empty", "-m", "another detached commit")
        payload = {"path": "README.md", "branch": "", "head": read["head"],
                   "content": "edited\n", "expected_digest": read["digest"]}
        rejected = self.call("workspace.file.save", **payload)
        self.assertEqual(rejected["code"], "workspace_branch_changed")
        self.assertEqual((self.repo / "README.md").read_text(), read["content"])
        fresh = self.call("workspace.file.read", path="README.md")
        self.assertNotEqual(fresh["head"], read["head"])
        self.assertTrue(self.call("workspace.file.save", **{**payload, "head": fresh["head"]})["ok"])

    @unittest.skipUnless(os.name == "posix", "POSIX directory-relative staging")
    def test_failure_cleanup_does_not_delete_replaced_temporary_inode(self):
        read = editor.read_source(self.repo, "README.md")
        replacement = self.repo / (".README.md.tb-" + "11" * 12)
        original_stage = self.repo / "held-temporary-stage"
        def replace_stage(_descriptor):
            replacement.rename(original_stage)
            replacement.write_bytes(b"a different file now owns this name")
            raise OSError("simulated staging failure")
        with patch.object(editor.os, "urandom", return_value=b"\x11" * 12), patch.object(editor.os, "fsync", side_effect=replace_stage):
            with self.assertRaises(commands.CommandRejected):
                editor.save_source(self.repo, "README.md", "new draft\n", read["digest"])
        self.assertEqual(replacement.read_bytes(), b"a different file now owns this name")
        self.assertEqual((self.repo / "README.md").read_text(), read["content"])

    def test_detached_head_missing_or_wrong_token_cannot_write(self):
        git(self.repo, "checkout", "--detach", "HEAD")
        read = self.call("workspace.file.read", path="README.md")
        for head in (None, "", "0" * 40, [], False):
            with self.subTest(head=head):
                result = self.call("workspace.file.save", path="README.md", branch="", head=head,
                                   content="edited\n", expected_digest=read["digest"])
                self.assertEqual(result["code"], "workspace_branch_changed")
                self.assertEqual((self.repo / "README.md").read_text(), read["content"])

    def test_worker_surrogate_response_preserves_next_response_and_request_id(self):
        requests = [{"id": index, "action": "application.state"} for index in (1, 2)]
        source = io.BytesIO(b"".join(json.dumps(row).encode() + b"\n" for row in requests))
        target = io.BytesIO()
        with patch.object(commands, "dispatch", side_effect=[{"ok": True, "legacy": "bad\ud800value"}, {"ok": True}]):
            self.assertEqual(app_worker.serve(source, target, default_root=self.repo), 0)
        responses = [json.loads(row) for row in target.getvalue().decode("utf-8").splitlines()]
        self.assertEqual([row["id"] for row in responses], [1, 2])
        self.assertEqual(responses[0]["legacy"], "bad\ud800value")
        self.assertTrue(responses[1]["ok"])

    def test_diagnostic_storage_failure_does_not_escape_worker_boundary(self):
        host = platform_services.current()
        with patch.object(commands, "dispatch", side_effect=RuntimeError("simulated command failure")), patch.object(type(host), "data_home", side_effect=OSError("simulation data directory unavailable")):
            result = app_worker.dispatch_request({"id": 8, "action": "application.state"}, default_root=self.repo)
        self.assertFalse(result["ok"])
        self.assertEqual(result["id"], 8)
        self.assertEqual(result["code"], "internal_error")

    def test_diagnostic_write_failure_is_actionable_and_does_not_echo_private_input(self):
        secret = "simulation-private-payload-never-display"
        with patch.object(commands, "dispatch", side_effect=RuntimeError(secret)), patch.object(Path, "open", side_effect=PermissionError("simulation diagnostic write denied")):
            result = app_worker.dispatch_request({"id": 9, "action": "application.state", "payload": {"secret": secret}}, default_root=self.repo)
        self.assertEqual(result["code"], "internal_error")
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(result["diagnostic_path"], "")
        self.assertNotIn("was saved", result["message"])

    def test_coworker_session_git_lookup_is_headless_on_windows(self):
        windows = platform_services.PlatformServices.detect(system="win32")
        result = Mock(returncode=0, stdout=str(self.repo / ".git"))
        with patch.object(platform_services, "current", return_value=windows), patch.object(coworker.subprocess, "run", return_value=result) as run:
            self.assertEqual(coworker._git_dir(self.repo), (self.repo / ".git").resolve())
        self.assertEqual(run.call_args.kwargs.get("creationflags"), 0x08000000)

    def test_application_repository_and_profile_git_queries_are_headless_on_windows(self):
        windows = platform_services.PlatformServices.detect(system="win32")
        result = Mock(returncode=0, stdout=str(self.repo))
        with patch.object(platform_services, "current", return_value=windows), patch.object(application.subprocess, "run", return_value=result) as run:
            application._git_value("user.name")
            application._source_root(str(self.repo))
            application._source_remote(self.repo)
        self.assertEqual(run.call_count, 3)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs.get("creationflags"), 0x08000000)

    def test_coworker_default_identity_queries_are_headless_on_windows(self):
        windows = platform_services.PlatformServices.detect(system="win32")
        result = Mock(returncode=0, stdout="Simulation User")
        with patch.object(platform_services, "current", return_value=windows), patch.object(config, "load", return_value={}), patch.object(config.subprocess, "run", return_value=result) as run:
            self.assertEqual(config.get_user(self.repo), ("Simulation-User", "git"))
            self.assertEqual(config.get_identity_name(self.repo), "Simulation User")
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs.get("creationflags"), 0x08000000)

    def test_failed_fetch_diagnostic_git_query_is_headless_on_windows(self):
        windows = platform_services.PlatformServices.detect(system="win32")
        result = Mock(returncode=0, stdout="https://example.test/simulation.git")
        with patch.object(platform_services, "current", return_value=windows), \
                patch.object(truth.project, "load_locator", return_value=None), \
                patch.object(truth.project, "refresh_source_refs", return_value=(False, "simulation offline")), \
                patch.object(truth, "_ticket"), patch.object(truth, "_git_dir", return_value=self.repo / ".git"), \
                patch.object(truth, "_content_revision", return_value="simulated"), \
                patch.object(truth, "synthesis_state", return_value={}), patch.object(truth, "_update"), \
                patch.object(truth.subprocess, "run", return_value=result) as run:
            refreshed = truth.command_refresh(commands.CommandContext(self.repo), {"ticket": "ECS-4"})
        self.assertEqual(refreshed["git"]["status"], "failed")
        self.assertEqual(run.call_args.kwargs.get("creationflags"), 0x08000000)


class CampaignSnapshotReviewTests(unittest.TestCase):
    def setUp(self):
        self.lab = shared_fixture.SharedFailureCampaignTests()
        self.lab.setUp()
        self.addCleanup(self.lab.doCleanups)

    def rows(self):
        rows = self.lab.local_rows()
        with self.lab.lab.participant("member"), bunkerdb._database(self.lab.lab.ids["member"]) as db:
            rows["members"] = sorted(tuple(row) for row in db.execute("SELECT * FROM members"))
        return rows

    def test_invalid_late_member_capabilities_roll_back_earlier_shared_upserts(self):
        snapshot = self.lab.member.pull(self.lab.remote_id, "")
        for malformed in ("config:write", [False], [{"config:write": True}]):
            with self.subTest(malformed=malformed):
                members = tuple({**row, "capabilities": malformed} for row in snapshot.members)
                changed = replace(snapshot, bunker={**snapshot.bunker, "name": "must roll back"}, members=members)
                before = self.rows()
                with self.lab.lab.participant("member"):
                    with self.assertRaises(bunkerdb.BunkerDatabaseError):
                        bunkerdb.apply_snapshot(self.lab.lab.ids["member"], changed, expected_cursor=snapshot.cursor)
                self.assertEqual(self.rows(), before)

    def test_malformed_capability_response_preserves_cursor_and_membership(self):
        snapshot = self.lab.member.pull(self.lab.remote_id, "")
        for malformed in (None, "config:write", [False], [""], [{}]):
            with self.subTest(malformed=malformed):
                value = asdict(snapshot)
                # HTTP JSON arrays arrive as lists, not dataclass tuples.
                value = json.loads(json.dumps(value))
                value["capabilities"] = malformed
                before = self.rows()
                with self.assertRaises(remote.RemoteContractError):
                    remote._snapshot(value)
                self.assertEqual(self.rows(), before)

    def test_late_pre_enrollment_snapshot_cannot_erase_new_member(self):
        before = self.lab.member.pull(self.lab.remote_id, "")
        invitation = self.lab.owner.create_invitation(self.lab.remote_id)
        link = self.lab.lab.service.invitation_link(self.lab.remote_id, invitation["token"])
        self.lab.lab.service.adapter().enroll(link, {"display_name": "Third simulation participant", "email": "third@example.test"})
        after = self.lab.member.pull(self.lab.remote_id, "")
        self.assertEqual(len(after.members), len(before.members) + 1)
        self.assertNotEqual(after.cursor, before.cursor)
        self.assertNotEqual(after.revision, before.revision)
        with self.lab.lab.participant("member"):
            bunkerdb.apply_snapshot(self.lab.lab.ids["member"], after, expected_cursor=before.cursor)
            rows = self.rows()
            with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "newer synchronization"):
                bunkerdb.apply_snapshot(self.lab.lab.ids["member"], before, expected_cursor=before.cursor)
        self.assertEqual(self.rows(), rows)

    def test_authenticated_existing_member_rejoin_does_not_advance_snapshot_version(self):
        before = self.lab.member.pull(self.lab.remote_id, "")
        invitation = self.lab.owner.create_invitation(self.lab.remote_id)
        link = self.lab.lab.service.invitation_link(self.lab.remote_id, invitation["token"])
        enrollment = self.lab.member.enroll(link, {})
        after = self.lab.member.pull(self.lab.remote_id, "")
        self.assertEqual(enrollment.snapshot.cursor, before.cursor)
        self.assertEqual(after.cursor, before.cursor)
        self.assertEqual(after.revision, before.revision)
        self.assertEqual(after.members, before.members)

    def test_concurrent_invitation_redemption_enrolls_only_one_participant(self):
        invitation = self.lab.owner.create_invitation(self.lab.remote_id)
        link = self.lab.lab.service.invitation_link(self.lab.remote_id, invitation["token"])
        before = self.lab.owner.pull(self.lab.remote_id, "")
        store = self.lab.lab.service.store
        original = store._invitation
        redeem = store.redeem_invitation
        barrier = threading.Barrier(2)
        validation_transactions = []
        def checked(*args):
            validation_transactions.append(args[0].in_transaction)
            return original(*args)
        def start(*args, **kwargs):
            barrier.wait(timeout=5)
            return redeem(*args, **kwargs)
        def enroll(index):
            try:
                self.lab.lab.service.adapter().enroll(link, {"display_name": f"Parallel simulation {index}"})
                return True
            except remote.RemoteContractError:
                return False
        with patch.object(store, "_invitation", side_effect=checked), patch.object(store, "redeem_invitation", side_effect=start), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(enroll, (1, 2)))
        self.assertEqual(validation_transactions, [True, True])
        self.assertEqual(sum(results), 1, "A one-use invitation must not enroll two participants concurrently")
        self.assertEqual(len(self.lab.owner.pull(self.lab.remote_id, "").members), len(before.members) + 1)

    def test_failed_enrollment_profile_rolls_back_and_invitation_remains_usable(self):
        invitation = self.lab.owner.create_invitation(self.lab.remote_id)
        link = self.lab.lab.service.invitation_link(self.lab.remote_id, invitation["token"])
        before = self.lab.owner.pull(self.lab.remote_id, "")
        with self.assertRaises(remote.RemoteContractError):
            self.lab.lab.service.adapter().enroll(link, {"display_name": ""})
        unchanged = self.lab.owner.pull(self.lab.remote_id, "")
        self.assertEqual(unchanged.members, before.members)
        self.assertEqual(unchanged.cursor, before.cursor)
        self.assertEqual(unchanged.revision, before.revision)
        self.lab.lab.service.adapter().enroll(link, {"display_name": "Valid retry participant"})
        self.assertEqual(len(self.lab.owner.pull(self.lab.remote_id, "").members), len(before.members) + 1)


if __name__ == "__main__":
    unittest.main()
