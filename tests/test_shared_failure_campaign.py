"""Two isolated replicas exercise disposable real-HTTP sharing failure modes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import test_workspace_membership as membership_fixture
from ticketbunker import application, bunkerdb, commands, remote
from ticketbunker.service import server as service_server


class SharedFailureCampaignTests(unittest.TestCase):
    def setUp(self):
        self.lab = membership_fixture.WorkspaceMembershipTests()
        self.lab.setUp()
        self.addCleanup(self.lab.doCleanups)
        self.owner = self.lab.owner
        self.member = self.lab.member
        self.remote_id = self.lab.remote_id

    def local_state(self):
        with self.lab.participant("member"):
            return bunkerdb.get_bunker(self.lab.ids["member"])

    def local_rows(self):
        with self.lab.participant("member"), bunkerdb._database(self.lab.ids["member"]) as connection:
            return {table: sorted(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))
                    for table in ("bunker", "projects", "repository_bindings", "local_repositories", "config_documents", "tickets", "reports", "remote_connection")}

    def sync(self):
        return self.lab.command("member", "application.bunker.sync")

    def ticket_change(self, key, revision, expected="", title="Simulation", ticket="ECS-9"):
        return remote.change_record(key=key, kind="ticket", record_id=ticket, expected_revision=expected, revision=revision,
            payload={"id": ticket, "project_id": "ecs", "payload": {"id": ticket, "title": title, "status": "todo"}, "source_kind": "manual"})

    def test_member_revocation_blocks_new_sync_but_does_not_destroy_offline_replica(self):
        self.lab.command("member", "application.bunker.repository.attach", project_id="ecs", path=str(self.lab.repo))
        before = self.local_state()
        member_id = before["membership"]["id"]
        # Simulate the hosting operator revoking an existing membership; the
        # product does not currently expose a member-removal command.
        connection = self.lab.service.store._connect()
        with connection:
            connection.execute("UPDATE memberships SET state='revoked' WHERE bunker_id=? AND user_id=?", (self.remote_id, member_id))
        connection.close()
        with self.assertRaises(commands.CommandRejected):
            self.sync()
        after = self.local_state()
        self.assertEqual(after["repositories"], before["repositories"])
        self.assertEqual(after["remote"]["sync_cursor"], before["remote"]["sync_cursor"])
        with self.assertRaises(remote.RemoteContractError):
            self.member.push(self.remote_id, "", (self.ticket_change("revoked-change", "v1"),))
        self.assertNotIn("ECS-9", {item["id"] for item in self.owner.pull(self.remote_id, "").tickets})

    def test_wrong_or_revoked_bearer_never_changes_replica(self):
        before = self.local_state()
        wrong = remote.HTTPRemoteAdapter(self.lab.service.origin, bearer="not-a-real-token")
        with self.lab.participant("member"), patch.object(application, "_remote_adapter", return_value=(wrong, before["remote"])):
            with self.assertRaises(commands.CommandRejected):
                commands.dispatch("application.bunker.sync", {"bunker_id": self.lab.ids["member"]}, commands.CommandContext(self.lab.repo))
        self.assertEqual(self.local_state()["remote"]["sync_cursor"], before["remote"]["sync_cursor"])
        connection = self.lab.service.store._connect()
        with connection:
            connection.execute("UPDATE credentials SET revoked_at='2026-01-01T00:00:00+00:00' WHERE user_id=?", (before["membership"]["id"],))
        connection.close()
        with self.assertRaises(commands.CommandRejected):
            self.sync()
        self.assertEqual(self.local_state()["repositories"], before["repositories"])

    def test_parallel_ticket_updates_have_one_winner_and_retry_converges(self):
        revision = ""
        for round_number in range(4):
            barrier = threading.Barrier(2)
            def publish(participant):
                change = self.ticket_change(f"race-{round_number}-{participant}", f"revision-{round_number}-{participant}", revision, f"round {round_number} {participant}")
                barrier.wait(timeout=5)
                try:
                    adapter = self.owner if participant == "owner" else self.member
                    adapter.push(self.remote_id, "", (change,))
                    return True, change
                except remote.RemoteContractError:
                    return False, change
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(publish, ("owner", "member")))
            self.assertEqual(sorted(result[0] for result in results), [False, True])
            winner = next(change for accepted, change in results if accepted)
            snapshot = self.owner.pull(self.remote_id, "")
            ticket = next(row for row in snapshot.tickets if row["id"] == "ECS-9")
            self.assertEqual(ticket["payload"]["title"], winner["payload"]["payload"]["title"])
            revision = winner["revision"]
            self.sync()
            with self.lab.participant("member"):
                local = bunkerdb.get_ticket(self.lab.ids["member"], "ECS-9")
            self.assertEqual(local["ticket"]["title"], ticket["payload"]["title"])

    def test_batch_conflict_rolls_back_earlier_ticket_in_the_same_request(self):
        self.owner.push(self.remote_id, "", (self.ticket_change("initial", "v1"),))
        before = self.owner.pull(self.remote_id, "")
        good = self.ticket_change("new-ticket", "v1", ticket="ECS-10")
        stale = self.ticket_change("stale-ticket", "v2", expected="wrong revision")
        with self.assertRaises(remote.RemoteContractError):
            self.member.push(self.remote_id, "", (good, stale))
        after = self.owner.pull(self.remote_id, "")
        self.assertEqual(after.cursor, before.cursor)
        self.assertEqual(after.tickets, before.tickets)

    def test_parallel_owner_configuration_edits_have_one_revision_winner(self):
        snapshot = self.owner.pull(self.remote_id, "")
        document = next(item for item in snapshot.config_documents if item["scope_type"] == "bunker" and item["kind"] == "ticket-fields")
        barrier = threading.Barrier(2)
        def publish(label):
            key = "configuration-" + label.replace(" ", "-")
            change = remote.change_record(key=key, kind="config-document", record_id=f"bunker:{self.remote_id}:ticket-fields", expected_revision=document["revision"], revision=key, payload={"scope_type": "bunker", "scope_id": self.remote_id, "kind": "ticket-fields", "content": {"fields": [{"key": "title", "label": label, "type": "string", "required": True}]}})
            barrier.wait(timeout=5)
            try:
                self.owner.push(self.remote_id, snapshot.cursor, (change,))
                return label
            except remote.RemoteContractError as error:
                self.assertEqual(getattr(error, "code", ""), "revision_conflict")
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, ("Owner device A", "Owner device B")))
        winner = [item for item in results if item is not None]
        self.assertEqual(len(winner), 1)
        self.sync()
        with self.lab.participant("member"):
            document = bunkerdb.get_configuration(self.lab.ids["member"])["documents"]["ticket-fields"]
        self.assertEqual(document["content"]["fields"][0]["label"], winner[0])

    def test_remote_allowlist_removal_detaches_only_mapping_not_local_files(self):
        self.lab.command("member", "application.bunker.repository.attach", project_id="ecs", path=str(self.lab.repo))
        before_bytes = (self.lab.repo / "README.md").read_bytes()
        configuration = self.lab.configuration("owner")
        self.lab.command("owner", "application.project.repositories.select", project_id="ecs", expected_revision=configuration["repositories"]["revision"], repositories=[])
        self.sync()
        self.assertEqual(self.local_state()["repositories"], [])
        self.assertEqual((self.lab.repo / "README.md").read_bytes(), before_bytes)
        with self.assertRaises(commands.CommandRejected):
            self.lab.command("member", "application.bunker.repository.attach", project_id="ecs", binding_id="coal", path=str(self.lab.repo))
        self.assertEqual(self.local_state()["ticket_count"], 1)

    def test_partial_http_snapshot_is_rejected_before_removing_local_attachments(self):
        self.lab.command("member", "application.bunker.repository.attach", project_id="ecs", path=str(self.lab.repo))
        before = self.local_state()
        complete = self.member._request("GET", f"/bunkers/{self.remote_id}/snapshot")
        before_rows = self.local_rows()
        for field in ("projects", "repository_bindings", "config_documents", "tickets", "reports", "revision", "cursor"):
            with self.subTest(field=field):
                partial = dict(complete); partial.pop(field)
                with patch.object(remote.HTTPRemoteAdapter, "_request", return_value=partial):
                    with self.assertRaises(commands.CommandRejected):
                        self.sync()
                self.assertEqual(self.local_state()["repositories"], before["repositories"])
                self.assertEqual(self.local_state()["remote"]["sync_cursor"], before["remote"]["sync_cursor"])
                self.assertEqual(self.local_rows(), before_rows)

    def test_invalid_snapshot_hierarchy_rolls_back_all_prior_upserts(self):
        snapshot = self.member.pull(self.remote_id, "")
        before = self.local_state()
        before_rows = self.local_rows()
        malformed = replace(snapshot, bunker={**snapshot.bunker, "name": "Should roll back"},
            projects=tuple({**project, "parent_id": "ecs" if project["id"] == "systems" else "systems"} for project in snapshot.projects))
        with self.lab.participant("member"):
            with self.assertRaises(bunkerdb.BunkerDatabaseError):
                bunkerdb.apply_snapshot(self.lab.ids["member"], malformed)
        self.assertEqual(self.local_state()["projects"], before["projects"])
        self.assertEqual(self.local_state()["name"], before["name"])
        self.assertEqual(self.local_rows(), before_rows)

    def test_snapshot_is_consistent_when_a_writer_commits_during_collection(self):
        store = self.lab.service.store
        # WAL permits the writer to finish while this deliberately paused
        # reader remains open. Rollback-journal mode instead blocks the writer
        # until the read transaction completes, which is also consistent.
        connection = store._connect()
        connection.execute("PRAGMA journal_mode=WAL")
        connection.close()
        before = self.owner.pull(self.remote_id, "")
        original_records = store._records
        triggered = False
        def read_with_concurrent_write(connection, bunker_id, kind):
            nonlocal triggered
            rows = original_records(connection, bunker_id, kind)
            if kind == "project" and not triggered:
                triggered = True
                self.owner.push(self.remote_id, "", (self.ticket_change("during-snapshot", "v1"),))
            return rows
        with patch.object(store, "_records", side_effect=read_with_concurrent_write):
            snapshot = self.member.pull(self.remote_id, "")
        self.assertEqual(snapshot.cursor, before.cursor)
        self.assertNotIn("ECS-9", {item["id"] for item in snapshot.tickets})
        self.assertIn("ECS-9", {item["id"] for item in self.member.pull(self.remote_id, "").tickets})

    def test_late_old_pull_cannot_replace_a_newer_local_snapshot(self):
        old = self.member.pull(self.remote_id, "")
        ticket = next(row for row in old.tickets if row["id"] == "ECS-4")
        self.owner.push(self.remote_id, "", (self.ticket_change("newer-version", "newer", expected=ticket["revision"], title="Newest shared title", ticket="ECS-4"),))
        newer = self.member.pull(self.remote_id, "")
        for initial_cursor in (old.cursor, ""):
            with self.subTest(initial_cursor=initial_cursor):
                with self.lab.participant("member"):
                    bunkerdb.apply_snapshot(self.lab.ids["member"], old)
                    with bunkerdb._database(self.lab.ids["member"]) as connection:
                        connection.execute("UPDATE remote_connection SET sync_cursor=?", (initial_cursor,))
                accepted_rows = {}
                def finish_old_pull(*_args):
                    # The newer sync finishes while the earlier network
                    # request is outstanding in another local window.
                    bunkerdb.apply_snapshot(self.lab.ids["member"], newer)
                    accepted_rows.update(self.local_rows())
                    return old
                with patch.object(remote.HTTPRemoteAdapter, "pull", side_effect=finish_old_pull):
                    with self.assertRaisesRegex(commands.CommandRejected, "newer synchronization"):
                        self.sync()
                with self.lab.participant("member"):
                    local = bunkerdb.get_ticket(self.lab.ids["member"], "ECS-4")
                self.assertEqual(local["ticket"]["title"], "Newest shared title")
                self.assertEqual(self.local_state()["remote"]["sync_cursor"], newer.cursor)
                self.assertEqual(self.local_rows(), accepted_rows)

    def test_empty_project_deletion_propagates_without_removing_git_or_tickets(self):
        self.lab.command("member", "application.bunker.repository.attach", project_id="ecs", path=str(self.lab.repo))
        created = self.lab.command("owner", "application.bunker.project.create", key="TEMP", name="Temporary simulation")
        project = next(item for item in created["bunker"]["projects"] if item["key"] == "TEMP")
        self.sync()
        self.assertIn(project["id"], {item["id"] for item in self.local_state()["projects"]})
        inspected = self.lab.command("owner", "application.bunker.project.inspect", project_id=project["id"])
        self.lab.command("owner", "application.bunker.project.delete", project_id=project["id"], expected_revision=inspected["revision"], confirmed=True)
        self.sync()
        state = self.local_state()
        self.assertNotIn(project["id"], {item["id"] for item in state["projects"]})
        self.assertEqual(state["ticket_count"], 1)
        self.assertEqual(state["repositories"][0]["local_path"], str(self.lab.repo))
        self.assertTrue((self.lab.repo / "README.md").is_file())
        with self.lab.participant("member"), bunkerdb._database(self.lab.ids["member"]) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM config_documents WHERE scope_id=?", (project["id"],)).fetchone()[0], 0)

    def test_server_restart_retains_authentication_and_duplicate_join_is_rejected(self):
        self.owner.push(self.remote_id, "", (self.ticket_change("before-restart", "v1"),))
        self.sync()
        before = self.local_state()
        service = self.lab.service
        port = service.server.server_address[1]
        service.server.shutdown(); service.server.server_close(); service.thread.join(timeout=5)
        service.server = service_server.serve(service.store, host="127.0.0.1", port=port, quiet=True)
        service.thread = threading.Thread(target=service.server.serve_forever, daemon=True)
        service.thread.start()
        self.sync()
        self.assertEqual(self.local_state()["remote"]["sync_cursor"], before["remote"]["sync_cursor"])
        self.assertIn("ECS-9", {item["id"] for item in self.member.pull(self.remote_id, "").tickets})
        invitation = self.owner.create_invitation(self.remote_id)
        link = service.invitation_link(self.remote_id, invitation["token"])
        enrolled = self.member.enroll(link)
        with self.lab.participant("member"):
            with self.assertRaisesRegex(bunkerdb.BunkerDatabaseError, "already connected"):
                bunkerdb.create_replica(link, enrolled)
        self.assertEqual(self.local_state()["remote"]["sync_cursor"], before["remote"]["sync_cursor"])


if __name__ == "__main__":
    unittest.main()
