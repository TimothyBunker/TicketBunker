"""Read-only-to-the-product audit: all writes use disposable SQLite databases.

Run from the repository: PYTHONPATH=. python docs/audits/2026-09-19/probe_database_flow.py
Assertions capture weaknesses at d6c8cb5, not desired regression-test behavior.
No real credentials, network, source repositories, or model calls are used.
"""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import tempfile
from unittest import mock

from ticketbunker import application, bunkerdb, remote
from ticketbunker.service.storage import ServiceStore


@contextmanager
def lab():
    with tempfile.TemporaryDirectory(prefix="tb-db-audit-") as folder:
        with mock.patch.dict(os.environ, {"TB_DATA_HOME": str(Path(folder) / "device")}):
            local = bunkerdb.create_bunker("Audit fixture", project_key="LAB")
            store = ServiceStore(Path(folder) / "service.sqlite3")
            store.initialize()
            owner = store.complete_setup(store.issue_setup_token(), {"display_name": "Audit owner"})
            uid = owner["user"]["id"]
            snap = store.publish_bunker(uid, bunkerdb.local_publication(local["id"]))
            rid = snap["bunker"]["id"]
            bunkerdb.share_bunker(local["id"], server_url="http://localhost:9876",
                                 snapshot=remote._snapshot(snap), credential_reference="audit:dummy")
            yield local["id"], local["projects"][0]["id"], store, uid, rid


def ticket(number, title="Audit ticket"):
    return {"id": f"LAB-{number}", "title": title, "status": "Todo"}


def proposal(bid, pid, values):
    return bunkerdb.propose_ticket_sync(bid, project_id=pid, tickets=values, source_kind="manual")


def local_count(bid):
    with bunkerdb._database(bid) as db:
        return db.execute("SELECT count(*) FROM tickets").fetchone()[0]


def write_receipt_is_not_a_read_cursor():
    with lab() as (bid, pid, store, uid, rid):
        other = proposal(bid, pid, [ticket(1, "Someone else's work")])
        store.apply_changes(rid, uid, "change-0", other["changes"])
        own = proposal(bid, pid, [ticket(2, "My work")])
        accepted = store.apply_changes(rid, uid, "change-0", own["changes"])
        bunkerdb.apply_ticket_sync_change(bid, own, accepted=accepted)
        snap = store.snapshot(rid, uid)
        conn = bunkerdb.remote_connection(bid)
        assert conn["sync_cursor"] == snap["cursor"] and local_count(bid) == 1
        assert len(snap["tickets"]) == 2
        with bunkerdb._database(bid) as db:
            stamped = db.execute("SELECT last_synced_at FROM remote_connection").fetchone()[0]
        return {"local_tickets": 1, "remote_tickets": 2, "both_cursors": snap["cursor"],
                "last_synced_at_set": bool(stamped)}


def batch_failure_leaves_remote_partial():
    with lab() as (bid, pid, store, uid, rid):
        other = proposal(bid, pid, [ticket(201, "Already created elsewhere")])
        store.apply_changes(rid, uid, "change-0", other["changes"])
        pending = proposal(bid, pid, [ticket(i) for i in range(1, 202)])

        class Adapter:
            def push(self, bunker, cursor, changes):
                return store.apply_changes(bunker, uid, cursor, changes)

        with mock.patch.object(application, "_remote_adapter", return_value=(Adapter(), bunkerdb.remote_connection(bid))):
            try:
                application._publish_changes(bid, pending)
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
            else:
                raise AssertionError("Expected final batch conflict")
        assert len(store.snapshot(rid, uid)["tickets"]) == 201 and local_count(bid) == 0
        retry = proposal(bid, pid, [ticket(i) for i in range(1, 202)])
        assert pending["changes"][0]["key"] != retry["changes"][0]["key"]
        return {"failure": code, "new_remote_tickets_committed_before_failure": 200,
                "local_tickets": 0, "retry_generates_new_idempotency_keys": True}


def unvalidated_report_and_oversized_snapshot():
    with lab() as (bid, pid, store, uid, rid):
        seed = proposal(bid, pid, [ticket(1)])
        store.apply_changes(rid, uid, "change-0", seed["changes"])
        # This goes through the same store method the HTTP /changes route invokes.
        invalid = remote.change_record(key="ungated", kind="report", record_id="LAB-1|combined|",
            expected_revision="", revision="audit:1",
            payload={"ticket_id": "LAB-1", "scope": "combined", "repository_id": "",
                     "content": "Untyped prose without any report sections or claim tags."})
        store.apply_changes(rid, uid, "change-0", [invalid])
        assert store.snapshot(rid, uid)["reports"][0]["content"].startswith("Untyped")
        for i in range(5):
            scope = f"branch:audit-{i}"
            change = remote.change_record(key=f"large-{i}", kind="report", record_id=f"LAB-1|{scope}|",
                expected_revision="", revision=f"audit:large-{i}",
                payload={"ticket_id": "LAB-1", "scope": scope, "repository_id": "",
                         "content": "x" * (2 * 1024 * 1024)})
            store.apply_changes(rid, uid, "change-0", [change])
        raw = json.dumps(store.snapshot(rid, uid)).encode()
        assert len(raw) > remote.MAX_RESPONSE_BYTES
        adapter = remote.HTTPRemoteAdapter("http://localhost:9876")
        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(raw)):
            try:
                adapter.pull(rid, "")
            except remote.RemoteContractError as exc:
                message = str(exc)
            else:
                raise AssertionError("Expected snapshot size refusal")
        return {"unvalidated_combined_report_accepted": True, "snapshot_bytes": len(raw),
                "client_limit_bytes": remote.MAX_RESPONSE_BYTES, "pull_error": message}


if __name__ == "__main__":
    print(json.dumps({
        "write_receipt_vs_read_cursor": write_receipt_is_not_a_read_cursor(),
        "multi_batch_partial_commit": batch_failure_leaves_remote_partial(),
        "report_trust_and_snapshot_size": unvalidated_report_and_oversized_snapshot(),
    }, indent=2))
