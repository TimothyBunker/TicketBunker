"""Project context actions use shared gates, not legacy checkout removal."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import app_worker, bunkerdb, commands, remote
import test_application as application_tests


class ProjectActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.dict(os.environ, {"TB_DATA_HOME": self.temp.name})
        patch.start(); self.addCleanup(patch.stop)
        self.context = commands.CommandContext(Path(self.temp.name))
        value = self.call("application.bunker.create", name="Lab", project_key="NAV")["bunker"]
        self.bid, self.pid = value["id"], value["projects"][0]["id"]

    def call(self, action, **payload):
        return commands.dispatch(action, payload, self.context)

    def inspect(self):
        return self.call("application.bunker.project.inspect", bunker_id=self.bid, project_id=self.pid)

    def test_rename_preserves_identity_and_rejects_stale_writes(self):
        with bunkerdb._database(self.bid) as connection:
            connection.execute("UPDATE projects SET position=7 WHERE id=?", (self.pid,))
        before = self.inspect()
        proposal = bunkerdb.propose_project_edit(self.bid, self.pid, before["revision"], name="Navigation")
        self.assertEqual(proposal["change"]["payload"]["position"], 7)
        value = self.call("application.bunker.project.rename", bunker_id=self.bid, project_id=self.pid,
            expected_revision=before["revision"], name="Navigation")
        self.assertEqual(value["bunker"]["projects"][0]["id"], self.pid)
        self.assertEqual(value["bunker"]["projects"][0]["key"], "NAV")
        self.assertEqual(value["bunker"]["projects"][0]["name"], "Navigation")
        with self.assertRaises(commands.CommandRejected):
            self.call("application.bunker.project.rename", bunker_id=self.bid, project_id=self.pid,
                expected_revision=before["revision"], name="Stale")

    def test_delete_requires_confirmation_and_does_not_touch_git_folders(self):
        marker = Path(self.temp.name) / "local-source.txt"
        marker.write_text("Keep my work", encoding="utf-8")
        payload = dict(bunker_id=self.bid, project_id=self.pid, expected_revision=self.inspect()["revision"])
        with self.assertRaises(commands.CommandRejected):
            self.call("application.bunker.project.delete", **payload)
        value = self.call("application.bunker.project.delete", **payload, confirmed=True)
        self.assertEqual(value["bunker"]["project_count"], 0)
        self.assertEqual(marker.read_text(), "Keep my work")
        self.assertFalse(any(row["scope_type"] == "project" for row in bunkerdb.local_publication(self.bid)["config_documents"]))

    def test_nonempty_delete_is_rejected_even_if_content_arrives_after_preview(self):
        before = self.inspect()
        self.call("application.bunker.project.create", bunker_id=self.bid, parent_id=self.pid, key="SUB", name="Subproject")
        with self.assertRaisesRegex(commands.CommandRejected, "subprojects"):
            self.call("application.bunker.project.delete", bunker_id=self.bid, project_id=self.pid,
                expected_revision=before["revision"], confirmed=True)
        self.assertEqual(bunkerdb.get_bunker(self.bid)["project_count"], 2)

    def test_delete_preserves_repository_referenced_by_another_projects_report(self):
        other = self.call("application.bunker.project.create", bunker_id=self.bid, key="OTHER", name="Other")["bunker"]
        other_id = next(row["id"] for row in other["projects"] if row["key"] == "OTHER")
        with bunkerdb._database(self.bid) as connection:
            connection.execute("INSERT INTO repository_bindings(id,bunker_id,project_id,name,created_at,updated_at) VALUES ('repo',?,?, 'Source','','')", (self.bid, self.pid))
            connection.execute("INSERT INTO tickets(id,project_id,payload_json,updated_at) VALUES ('OTHER-1',?,'{}','')", (other_id,))
            connection.execute("INSERT INTO reports(ticket_id,scope,repository_id,content,updated_at) VALUES ('OTHER-1','branch:main','repo','Report','')")
        with self.assertRaisesRegex(commands.CommandRejected, "report still references"):
            self.call("application.bunker.project.delete", bunker_id=self.bid, project_id=self.pid,
                expected_revision=self.inspect()["revision"], confirmed=True)
        self.assertEqual(bunkerdb.get_bunker(self.bid)["project_count"], 2)

    def test_project_actions_are_worker_capabilities(self):
        for action in ("inspect", "rename", "delete"):
            self.assertIn(f"application.bunker.project.{action}", app_worker.DESKTOP_ACTIONS)


class SharedProjectActionsTests(unittest.TestCase):
    def test_rename_delete_sync_and_member_gates(self):
        lab = application_tests.SharedBunkerTests(); lab.setUp(); self.addCleanup(lab.tearDown)
        lab.as_user("owner")
        bunker = lab.dispatch("application.bunker.create", {"name": "Lab", "project_key": "NAV"})["bunker"]
        bid, pid = bunker["id"], bunker["projects"][0]["id"]
        lab.dispatch("application.bunker.share", {"bunker_id": bid, "server_url": lab.origin, "setup_token": lab.setup_token})
        link = lab.dispatch("application.bunker.invitation.create", {"bunker_id": bid})["invitation"]["link"]
        lab.as_user("member")
        joined = lab.dispatch("application.bunker.join", {"invitation": link})["bunker"]
        mbid = joined["id"]
        before = bunkerdb.inspect_project_change(mbid, pid)
        for action in ("rename", "delete"):
            with self.assertRaises(commands.CommandRejected):
                lab.dispatch(f"application.bunker.project.{action}", {"bunker_id": mbid, "project_id": pid,
                    "expected_revision": before["revision"], "name": "Forged", "confirmed": True})
        # The service independently rejects forged typed changes too.
        from ticketbunker import application
        adapter, _ = application._remote_adapter(mbid)
        for kind in ("project", "project-delete"):
            with self.assertRaises(remote.RemoteContractError):
                adapter.push(bid, "", (remote.change_record(key=kind, kind=kind, record_id=pid,
                    expected_revision=before["revision"], revision="forged", payload=before["project"]),))
        lab.as_user("owner")
        lab.dispatch("application.bunker.project.rename", {"bunker_id": bid, "project_id": pid,
            "expected_revision": before["revision"], "name": "Navigation"})
        lab.as_user("member")
        lab.dispatch("application.bunker.sync", {"bunker_id": mbid})
        self.assertEqual(bunkerdb.get_bunker(mbid)["projects"][0]["name"], "Navigation")
        lab.as_user("owner")
        current = bunkerdb.inspect_project_change(bid, pid)
        lab.dispatch("application.bunker.project.delete", {"bunker_id": bid, "project_id": pid,
            "expected_revision": current["revision"], "confirmed": True})
        lab.as_user("member")
        lab.dispatch("application.bunker.sync", {"bunker_id": mbid})
        self.assertEqual(bunkerdb.get_bunker(mbid)["project_count"], 0)
        self.assertFalse(any(row["scope_type"] == "project" for row in bunkerdb.local_publication(mbid)["config_documents"]))
