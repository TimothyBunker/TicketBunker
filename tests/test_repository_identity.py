"""Native Windows path spelling must not change Project attachment authority."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ticketbunker import autoagent, bunkerdb, bunkerruntime, platform_services, truth
from ticketbunker.adapters import gitrun


class RepositoryIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tb-identity-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.other = self.root / "other"
        self.other.mkdir()

    def test_identity_distinguishes_existing_and_missing_directories(self):
        identity = platform_services.local_path_identity
        self.assertNotEqual(identity(self.root), identity(self.other))
        self.assertEqual(identity(self.root), identity(self.other / ".."))
        self.assertNotEqual(identity(self.root / "missing"), identity(self.root / "missing2"))

    def test_ledgered_git_and_synthesis_helpers_request_headless_execution(self):
        from ticketbunker.ledger import Ledger

        service = mock.Mock()
        service.headless_process_options.return_value = {"creationflags": 0x08000000}
        with mock.patch.object(platform_services, "current", return_value=service), \
             mock.patch("subprocess.run", return_value=mock.Mock(stdout=b"", returncode=0)) as run:
            gitrun.git(self.root, ["status"], Ledger(self.root / "test.jsonl"), "test")
            self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
            run.return_value.stdout = "main\n"
            autoagent._git(self.root, "branch", "--show-current")
            self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
            truth._current_branch(self.root)
            self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
            bunkerdb._git_value(self.root, "branch", "--show-current")
            self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)

    def test_unattached_repository_and_wrong_binding_stay_rejected(self):
        detail = {"repositories": [{"id": "allowed", "local_path": str(self.root)}]}
        self.assertEqual(bunkerruntime.select_repository(detail, repo=self.root)["id"], "allowed")
        for arguments in ({"repo": self.other}, {"repo": self.root, "repository_id": "wrong"}):
            with self.assertRaises(bunkerdb.BunkerDatabaseError):
                bunkerruntime.select_repository(detail, **arguments)

    @unittest.skipUnless(os.name == "nt", "native Windows verbatim-path regression")
    def test_native_verbatim_path_shares_attachment_lock_and_refresh_identity(self):
        root = str(self.root)
        extended = Path("\\\\?\\UNC\\" + root[2:] if root.startswith("\\\\") else "\\\\?\\" + root)
        self.assertTrue(self.root.samefile(extended))
        detail = {"repositories": [{"id": "allowed", "local_path": root}]}
        self.assertEqual(bunkerruntime.select_repository(detail, repo=extended)["id"], "allowed")
        key = platform_services.local_path_identity(self.root)
        self.assertEqual(key, platform_services.local_path_identity(extended))
        with gitrun.repository_lock(self.root):
            lock = gitrun._REPOSITORY_LOCKS[key]
            with gitrun.repository_lock(extended):
                self.assertIs(gitrun._REPOSITORY_LOCKS[key], lock)
        target = (key, "DEMO", "DEMO-1")
        truth._RUNNING.add(target)
        try:
            with mock.patch.object(truth, "_ticket", return_value=None), mock.patch.object(
                truth, "_read_all", return_value={"tickets": {truth._key("DEMO", "DEMO-1"): {"git": {"status": "refreshing"}}}}
            ):
                self.assertEqual(truth.state(extended, "DEMO", "DEMO-1")["git"]["status"], "refreshing")
            self.assertFalse(truth.queue_refresh(extended, "DEMO", "DEMO-1", force=True))
        finally:
            truth._RUNNING.discard(target)


@unittest.skipUnless(os.name == "nt", "native Windows worker and Git regression")
class NativeWindowsTicketRefreshTests(unittest.TestCase):
    def test_native_verbatim_branch_lookup_and_synthesis_keep_selected_ticket(self):
        from test_bunker_cutover import BunkerCutoverTests, git
        from ticketbunker import app_worker

        lab = BunkerCutoverTests()
        lab.setUp()
        self.addCleanup(lab.doCleanups)
        branch = lab.fixture.start_work()
        normal = str(lab.repo.resolve())
        extended = "\\\\?\\UNC\\" + normal[2:] if normal.startswith("\\\\") else "\\\\?\\" + normal
        found = bunkerdb.find_bunker_ticket(extended, branch)
        self.assertIsNotNone(found)
        self.assertEqual(found["repository_id"], lab.rid)
        with mock.patch.object(autoagent, "_spawn_worker"):
            queued = app_worker.dispatch_request({"id": 1, "action": "truth.synthesize", "project_root": extended,
                "payload": {"bunker_id": lab.bid, "project": "ECS", "ticket": "ECS-4", "branch": branch}})
        self.assertTrue(queued["queued"], queued)
        # Real child processes: deterministic refresh, stub agent, lint, anchor
        # verification and database publication, with the native path spelling.
        status = autoagent.run_worker(Path(extended), debounce=0, once=True)
        self.assertEqual(status, 0, autoagent.read_state(Path(extended)))
        opened = lab.dispatch("application.ticket.get", {"ticket_id": "ECS-4"})
        self.assertEqual(opened["truth"]["synthesis"]["status"], "current")
        self.assertIn(branch, opened["ticket"]["branch_assignments"][lab.rid])
        self.assertEqual(git(lab.repo, "status", "--porcelain"), "")

    def test_native_worker_refresh_uses_existing_project_attachment(self):
        from test_bunker_cutover import BunkerCutoverTests, git
        from ticketbunker import app_worker

        lab = BunkerCutoverTests()
        lab.setUp()
        self.addCleanup(lab.doCleanups)
        lab.offline_origin()
        normal = str(lab.repo.resolve())
        extended = "\\\\?\\UNC\\" + normal[2:] if normal.startswith("\\\\") else "\\\\?\\" + normal
        before = bunkerdb.get_ticket(lab.bid, "ECS-4")["repositories"]
        refreshed = app_worker.dispatch_request({"id": 1, "action": "truth.refresh", "project_root": extended,
            "payload": {"bunker_id": lab.bid, "project": "ECS", "ticket": "ECS-4"}})
        self.assertTrue(refreshed.get("ok", True), refreshed)
        self.assertEqual(refreshed["git"]["status"], "current", refreshed)
        reopened = app_worker.dispatch_request({"id": 2, "action": "application.ticket.get",
            "payload": {"bunker_id": lab.bid, "ticket_id": "ECS-4"}})
        self.assertEqual(reopened["truth"]["git"]["status"], "current")
        self.assertEqual(bunkerdb.get_ticket(lab.bid, "ECS-4")["repositories"], before)
        self.assertEqual(git(lab.repo, "status", "--porcelain"), "")
        created = app_worker.dispatch_request({"id": 3, "action": "workflow.start", "project_root": extended,
            "payload": {"bunker_id": lab.bid, "ticket": "ECS-4", "name": "identity", "user": "Ada"}})
        self.assertEqual(created["branch"], "Ada/ECS-4-identity")
        self.assertEqual(git(lab.repo, "branch", "--show-current"), created["branch"])
        self.assertEqual(git(lab.repo, "status", "--porcelain"), "")
