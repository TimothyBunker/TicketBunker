import json
import os
import tempfile
import unittest
from unittest import mock

from pathlib import Path

from ticketbunker import app_worker, application, bunkerdb, pipelines


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = mock.patch.dict(os.environ, {"TB_DATA_HOME": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.bunker = bunkerdb.create_bunker("Pipeline lab", project_key="LAB")
        self.bid = self.bunker["id"]

    def row(self, value, identity):
        return next(row for row in value["rows"] if row["id"] == identity)

    def remote(self):
        with bunkerdb._database(self.bid) as db:
            db.execute("UPDATE bunker SET mode='remote'")
            db.execute("""INSERT INTO remote_connection(singleton,server_url,remote_bunker_id,state,
                       credential_reference,config_revision,sync_cursor,last_synced_at)
                       VALUES(1,'https://service.example','shared','ready','secret-reference','r1','change-1','2026-09-19T12:00:00Z')""")

    def operation(self, identity="op", state="unknown", count=0, error=""):
        changes = [{"key": "private-idempotency-key", "kind": "report", "record_id": "LAB-1|branch:work|repo",
                    "payload": {"ticket_id": "LAB-1", "scope": "branch:work", "repository_id": "repo", "content": "PRIVATE REPORT BODY"}}]
        with bunkerdb._database(self.bid) as db:
            db.execute("""INSERT INTO publication_operations(id,fingerprint,changes_json,accepted_count,
                       state,created_at,error,server_url,remote_bunker_id) VALUES(?,?,?,?,?,'2026-09-19T12:01:00Z',?,?,?)""",
                       (identity, identity, json.dumps(changes), count, state, error, "https://service.example", "shared"))

    def test_local_view_has_no_remote_action_and_does_not_contact_adapters(self):
        with mock.patch.object(application, "_remote_adapter", side_effect=AssertionError("network forbidden")):
            value = application.command_bunker_pipelines(None, {"bunker_id": self.bid})
        self.assertEqual(value["authority"], "device")
        self.assertFalse(any(row.get("action") == "sync" for row in value["rows"]))
        self.assertEqual(self.row(value, "git")["state"], "unknown")
        self.assertEqual(self.row(value, "reports")["result"], "No saved reports")

    def test_read_is_available_through_the_desktop_worker(self):
        value = app_worker.dispatch_request({"id": "inspect", "action": "application.bunker.pipelines",
                                            "payload": {"bunker_id": self.bid}}, default_root=Path(self.temp.name))
        self.assertTrue(value["ok"], value)
        self.assertEqual(value["authority"], "device")

    def test_write_receipt_and_read_snapshot_remain_distinct(self):
        self.remote()
        with bunkerdb._database(self.bid) as db:
            db.execute("UPDATE remote_connection SET last_write_at='2026-09-19T13:00:00Z',last_write_cursor='change-2'")
        value = pipelines.inspect(self.bid)
        self.assertEqual(self.row(value, "shared-refresh")["recorded_at"], "2026-09-19T12:00:00Z")
        self.assertEqual(self.row(value, "shared-saves")["recorded_at"], "2026-09-19T13:00:00Z")
        self.assertNotIn("current", [row["state"] for row in value["rows"]])

    def test_unknown_partial_and_conflicted_saves_are_not_claimed_running_or_saved(self):
        self.remote()
        self.operation("lost")
        self.operation("conflict", "conflict", 1, "Revision changed")
        before = bunkerdb.pending_publications(self.bid)
        value = pipelines.inspect(self.bid)
        self.assertEqual(value["pending_count"], 2)
        self.assertEqual({op["state"] for op in value["operations"]}, {"unknown", "conflict"})
        self.assertEqual(value["operations"][0]["accepted"], 1)
        self.assertEqual(value["operations"][0]["targets"][0]["ticket_id"], "LAB-1")
        self.assertEqual(before, bunkerdb.pending_publications(self.bid))
        encoded = json.dumps(value)
        for private in ("PRIVATE REPORT BODY", "private-idempotency-key", "secret-reference", "changes_json", "receipt_json"):
            self.assertNotIn(private, encoded)
        self.assertEqual(bunkerdb.get_bunker(self.bid)["pending_saves"], 2)

    def test_old_destination_and_superseded_intent_are_explicit(self):
        self.remote()
        self.operation("old")
        self.operation("replaced", "complete", 0, "Superseded by saved operation new")
        with bunkerdb._database(self.bid) as db:
            db.execute("UPDATE remote_connection SET server_url='https://new.example'")
        value = pipelines.inspect(self.bid)
        self.assertFalse(value["operations"][0]["same_destination"])
        self.assertEqual(value["operations"][1]["state"], "superseded")
        self.assertEqual(value["pending_count"], 1)

    def test_journal_window_is_bounded_but_pending_count_is_not(self):
        self.remote()
        for index in range(52):
            self.operation(str(index))
        value = pipelines.inspect(self.bid)
        self.assertEqual(len(value["operations"]), 50)
        self.assertEqual(value["pending_count"], 52)
        self.assertTrue(value["operations_truncated"])

    def test_interrupted_tracker_import_is_not_presented_as_active(self):
        pid = self.bunker["projects"][0]["id"]
        bunkerdb.begin_ticket_sync(self.bid, pid)
        row = self.row(pipelines.inspect(self.bid), "tracker:" + pid)
        self.assertEqual(row["result"], "Completion not recorded")
        self.assertEqual(row["state"], "pending")


if __name__ == "__main__":
    unittest.main()
