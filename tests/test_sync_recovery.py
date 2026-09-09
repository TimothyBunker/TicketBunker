"""Disposable restart/recovery drills through the real command and DB gates."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import test_application as fixtures
from ticketbunker import application, bunkerdb, commands, config, projectconfig, remote
from ticketbunker.integrations import github


class SyncRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.lab = fixtures.TrackerSyncTests()
        self.lab.setUp()
        self.addCleanup(self.lab.tearDown)
        self.call = self.lab.dispatch
        created = self.call("application.bunker.create", {"name": "Recovery lab", "project_key": "ECS"})["bunker"]
        self.bid, self.pid = created["id"], created["projects"][0]["id"]
        self.payload = {"bunker_id": self.bid, "project_id": self.pid}
        self.call("application.bunker.configuration.replace", {**self.payload, "kind": "tracker-source", "expected_revision": "",
            "content": {"provider": "github", "kind": "project", "owner": "example", "number": 8}})
        patch = mock.patch.object(projectconfig, "credential_value", return_value="dummy-recovery-token")
        patch.start(); self.addCleanup(patch.stop)

    def sync(self, entries):
        with mock.patch.object(github, "collect_entries", return_value=entries):
            return self.call("application.project.sync", self.payload)

    def restarted(self, action, payload):
        proc = subprocess.run([sys.executable, "-m", "ticketbunker.cli", "_app-worker"],
            input=json.dumps({"id": 1, "action": action, "payload": payload}) + "\n", text=True,
            capture_output=True, timeout=20, env={**os.environ, "TB_CONFIG": str(config.GLOBAL_CONFIG)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok"], result)
        return result

    def test_success_and_failure_receipts_survive_a_fresh_worker(self):
        first = self.sync([fixtures.github_entry(4)])
        stamp = first["bunker"]["projects"][0]["ticket_sync"]["last_success_at"]
        self.assertTrue(stamp)
        with mock.patch.object(github, "collect_entries", side_effect=github.GitHubError("Dummy connection dropped")):
            with self.assertRaises(commands.CommandRejected):
                self.call("application.project.sync", self.payload)
        reopened = self.restarted("application.bunker.get", {"bunker_id": self.bid})["bunker"]
        receipt = reopened["projects"][0]["ticket_sync"]
        self.assertEqual(receipt["last_success_at"], stamp)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(reopened["ticket_count"], 1)
        self.assertNotIn("dummy-recovery-token", json.dumps(reopened))
        retried = self.sync([fixtures.github_entry(4), fixtures.github_entry(5)])
        self.assertEqual(retried["bunker"]["ticket_count"], 2)
        self.assertEqual(retried["bunker"]["projects"][0]["ticket_sync"]["status"], "current")
        again = self.sync([fixtures.github_entry(4), fixtures.github_entry(5)])
        self.assertEqual(again["sync"]["unchanged"], 2)
        # Device receipts are not published as team configuration or Project fields.
        published = bunkerdb.local_publication(self.bid)
        self.assertNotIn("ticket_sync", json.dumps(published))

    def test_process_exit_mid_transaction_rolls_back_and_retry_has_no_duplicates(self):
        self.sync([fixtures.github_entry(4)])
        before = bunkerdb.get_bunker(self.bid)["projects"][0]["ticket_sync"]["last_success_at"]
        # Exit after SQLite executes an INSERT, before the transaction and receipt commit.
        script = '''
import os, sys
from unittest import mock
from ticketbunker import application, bunkerdb, commands, projectconfig
from ticketbunker.integrations import github
import test_application as fixtures
original = bunkerdb._ClosingConnection.execute
def interrupted(self, sql, *args, **kwargs):
    result = original(self, sql, *args, **kwargs)
    if "INSERT INTO tickets(id, project_id" in sql:
        os._exit(23)
    return result
with mock.patch.object(projectconfig, "credential_value", return_value="dummy"), mock.patch.object(github, "collect_entries", return_value=[fixtures.github_entry(4, "Done"), fixtures.github_entry(5)]), mock.patch.object(bunkerdb._ClosingConnection, "execute", interrupted):
    commands.dispatch("application.project.sync", {"bunker_id": sys.argv[1], "project_id": sys.argv[2]}, commands.CommandContext(__import__("pathlib").Path.cwd()))
'''
        proc = subprocess.run([sys.executable, "-c", script, self.bid, self.pid], text=True, capture_output=True,
            timeout=20, env={**os.environ, "TB_CONFIG": str(config.GLOBAL_CONFIG),
                "PYTHONPATH": os.pathsep.join([str(Path(__file__).resolve().parent.parent), str(Path(__file__).resolve().parent)])})
        self.assertEqual(proc.returncode, 23, proc.stderr)
        reopened = self.restarted("application.bunker.get", {"bunker_id": self.bid})["bunker"]
        self.assertEqual(reopened["ticket_count"], 1)
        receipt = reopened["projects"][0]["ticket_sync"]
        self.assertEqual(receipt["status"], "pending")
        self.assertEqual(receipt["last_success_at"], before)
        ticket = self.restarted("application.ticket.get", {"bunker_id": self.bid, "ticket_id": "ECS-4"})
        self.assertEqual(ticket["ticket"]["status"], "todo")
        result = self.sync([fixtures.github_entry(4, "Done"), fixtures.github_entry(5)])
        self.assertEqual(result["bunker"]["ticket_count"], 2)
        self.assertEqual(result["sync"]["updated"], 1)
        self.assertEqual(result["sync"]["created"], 1)


class SharedSyncRecoveryTests(unittest.TestCase):
    def test_lost_acceptance_is_recovered_by_shared_sync_before_retry(self):
        lab = fixtures.SharedBunkerTests(); lab.setUp(); self.addCleanup(lab.tearDown)
        lab.as_user("owner")
        created = lab.dispatch("application.bunker.create", {"name": "Recovery lab", "project_key": "ECS"})["bunker"]
        bid, pid = created["id"], created["projects"][0]["id"]
        payload = {"bunker_id": bid, "project_id": pid}
        lab.dispatch("application.bunker.configuration.replace", {**payload, "kind": "tracker-source", "expected_revision": "",
            "content": {"provider": "github", "kind": "project", "owner": "example", "number": 8}})
        lab.dispatch("application.bunker.share", {"bunker_id": bid, "server_url": lab.origin, "setup_token": lab.setup_token})
        original_push = remote.HTTPRemoteAdapter.push
        def lose_reply(adapter, *args, **kwargs):
            original_push(adapter, *args, **kwargs)
            raise remote.RemoteContractError("service_unreachable", "Dummy reply lost after server commit")
        with mock.patch.object(projectconfig, "credential_value", return_value="dummy"), mock.patch.object(github, "collect_entries", return_value=[fixtures.github_entry(4), fixtures.github_entry(5)]):
            with mock.patch.object(remote.HTTPRemoteAdapter, "push", lose_reply):
                with self.assertRaises(commands.CommandRejected):
                    lab.dispatch("application.project.sync", payload)
            self.assertEqual(bunkerdb.get_bunker(bid)["ticket_count"], 0)
            # Reopen the same device settings and reconcile authoritative data.
            lab.as_user("owner")
            lab.dispatch("application.bunker.sync", {"bunker_id": bid})
            self.assertEqual(bunkerdb.get_bunker(bid)["ticket_count"], 2)
            result = lab.dispatch("application.project.sync", payload)
            self.assertEqual(result["sync"]["unchanged"], 2)
            self.assertEqual(result["bunker"]["ticket_count"], 2)
