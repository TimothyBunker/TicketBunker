from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

from ticketbunker import cli, remote
from ticketbunker.service import server as service_server
from ticketbunker.service import storage as service_storage


def publication(bunker_id: str = "local-bunker") -> dict:
    return {
        "bunker": {"id": bunker_id, "name": "Engineering"},
        "projects": [
            {"id": "systems", "parent_id": "", "key": "SYS", "name": "Systems", "position": 0},
            {"id": "ecs", "parent_id": "systems", "key": "ECS", "name": "Entity Component System"},
        ],
        "repository_bindings": [
            {
                "id": "coal",
                "project_id": "ecs",
                "name": "Coal",
                "canonical_remote": "github.com/example/coal",
                "default_branch": "main",
            }
        ],
        "config_documents": [
            {
                "scope_type": "bunker",
                "scope_id": bunker_id,
                "kind": "ticket-fields",
                "revision": "local:1",
                "content": {"fields": [{"key": "title", "label": "Title", "type": "string", "required": True}]},
            },
            {
                "scope_type": "project",
                "scope_id": "ecs",
                "kind": "integrations",
                "revision": "local:1",
                "content": {"providers": {}},
            },
        ],
        "tickets": [
            {
                "id": "ECS-4",
                "project_id": "ecs",
                "payload": {"id": "ECS-4", "title": "Position System", "status": "Todo"},
                "source_kind": "github",
            }
        ],
        "reports": [
            {"ticket_id": "ECS-4", "scope": "combined", "content": "# Combined\n"},
            {
                "ticket_id": "ECS-4",
                "scope": "branch:tim/ECS-4-position",
                "repository_id": "coal",
                "content": "# Branch\n",
            },
        ],
    }


class ReferenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = service_storage.ServiceStore(Path(self.temporary.name) / "service.sqlite3")
        self.store.initialize()
        self.setup_token = self.store.issue_setup_token()
        self.server = service_server.serve(self.store, host="127.0.0.1", port=0, quiet=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = self.server.origin

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def adapter(self, bearer: str = "") -> remote.HTTPRemoteAdapter:
        return remote.HTTPRemoteAdapter(
            self.origin, bearer=bearer, credential_reference="local:test" if bearer else ""
        )

    def owner(self) -> tuple[remote.HTTPRemoteAdapter, str]:
        result = self.adapter().setup(
            self.setup_token, {"display_name": "Tim", "email": "tim@example.test"}
        )
        return self.adapter(result["credential"]), result["credential"]

    def invitation_link(self, bunker_id: str, token: str) -> remote.JoinInvitation:
        return remote.JoinInvitation.parse(
            f"ticketbunker://join?server={quote(self.origin, safe='')}"
            f"&bunker={quote(bunker_id, safe='')}&invite={quote(token, safe='')}"
        )

    def test_capabilities_expose_only_protocol_facts(self) -> None:
        value = self.adapter().capabilities()

        self.assertEqual(value["protocol"], "v1")
        self.assertIn("invitation-credentials", value["features"])
        self.assertNotIn("bunkers", value)

    def test_setup_token_is_single_use_and_makes_the_first_owner(self) -> None:
        self.assertIsNone(self.store.issue_setup_token())
        owner, credential = self.owner()

        with self.assertRaises(remote.RemoteContractError) as failure:
            self.adapter().setup(self.setup_token, {"display_name": "Again"})
        self.assertIn("already has an owner", str(failure.exception))
        with self.assertRaises(remote.RemoteContractError) as wrong:
            self.adapter().setup("not-the-token", {"display_name": "Again"})
        self.assertIn("already has an owner", str(wrong.exception))
        self.assertIsNone(self.store.issue_setup_token())
        self.assertTrue(self.store.issue_setup_token(force=True))
        self.assertEqual(self.store.authenticate(credential)["display_name"], "Tim")
        self.assertIsNone(self.store.authenticate("nope"))
        self.assertNotIn(credential, json.dumps(self.store.setup_state()))

    def test_wrong_setup_token_is_rejected_before_setup(self) -> None:
        with self.assertRaises(remote.RemoteContractError) as failure:
            self.adapter().setup("not-the-token", {"display_name": "Mallory"})
        self.assertIn("setup token is not valid", str(failure.exception))

    def test_publish_invite_redeem_snapshot_flow(self) -> None:
        owner, _ = self.owner()

        snapshot = owner.publish(publication())

        bunker_id = str(snapshot.bunker["id"])
        self.assertEqual(bunker_id, "local-bunker")
        self.assertEqual(snapshot.bunker["name"], "Engineering")
        self.assertIn("members:invite", snapshot.capabilities)
        self.assertEqual([item["role"] for item in snapshot.members], ["owner"])
        self.assertEqual({item["id"] for item in snapshot.projects}, {"systems", "ecs"})
        bunker_documents = [
            item for item in snapshot.config_documents if item["scope_type"] == "bunker"
        ]
        self.assertEqual(bunker_documents[0]["scope_id"], bunker_id)
        self.assertEqual(len(snapshot.reports), 2)
        self.assertEqual(snapshot.cursor, "change-0")

        invitation = owner.create_invitation(bunker_id)
        self.assertEqual(invitation["role"], "member")
        self.assertTrue(invitation["token"])
        listed = owner.list_invitations(bunker_id)
        self.assertEqual([item["state"] for item in listed], ["pending"])
        self.assertNotIn(invitation["token"], json.dumps(listed))

        link = self.invitation_link(bunker_id, invitation["token"])
        inspected = self.adapter().inspect(link)
        self.assertEqual(inspected["bunker"]["name"], "Engineering")
        self.assertEqual(inspected["role"], "member")

        enrollment = self.adapter().enroll(
            link, {"display_name": "Ada", "email": "ada@example.test"}
        )
        self.assertTrue(enrollment.issued_credential)
        self.assertEqual(enrollment.snapshot.capabilities, tuple(remote.capabilities_for("member")))
        self.assertEqual(
            sorted(item["role"] for item in enrollment.snapshot.members), ["member", "owner"]
        )
        self.assertEqual(enrollment.snapshot.tickets[0]["payload"]["title"], "Position System")

        with self.assertRaises(remote.RemoteContractError) as reused:
            self.adapter().enroll(link, {"display_name": "Eve"})
        self.assertIn("already used", str(reused.exception))

        member = self.adapter(enrollment.issued_credential)
        with self.assertRaises(remote.RemoteContractError) as forbidden:
            member.create_invitation(bunker_id)
        self.assertIn("Only the Bunker owner", str(forbidden.exception))
        self.assertEqual(len(member.members(bunker_id)), 2)
        self.assertEqual(member.pull(bunker_id, "").bunker["id"], bunker_id)
        self.assertEqual(owner.list_invitations(bunker_id)[0]["state"], "redeemed")

        with self.assertRaises(remote.RemoteContractError) as anonymous:
            self.adapter().pull(bunker_id, "")
        self.assertIn("Sign in", str(anonymous.exception))

        stranger = self.adapter(self.adapter().enroll(
            self.invitation_link(bunker_id, owner.create_invitation(bunker_id)["token"]),
            {"display_name": "Lin"},
        ).issued_credential)
        other = owner.publish(publication("second"))
        with self.assertRaises(remote.RemoteContractError) as hidden:
            stranger.pull(str(other.bunker["id"]), "")
        self.assertIn("no longer available", str(hidden.exception))

    def test_revoked_and_expired_invitations_cannot_be_redeemed(self) -> None:
        owner, _ = self.owner()
        bunker_id = str(owner.publish(publication()).bunker["id"])
        revoked = owner.create_invitation(bunker_id)
        owner.revoke_invitation(bunker_id, revoked["id"])
        with self.assertRaises(remote.RemoteContractError) as failure:
            self.adapter().inspect(self.invitation_link(bunker_id, revoked["token"]))
        self.assertIn("revoked", str(failure.exception))
        self.assertEqual(owner.list_invitations(bunker_id)[0]["state"], "revoked")

        expired = owner.create_invitation(bunker_id, expires_in=60)
        connection = self.store._connect()
        with connection:
            connection.execute(
                "UPDATE invitations SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                (expired["id"],),
            )
        connection.close()
        with self.assertRaises(remote.RemoteContractError) as late:
            self.adapter().enroll(self.invitation_link(bunker_id, expired["token"]), {"display_name": "Ada"})
        self.assertIn("expired", str(late.exception))
        with self.assertRaises(remote.RemoteContractError):
            owner.create_invitation(bunker_id, expires_in=5)

    def test_changes_are_capability_gated_revision_checked_and_idempotent(self) -> None:
        owner, _ = self.owner()
        bunker_id = str(owner.publish(publication()).bunker["id"])
        member = self.adapter(self.adapter().enroll(
            self.invitation_link(bunker_id, owner.create_invitation(bunker_id)["token"]),
            {"display_name": "Ada"},
        ).issued_credential)
        document = remote.change_record(
            key="change-1",
            kind="config-document",
            record_id=f"bunker:{bunker_id}:ticket-fields",
            expected_revision="local:1",
            revision="shared:abc",
            payload={
                "scope_type": "bunker",
                "scope_id": bunker_id,
                "kind": "ticket-fields",
                "content": {"fields": []},
            },
        )

        with self.assertRaises(remote.RemoteContractError) as forbidden:
            member.push(bunker_id, "change-0", (document,))
        self.assertIn("Only the Bunker owner", str(forbidden.exception))

        accepted = owner.push(bunker_id, "change-0", (document,))
        self.assertEqual(accepted["results"][0], {"key": "change-1", "ok": True, "revision": "shared:abc"})
        self.assertEqual(accepted["cursor"], "change-1")
        replayed = owner.push(bunker_id, "change-1", (document,))
        self.assertTrue(replayed["results"][0]["replayed"])
        self.assertEqual(replayed["cursor"], "change-1")

        stale = remote.change_record(
            key="change-2",
            kind="config-document",
            record_id=f"bunker:{bunker_id}:ticket-fields",
            expected_revision="local:1",
            revision="shared:def",
            payload=document["payload"],
        )
        with self.assertRaises(remote.RemoteContractError) as conflict:
            owner.push(bunker_id, "change-1", (stale,))
        self.assertIn("changed elsewhere", str(conflict.exception))

        project = remote.change_record(
            key="change-3",
            kind="project",
            record_id="render",
            expected_revision="",
            revision="1",
            payload={"id": "render", "parent_id": "systems", "key": "REN", "name": "Rendering"},
        )
        owner.push(bunker_id, "change-1", (project,))
        duplicate = remote.change_record(
            key="change-4",
            kind="project",
            record_id="render-2",
            expected_revision="",
            revision="1",
            payload={"id": "render-2", "key": "REN", "name": "Rendering again"},
        )
        with self.assertRaises(remote.RemoteContractError) as taken:
            owner.push(bunker_id, "change-2", (duplicate,))
        self.assertIn("already exists", str(taken.exception))

        rows = [
            {"id": "coal", "project_id": "ecs", "name": "Coal", "canonical_remote": "github.com/example/coal", "default_branch": "main"},
            {"id": "flux", "project_id": "ecs", "name": "Flux", "canonical_remote": "github.com/example/flux", "default_branch": "main"},
        ]
        current = remote.repository_list_revision(rows[:1])
        bindings = remote.change_record(
            key="change-5",
            kind="repository-bindings",
            record_id="ecs",
            expected_revision=current,
            revision=remote.repository_list_revision(rows),
            payload=rows,
        )
        owner.push(bunker_id, "change-2", (bindings,))
        latest = owner.pull(bunker_id, "")
        self.assertEqual({item["id"] for item in latest.repository_bindings}, {"coal", "flux"})
        self.assertEqual(latest.cursor, "change-3")
        self.assertEqual({item["key"] for item in latest.projects}, {"SYS", "ECS", "REN"})

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        connection.request(
            "GET",
            f"/api/v1/bunkers/{bunker_id}/snapshot?after=change-1",
            headers={"Authorization": f"Bearer {member.bearer}"},
        )
        response = connection.getresponse()
        after = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual([item["kind"] for item in after["changes"]], ["project", "repository-bindings"])

    def test_members_may_publish_tickets_but_not_configuration(self) -> None:
        owner, _ = self.owner()
        bunker_id = str(owner.publish(publication()).bunker["id"])
        member = self.adapter(self.adapter().enroll(
            self.invitation_link(bunker_id, owner.create_invitation(bunker_id)["token"]),
            {"display_name": "Ada"},
        ).issued_credential)
        ticket = remote.change_record(
            key="ticket-1",
            kind="ticket",
            record_id="ECS-7",
            expected_revision="",
            revision="t:first",
            payload={
                "id": "ECS-7",
                "project_id": "ecs",
                "payload": {"id": "ECS-7", "title": "Seven", "status": "todo"},
                "source_kind": "github",
                "source_reference": "https://github.com/example/coal/issues/7",
            },
        )

        accepted = member.push(bunker_id, "change-0", (ticket,))

        self.assertEqual(accepted["results"][0]["revision"], "t:first")
        latest = owner.pull(bunker_id, "")
        self.assertIn("ECS-7", {item["id"] for item in latest.tickets})
        stale = remote.change_record(
            key="ticket-2", kind="ticket", record_id="ECS-7",
            expected_revision="", revision="t:second", payload=ticket["payload"],
        )
        with self.assertRaises(remote.RemoteContractError) as conflict:
            owner.push(bunker_id, "change-1", (stale,))
        self.assertIn("Sync again", str(conflict.exception))
        mixed = remote.change_record(
            key="config-1", kind="config-document",
            record_id=f"bunker:{bunker_id}:ticket-fields", expected_revision="local:1",
            revision="shared:x",
            payload={"scope_type": "bunker", "scope_id": bunker_id, "kind": "ticket-fields", "content": {"fields": []}},
        )
        with self.assertRaises(remote.RemoteContractError) as forbidden:
            member.push(bunker_id, "change-1", (ticket, mixed))
        self.assertIn("Only the Bunker owner", str(forbidden.exception))

    def test_transport_bounds_bodies_and_hides_internal_detail(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        connection.putrequest("POST", "/api/v1/setup")
        connection.putheader("Content-Length", str(service_server.MAX_BODY_BYTES + 1))
        connection.putheader("Content-Type", "application/json")
        connection.endheaders()
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 413)
        self.assertEqual(body["code"], "request_too_large")

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        connection.request("GET", "/api/v1/nowhere")
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 404)
        self.assertEqual(body["code"], "not_found")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")

    def test_serve_command_is_registered(self) -> None:
        with self.assertRaises(SystemExit) as exit_state:
            cli.main(["serve", "--help"])
        self.assertEqual(exit_state.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
