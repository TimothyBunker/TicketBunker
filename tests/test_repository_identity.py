"""Native Windows path spelling must not change Project attachment authority."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ticketbunker import bunkerdb, bunkerruntime, platform_services, truth
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
