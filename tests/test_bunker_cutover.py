"""New Bunkers must never require or reread companion-Git storage."""
from __future__ import annotations

import json
import threading
import unittest
from unittest import mock

import test_bunkerruntime as runtime_tests
from test_bunkerruntime import git
from ticketbunker import autoagent, bunkerdb, bunkerruntime, cli, commands, layout
from ticketbunker import credentials, remote
from ticketbunker.service import server as service_server, storage as service_storage


class BunkerCutoverTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_tests.BunkerRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.repo = self.fixture.repo
        self.bid = self.fixture.bunker_id
        self.pid = self.fixture.project_id
        self.rid = self.fixture.repository_id

    def offline_origin(self):
        remote = self.fixture.root / "origin.git"
        git(self.repo, "clone", "--quiet", "--bare", str(self.repo), str(remote))
        git(self.repo, "config", f"url.{remote}.insteadOf", "https://github.com/example/coal.git")

    def dispatch(self, name, payload, repo=None):
        return commands.dispatch(name, {"bunker_id": self.bid, "ticket": "ECS-4", **payload},
                                 commands.CommandContext(repo or self.repo, transport="desktop"))

    def publish(self, text, branch="Ada/ECS-4-work", rid=None):
        proposal = bunkerdb.propose_report_publication(self.bid, ticket_id="ECS-4",
            repository_id=rid or self.rid, branch=branch, child=text, master="untrusted master")
        bunkerdb.apply_report_publication(self.bid, proposal)
        return proposal

    def second_repository(self):
        root = self.fixture.root / "renderer"
        git(self.repo, "clone", "--quiet", str(self.repo), str(root))
        git(root, "remote", "set-url", "origin", "https://github.com/example/renderer.git")
        cfg = bunkerdb.get_configuration(self.bid, project_id=self.pid)
        original = cfg["repositories"]["items"][0]
        cfg = bunkerdb.replace_repository_bindings(self.bid, project_id=self.pid,
            expected_revision=cfg["repositories"]["revision"], repositories=[
                {key: original[key] for key in ("id", "name", "canonical_remote", "default_branch")},
                {"name": "Renderer", "canonical_remote": "github.com/example/renderer", "default_branch": "main"}])
        rid = next(row["id"] for row in cfg["repositories"]["items"] if row["name"] == "Renderer")
        with mock.patch.object(bunkerdb, "_fetch_repository"):
            bunkerdb.attach_repository(self.bid, project_id=self.pid, binding_id=rid, local_path=str(root))
        return root, rid

    def test_native_branch_creation_fetches_and_records_work_without_companion(self):
        self.offline_origin()
        with mock.patch("ticketbunker.project.publish_data", side_effect=AssertionError("no companion writes")):
            result = self.dispatch("workflow.start", {"name": "position", "user": "Ada"})
        self.assertEqual(result["branch"], "Ada/ECS-4-position")
        self.assertEqual(git(self.repo, "branch", "--show-current"), result["branch"])
        detail = bunkerdb.get_ticket(self.bid, "ECS-4")
        self.assertEqual(detail["ticket"]["branch_assignments"][self.rid], [result["branch"]])
        self.assertFalse((self.repo / ".tb").exists())
        self.assertFalse((self.repo / ".ticketbunker.json").exists())
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertIn("origin/main", git(self.repo, "branch", "-r"))

    def test_branch_preflight_preserves_dirty_work(self):
        path = self.repo / "README.md"
        path.write_text("unsaved work\n")
        with self.assertRaises(commands.CommandRejected) as caught:
            self.dispatch("workflow.start", {"name": "position", "user": "Ada"})
        self.assertEqual(caught.exception.code, "worktree_dirty")
        self.assertIn("README.md", str(caught.exception))
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(path.read_text(), "unsaved work\n")

    def test_default_ticket_and_tracker_labels_start_work(self):
        self.offline_origin()
        for index, fields in enumerate(({}, {"type": "Task", "status": "Todo"})):
            with self.subTest(fields=fields):
                proposal = bunkerdb.propose_ticket_sync(self.bid, project_id=self.pid,
                    source_kind="manual", tickets=[{"id": "ECS-4", "title": "Default ticket", **fields}])
                bunkerdb.apply_ticket_sync_change(self.bid, proposal)
                result = self.dispatch("workflow.start", {"name": f"default-{index}", "user": "Ada"})
                self.assertEqual(git(self.repo, "branch", "--show-current"), result["branch"])

    def test_invalid_ticket_rejected_before_any_git_mutation(self):
        proposal = bunkerdb.propose_ticket_sync(self.bid, project_id=self.pid,
            source_kind="manual", tickets=[{"id": "ECS-4", "title": "Custom ticket", "type": "unsupported-kind"}])
        bunkerdb.apply_ticket_sync_change(self.bid, proposal)
        before = git(self.repo, "show-ref")
        with self.assertRaises(commands.CommandRejected) as caught:
            self.dispatch("workflow.start", {"name": "invalid", "user": "Ada"})
        self.assertEqual(caught.exception.code, "ticket_invalid")
        self.assertEqual(git(self.repo, "show-ref"), before)
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")

    def test_attach_keeps_existing_branch_and_rejects_wrong_ticket(self):
        git(self.repo, "branch", "Ada/ECS-4-existing")
        git(self.repo, "branch", "Ada/ECS-5-wrong")
        self.dispatch("workflow.attach", {"branch": "Ada/ECS-4-existing"})
        with self.assertRaises(commands.CommandRejected):
            self.dispatch("workflow.attach", {"branch": "Ada/ECS-5-wrong"})
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")

    def test_branch_inventory_uses_the_same_bounded_key_as_synthesis(self):
        git(self.repo, "branch", "Ada/ECS-40-other-ticket")
        git(self.repo, "switch", "-c", "Ada/ECS-4-existing")
        git(self.repo, "update-ref", "refs/remotes/origin/Ada/ECS-4-existing", "HEAD")
        detail = bunkerdb.get_ticket(self.bid, "ECS-4")
        self.assertEqual([row["name"] for row in detail["branches"]], ["Ada/ECS-4-existing"])
        self.assertTrue(detail["branches"][0]["current"])
        self.assertTrue(detail["branches"][0]["remote"])

    def test_stale_synthesis_button_never_queues_a_different_checkout(self):
        git(self.repo, "switch", "-c", "Ada/ECS-4-new")
        with mock.patch.object(autoagent, "enqueue") as queued:
            with self.assertRaises(commands.CommandRejected) as caught:
                self.dispatch("truth.synthesize", {"project": "ECS", "branch": "Ada/ECS-4-old"})
        self.assertEqual(caught.exception.code, "synthesis_branch_changed")
        queued.assert_not_called()

    def test_explicit_refresh_does_not_rediscover_ticket_and_rejects_wrong_branch(self):
        branch = self.fixture.start_work()
        info = bunkerruntime.materialize(self.bid, "ECS-4", repo=self.repo)
        args = ["refresh", "--repo", str(self.repo), "--quiet", "--strict",
                "--bunker-dir", str(info["bunker"]), "--expected-branch", branch]
        with mock.patch.object(bunkerruntime, "resolve", side_effect=AssertionError("already selected")):
            self.assertEqual(cli.main(args), 0)
        git(self.repo, "switch", "main")
        self.assertEqual(cli.main(args), 1)
        self.assertEqual(git(self.repo, "branch", "--show-current"), "main")

    def test_tracker_refresh_keeps_ticketbunker_branch_relationships(self):
        git(self.repo, "branch", "Ada/ECS-4-existing")
        self.dispatch("workflow.attach", {"branch": "Ada/ECS-4-existing"})
        proposal = bunkerdb.propose_ticket_sync(self.bid, project_id=self.pid,
            source_kind="github", tickets=[{"id": "ECS-4", "title": "Changed upstream"}])
        bunkerdb.apply_ticket_sync_change(self.bid, proposal)
        self.assertEqual(bunkerdb.get_ticket(self.bid, "ECS-4")["ticket"]["branch_assignments"][self.rid],
                         ["Ada/ECS-4-existing"])

    def test_shared_branch_assignment_reaches_service_and_failed_link_keeps_branch(self):
        self.offline_origin()
        store = service_storage.ServiceStore(self.fixture.root / "service.sqlite3")
        store.initialize()
        server = service_server.serve(store, host="127.0.0.1", port=0, quiet=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.dispatch("application.profile.update", {"name": "Ada", "email": "ada@example.test", "branch_user": "Ada"})
            shared = self.dispatch("application.bunker.share", {"server_url": server.origin,
                                   "setup_token": store.issue_setup_token()})["bunker"]
            connection = store._connect()
            try:
                with connection:
                    initial = connection.execute("SELECT revision FROM records WHERE kind = 'ticket'").fetchone()
                    self.assertTrue(initial["revision"].startswith("t:"))
                    # An existing server may still carry the old initial revision.
                    connection.execute("UPDATE records SET revision = '1' WHERE kind = 'ticket'")
            finally:
                connection.close()
            result = self.dispatch("workflow.start", {"name": "shared", "user": "Ada"})
            adapter = remote.HTTPRemoteAdapter(server.origin,
                bearer=credentials.resolve(credentials.find(server.origin)),
                credential_reference=credentials.find(server.origin))
            snapshot = adapter.pull(shared["remote"]["remote_bunker_id"], "")
            self.assertEqual(snapshot.tickets[0]["payload"]["branch_assignments"][self.rid], [result["branch"]])
            with mock.patch("ticketbunker.application._publish_changes",
                            side_effect=commands.CommandRejected("offline", "Service unavailable")):
                with self.assertRaises(commands.CommandRejected) as caught:
                    self.dispatch("workflow.start", {"name": "pending", "user": "Ada"})
            self.assertEqual(caught.exception.code, "branch_assignment_pending")
            self.assertEqual(git(self.repo, "branch", "--show-current"), "Ada/ECS-4-pending")
            self.assertEqual(git(self.repo, "status", "--porcelain"), "")
            self.dispatch("workflow.attach", {"branch": "Ada/ECS-4-pending"})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_runtime_projection_receives_new_database_branch_links(self):
        info = bunkerruntime.materialize(self.bid, "ECS-4")
        proposal = bunkerdb.propose_branch_assignment(self.bid, "ECS-4", self.rid, "Ada/ECS-4-shared")
        bunkerdb.apply_ticket_sync_change(self.bid, proposal)
        bunkerruntime.materialize(self.bid, "ECS-4")
        ticket = json.loads(layout.paths(info["bunker"])["ticket"].read_text())
        self.assertEqual(ticket["assigned_branches"], ["Ada/ECS-4-shared"])

    def test_runtime_and_lookup_preserve_second_repository_identity(self):
        other, rid = self.second_repository()
        branch = "Ada/ECS-4-work"
        git(other, "checkout", "-q", "-b", branch)
        runtime = bunkerruntime.resolve(other, branch)
        self.assertEqual(bunkerruntime.marker(runtime)["repository_id"], rid)
        first = bunkerruntime.materialize(self.bid, "ECS-4", repo=self.repo)
        self.assertNotEqual(runtime, first["bunker"])
        with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "Choose a repository"):
            bunkerruntime.materialize(self.bid, "ECS-4")
        with self.assertRaises(bunkerdb.BunkerDatabaseError):
            bunkerruntime.materialize(self.bid, "ECS-4", repo=self.fixture.root)

    def test_selected_ticket_repository_controls_truth_and_not_legacy_storage(self):
        other, rid = self.second_repository()
        result = self.dispatch("application.ticket.get", {"ticket_id": "ECS-4", "repository_id": rid})
        self.assertEqual(result["selected_repository_id"], rid)
        self.assertEqual(bunkerruntime.marker(bunkerruntime.ticket_dir(self.bid, "ECS-4", rid))["repository_path"], str(other))

    def test_clean_runtime_updates_but_local_draft_survives(self):
        self.publish("version one\n")
        info = bunkerruntime.materialize(self.bid, "ECS-4")
        child = layout.child_report(info["bunker"], "Ada/ECS-4-work")
        self.publish("version two\n")
        bunkerruntime.materialize(self.bid, "ECS-4")
        self.assertEqual(child.read_text(), "version two\n")
        child.write_text("local unpublished draft\n")
        self.publish("version three\n")
        bunkerruntime.materialize(self.bid, "ECS-4")
        self.assertEqual(child.read_text(), "local unpublished draft\n")
        with self.assertRaises(commands.CommandRejected) as caught:
            bunkerruntime.publish_reports(info["bunker"], "Ada/ECS-4-work")
        self.assertEqual(caught.exception.code, "report_conflict")

    def test_master_contains_same_named_children_from_both_repositories(self):
        _, rid = self.second_repository()
        self.publish("First repository report\n")
        self.publish("Second repository report\n", rid=rid)
        combined = next(value["content"] for value in bunkerdb.get_ticket(self.bid, "ECS-4")["reports"]
                        if value["scope"] == "combined")
        self.assertIn("First repository report", combined)
        self.assertIn("Second repository report", combined)
        self.assertNotIn("untrusted master", combined)

    def test_report_receipts_handle_windows_newlines_and_missing_final_newline(self):
        self.publish("version one")
        info = bunkerruntime.materialize(self.bid, "ECS-4")
        child = layout.child_report(info["bunker"], "Ada/ECS-4-work")
        child.write_bytes(b"version one\r\n")
        self.publish("version two")
        bunkerruntime.materialize(self.bid, "ECS-4")
        self.assertEqual(child.read_text(), "version two\n")
        child.write_text("new local version\n")
        result = bunkerruntime.publish_reports(info["bunker"], "Ada/ECS-4-work")
        self.assertFalse(result["shared"])

    def test_stale_local_publication_cannot_overwrite_a_new_child(self):
        original = self.publish("original\n")
        self.publish("newer\n")
        with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "Reports changed"):
            bunkerdb.apply_report_publication(self.bid, original)

    def test_old_runtime_moves_only_to_its_recorded_repository(self):
        old = bunkerruntime.runtime_root(self.bid) / "ECS-4_bunker"
        old.mkdir(parents=True)
        (old / "bunker.json").write_text(json.dumps({"bunker_id": self.bid, "repository_id": self.rid}))
        draft = old / "retained-draft.md"
        draft.write_text("private draft\n")
        info = bunkerruntime.materialize(self.bid, "ECS-4")
        self.assertEqual((info["bunker"] / draft.name).read_text(), "private draft\n")
        self.assertFalse(old.exists())

    def test_old_runtime_is_not_moved_under_an_active_worker(self):
        old = bunkerruntime.runtime_root(self.bid) / "ECS-4_bunker"
        old.mkdir(parents=True)
        (old / "bunker.json").write_text(json.dumps({"bunker_id": self.bid, "repository_id": self.rid}))
        with mock.patch("ticketbunker.autoagent.read_state", return_value={"running": {"status": "running"}}):
            with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "Let it finish"):
                bunkerruntime.materialize(self.bid, "ECS-4")
        self.assertTrue(old.exists())
