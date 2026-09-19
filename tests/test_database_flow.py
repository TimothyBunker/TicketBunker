"""Regression drills for the 2026-09-19 audit, using isolated HTTP/SQLite stores."""
import hashlib
from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

import test_service as fixtures
from ticketbunker import application, bunkerdb, commands, publication, remote
from ticketbunker.builders.master import from_records
from ticketbunker.service import snapshots


class DatabaseFlowTests(unittest.TestCase):
    def setUp(self):
        self.service = fixtures.ReferenceServiceTests()
        self.service.setUp()
        self.addCleanup(self.service.tearDown)
        self.environment = mock.patch.dict(os.environ, {"TB_DATA_HOME": self.service.temporary.name + "/device"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.adapter, _ = self.service.owner()
        local = bunkerdb.create_bunker("Flow lab", project_key="LAB")
        self.bid = local["id"]
        self.pid = local["projects"][0]["id"]
        snapshot = self.adapter.publish(bunkerdb.local_publication(self.bid))
        self.rid = snapshot.bunker["id"]
        bunkerdb.share_bunker(self.bid, server_url=self.service.origin, snapshot=snapshot, credential_reference="fixture:owner")
        self.adapter_patch = mock.patch.object(application, "_remote_adapter",
            side_effect=lambda bid: (self.adapter, bunkerdb.remote_connection(bid)))
        self.adapter_patch.start()
        self.addCleanup(self.adapter_patch.stop)

    def propose(self, *numbers):
        return bunkerdb.propose_ticket_sync(self.bid, project_id=self.pid, source_kind="manual",
            tickets=[{"id": f"LAB-{number}", "title": "Fixture", "status": "Todo"} for number in numbers])

    def push(self, proposal):
        return application._publish_changes(self.bid, proposal)

    def apply(self, proposal):
        receipt = self.push(proposal)
        bunkerdb.apply_ticket_sync_change(self.bid, proposal, accepted=receipt)
        return receipt

    def sync(self):
        return application.command_bunker_sync(None, {"bunker_id": self.bid})

    def local_count(self):
        return bunkerdb.get_bunker(self.bid)["ticket_count"]

    def test_write_receipt_does_not_claim_unseen_remote_data_was_pulled(self):
        original = bunkerdb.get_bunker(self.bid)["remote"]
        self.adapter.push(self.rid, "", tuple(self.propose(1)["changes"]))
        self.apply(self.propose(2))
        after = bunkerdb.get_bunker(self.bid)["remote"]
        self.assertEqual(after["sync_cursor"], original["sync_cursor"])
        self.assertEqual(after["last_synced_at"], original["last_synced_at"])
        self.assertEqual(after["last_write_cursor"], "change-2")
        self.assertEqual(self.local_count(), 1)
        self.sync()
        self.assertEqual(self.local_count(), 2)
        self.assertEqual(bunkerdb.remote_connection(self.bid)["sync_cursor"], "change-2")

    def test_snapshot_started_before_local_accepted_write_cannot_overwrite_it(self):
        connection = bunkerdb.remote_connection(self.bid)
        snapshot = self.adapter.pull(self.rid, "")
        self.apply(self.propose(1))
        with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "changed while"):
            bunkerdb.apply_snapshot(self.bid, snapshot, expected_cursor=connection["sync_cursor"],
                                   expected_generation=connection["replica_generation"])
        self.assertEqual(self.local_count(), 1)

    def test_lost_receipt_reuses_persisted_keys_and_sync_reconciles(self):
        pending = self.propose(1)
        push = self.adapter.push
        sent = []

        def dropped(*args):
            sent.extend(value["key"] for value in args[2])
            push(*args)
            raise remote.RemoteContractError("Fixture lost acknowledgement")

        with mock.patch.object(self.adapter, "push", side_effect=dropped):
            with self.assertRaises(commands.CommandRejected) as failure:
                self.push(pending)
        self.assertEqual(failure.exception.code, "publication_outcome_unknown")
        self.assertEqual(self.local_count(), 0)
        journal = bunkerdb.pending_publications(self.bid)[0]
        self.assertEqual(journal["state"], "unknown")
        self.assertEqual(json.loads(journal["changes_json"])[0]["key"], sent[0])
        with mock.patch.object(self.adapter, "push", wraps=push) as replay:
            self.sync()
        self.assertEqual(replay.call_args.args[2][0]["key"], sent[0])
        self.assertEqual(self.local_count(), 1)
        self.assertEqual(bunkerdb.pending_publications(self.bid), [])
        self.assertEqual(self.adapter.pull(self.rid, "").cursor, "change-1")

    def test_restart_after_receipt_before_local_apply_recovers_without_resending(self):
        self.push(self.propose(1))
        self.assertEqual(self.local_count(), 0)
        with mock.patch.object(self.adapter, "push", side_effect=AssertionError("Already accepted")):
            self.sync()
        self.assertEqual(self.local_count(), 1)
        self.assertEqual(bunkerdb.pending_publications(self.bid), [])

    def test_worker_exit_after_remote_commit_recovers_from_disk(self):
        # A separate worker exits after HTTP acceptance, before storing its receipt.
        script = '''
import os
from ticketbunker import application, bunkerdb, remote
adapter = remote.HTTPRemoteAdapter(os.environ["AUDIT_ORIGIN"], bearer=os.environ["AUDIT_TOKEN"])
bid = os.environ["AUDIT_BID"]
application._remote_adapter = lambda value: (adapter, bunkerdb.remote_connection(value))
real_push = adapter.push
def interrupted(*args):
    real_push(*args)
    os._exit(27)
adapter.push = interrupted
proposal = bunkerdb.propose_ticket_sync(bid, project_id=os.environ["AUDIT_PID"], source_kind="manual", tickets=[{"id":"LAB-1","title":"Fixture"}])
application._publish_changes(bid, proposal)
'''
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30,
            env={**os.environ, "AUDIT_ORIGIN": self.service.origin, "AUDIT_TOKEN": self.adapter.bearer,
                 "AUDIT_BID": self.bid, "AUDIT_PID": self.pid})
        self.assertEqual(proc.returncode, 27, proc.stderr)
        self.assertEqual(self.local_count(), 0)
        self.sync()
        self.assertEqual(self.local_count(), 1)
        self.assertEqual(self.adapter.pull(self.rid, "").cursor, "change-1")

    def test_reusing_receipt_key_for_changed_payload_is_rejected(self):
        proposal = self.propose(1)
        self.adapter.push(self.rid, "", tuple(proposal["changes"]))
        proposal["changes"][0]["payload"]["payload"]["title"] = "Different intent"
        with self.assertRaises(remote.RemoteContractError) as failure:
            self.adapter.push(self.rid, "", tuple(proposal["changes"]))
        self.assertEqual(failure.exception.code, "idempotency_conflict")

    def test_second_batch_conflict_preserves_intent_and_sync_loads_partial_success(self):
        self.adapter.push(self.rid, "", tuple(self.propose(201)["changes"]))
        proposal = self.propose(*range(1, 202))
        with self.assertRaises(commands.CommandRejected) as failure:
            self.push(proposal)
        self.assertEqual(failure.exception.code, "publication_conflict")
        self.assertIn("200 of 201", failure.exception.message)
        pending = bunkerdb.pending_publications(self.bid)[0]
        self.assertEqual(pending["accepted_count"], 200)
        self.assertEqual(self.local_count(), 0)
        self.assertEqual(bunkerdb.prepare_publication(self.bid, self.propose(*range(1, 202))["changes"])["id"], pending["id"])
        result = self.sync()
        self.assertEqual(self.local_count(), 201)
        self.assertEqual(result["publication_recovery"]["conflicts"], [pending["id"]])
        # An explicitly reviewed save supersedes the unresolved intent, not
        # unrelated pending operations.
        replacement = bunkerdb.propose_ticket_sync(self.bid, project_id=self.pid, source_kind="manual",
            tickets=[{"id": "LAB-201", "title": "Reviewed replacement", "status": "Todo"}])
        self.apply(replacement)
        self.assertEqual(bunkerdb.pending_publications(self.bid), [])

    def test_incomplete_write_receipt_never_advances_local_state(self):
        with mock.patch.object(self.adapter, "push", return_value={"cursor": "change-1", "revision": "2"}):
            with self.assertRaises(commands.CommandRejected):
                self.apply(self.propose(1))
        self.assertEqual(self.local_count(), 0)
        self.assertEqual(bunkerdb.pending_publications(self.bid)[0]["accepted_count"], 0)

    def test_receipt_must_acknowledge_the_proposed_record_revision(self):
        pending = self.propose(1)
        receipt = {"cursor": "change-1", "revision": "2", "results": [
            {"key": pending["changes"][0]["key"], "revision": "different-content", "ok": True}]}
        with mock.patch.object(self.adapter, "push", return_value=receipt):
            with self.assertRaises(commands.CommandRejected):
                self.apply(pending)
        self.assertEqual(self.local_count(), 0)

    def test_pending_action_cannot_be_replayed_into_a_different_bunker(self):
        operation = bunkerdb.prepare_publication(self.bid, self.propose(1)["changes"])
        connection = {**bunkerdb.remote_connection(self.bid), "remote_bunker_id": "different-bunker"}
        with mock.patch.object(self.adapter, "push", side_effect=AssertionError("Must not send")):
            with self.assertRaisesRegex(commands.CommandRejected, "previous service"):
                publication.resume(self.bid, self.adapter, connection, operation)

    def report_change(self, scope, content):
        return remote.change_record(key="report:" + hashlib.sha256((scope + content).encode()).hexdigest()[:24],
            kind="report", record_id=f"LAB-1|{scope}|", expected_revision="",
            revision="r:" + hashlib.sha256(content.encode()).hexdigest()[:16],
            payload={"ticket_id": "LAB-1", "scope": scope, "repository_id": "", "content": content})

    def test_service_rejects_ungated_reports_and_direct_combined(self):
        self.apply(self.propose(1))
        for scope, content in (("combined", "Untyped prose"), ("branch:work", "Untyped prose"),
                               ("branch:work", fixtures.valid_report("wrong-branch"))):
            with self.subTest(scope=scope, content=content[:20]):
                with self.assertRaises(remote.RemoteContractError) as error:
                    self.adapter.push(self.rid, "", (self.report_change(scope, content),))
                self.assertEqual(error.exception.code, "report_invalid")
        self.assertEqual(self.adapter.pull(self.rid, "").reports, ())

    def test_service_derives_combined_from_child_and_labels_client_attestation(self):
        self.apply(self.propose(1))
        content = fixtures.valid_report("work")
        self.adapter.push(self.rid, "", (self.report_change("branch:work", content),))
        reports = self.adapter.pull(self.rid, "").reports
        child = next(row for row in reports if row["scope"] == "branch:work")
        self.assertEqual(child["verification"]["kind"], "client-attested")
        self.assertEqual(next(row["content"] for row in reports if row["scope"] == "combined"), from_records("LAB-1", [child]))

    def test_initial_publication_cannot_bypass_report_gate(self):
        value = fixtures.publication("bad-import")
        value["reports"][1]["content"] = "bad report"
        with self.assertRaises(remote.RemoteContractError) as failure:
            self.adapter.publish(value)
        self.assertEqual(failure.exception.code, "report_invalid")

    def test_large_snapshot_and_large_join_are_transferred_in_bounded_chunks(self):
        proposal = self.propose(*range(1, 7))
        for row in proposal["changes"]:
            row["payload"]["payload"]["description"] = "x" * (1500 * 1024)
            self.adapter.push(self.rid, "", (row,))
        snapshot = self.adapter.pull(self.rid, "")
        self.assertEqual(len(snapshot.tickets), 6)
        self.assertGreater(sum(len(row["payload"]["description"]) for row in snapshot.tickets), remote.MAX_RESPONSE_BYTES)
        invitation = self.adapter.create_invitation(self.rid)
        link = self.service.invitation_link(self.rid, invitation["token"])
        joined = self.service.adapter().enroll(link, {"display_name": "Second participant"})
        self.assertEqual(len(joined.snapshot.tickets), 6)
        self.assertTrue(joined.issued_credential)
        with closing(self.service.store._connect()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM snapshot_transfers").fetchone()[0], 0)
        # SQLite's own context manager ends a transaction, not the connection.
        # Assert closure on every OS; Windows also refuses to remove an open DB.
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")

    def test_snapshot_is_immutable_and_bound_to_requesting_member(self):
        user = self.service.store.authenticate(self.adapter.bearer)["id"]
        transfer = snapshots.create(self.service.store, self.rid, user)
        self.apply(self.propose(1))
        import base64
        frozen = json.loads(base64.b64decode(snapshots.read(self.service.store, self.rid, user, transfer["id"], 0)["content"]))
        self.assertEqual(frozen["tickets"], [])
        with self.assertRaises(Exception):
            snapshots.read(self.service.store, self.rid, "not-a-member", transfer["id"], 0)

    def test_corrupt_snapshot_does_not_replace_replica(self):
        self.apply(self.propose(1))
        request = self.adapter._request

        def corrupt(method, path, payload=None):
            result = request(method, path, payload)
            if method == "GET" and "/snapshot-transfers/" in path:
                result = {**result, "content": "e30="}
            return result

        with mock.patch.object(self.adapter, "_request", side_effect=corrupt):
            with self.assertRaises(commands.CommandRejected):
                self.sync()
        self.assertEqual(self.local_count(), 1)
