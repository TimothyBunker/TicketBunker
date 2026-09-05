from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import autoagent, bunkerdb, bunkerruntime, commands, config, credentials, remote
from ticketbunker.service import server as service_server
from ticketbunker.service import storage as service_storage

ROOT = Path(__file__).resolve().parent.parent


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "TB_AGENT_DISABLE": "1"},
    )
    return proc.stdout.strip()


class BunkerRuntimeTests(unittest.TestCase):
    """A ticket that lives in the Bunker database is synthesized without any .tb layout."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        home = self.root / "home"
        home.mkdir()
        self.settings = home / ".tbconfig"
        self.settings.write_text("{}\n", encoding="utf-8")
        command = f"{sys.executable} {ROOT / 'tools' / 'stub_agent.py'} {{form}}"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "TB_DATA_HOME": str(home / "data"),
                "TB_CONFIG": str(self.settings),
                "TB_AGENT_HARNESS": "command",
                "TB_AGENT_COMMAND": command,
            },
        )
        self.environment.start()
        os.environ.pop("TB_AGENT_DISABLE", None)
        self.config_patch = mock.patch.object(config, "GLOBAL_CONFIG", self.settings)
        self.config_patch.start()

        self.repo = self.root / "source"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Ada")
        git(self.repo, "config", "user.email", "ada@example.com")
        (self.repo / "README.md").write_text("bunker runtime\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "initial")
        git(self.repo, "remote", "add", "origin", "https://github.com/example/coal.git")

        created = bunkerdb.create_bunker("Engineering", project_key="ECS", project_name="Entity Component System")
        self.bunker_id = created["id"]
        self.project_id = created["projects"][0]["id"]
        configuration = bunkerdb.get_configuration(self.bunker_id, project_id=self.project_id)
        configuration = bunkerdb.replace_repository_bindings(
            self.bunker_id,
            project_id=self.project_id,
            expected_revision=configuration["repositories"]["revision"],
            repositories=[{"name": "Coal", "canonical_remote": "github.com/example/coal", "default_branch": "main"}],
        )
        binding_id = configuration["repositories"]["items"][0]["id"]
        with mock.patch.object(bunkerdb, "_fetch_repository"):
            bunkerdb.attach_repository(
                self.bunker_id, project_id=self.project_id, local_path=str(self.repo), binding_id=binding_id
            )
        self.repository_id = binding_id
        proposal = bunkerdb.propose_ticket_sync(
            self.bunker_id,
            project_id=self.project_id,
            tickets=[{
                "id": "ECS-4",
                "title": "Automatic report",
                "description": "Position state for the ECS.",
                "type": "task",
                "status": "in-progress",
                "metadata": {"source_identity": "test:ECS-4"},
            }],
            source_kind="github",
        )
        bunkerdb.apply_ticket_sync_change(self.bunker_id, proposal)
        self.context = commands.CommandContext(self.repo, transport="desktop")

    def tearDown(self) -> None:
        self.config_patch.stop()
        self.environment.stop()
        self.tmp.cleanup()

    def dispatch(self, action: str, payload: dict) -> dict:
        return commands.dispatch(action, payload, self.context)

    def start_work(self) -> str:
        branch = "Ada/ECS-4-automatic"
        git(self.repo, "checkout", "-q", "-b", branch)
        (self.repo / "position.py").write_text("POSITION = (0, 0, 0)\n", encoding="utf-8")
        git(self.repo, "add", "position.py")
        git(self.repo, "commit", "-m", "Add position state")
        return branch

    def test_synthesis_publishes_reports_into_the_bunker(self) -> None:
        branch = self.start_work()

        with mock.patch.object(autoagent, "_spawn_worker") as spawned:
            queued = self.dispatch(
                "truth.synthesize", {"project": "ECS", "ticket": "ECS-4", "bunker_id": self.bunker_id}
            )
        self.assertTrue(queued["queued"])
        spawned.assert_called_once()
        runtime = bunkerruntime.ticket_dir(self.bunker_id, "ECS-4")
        self.assertTrue((runtime / "ticket.json").is_file())
        self.assertEqual(json.loads((runtime / bunkerruntime.MARKER).read_text())["repository_id"], self.repository_id)
        self.assertFalse((self.repo / ".tb").exists())

        status = autoagent.run_worker(self.repo, debounce=0, once=True)

        last = autoagent.read_state(self.repo)["last"]
        self.assertEqual((status, last["status"]), (0, "pass"), last.get("detail"))
        self.assertFalse((self.repo / ".tb").exists())
        detail = self.dispatch("application.ticket.get", {"bunker_id": self.bunker_id, "ticket_id": "ECS-4"})
        reports = {report["scope"]: report for report in detail["reports"]}
        self.assertEqual(set(reports), {"combined", f"branch:{branch}"})
        self.assertIn("<!-- tb:verified", reports[f"branch:{branch}"]["content"])
        self.assertEqual(reports[f"branch:{branch}"]["repository_id"], self.repository_id)
        self.assertIn("Master report", reports["combined"]["content"])
        self.assertEqual(detail["truth"]["companion"]["label"], "Bunker data")
        self.assertEqual(detail["truth"]["synthesis"]["status"], "current")
        self.assertTrue(any(item["name"] == branch and item["current"] for item in detail["branches"]))

    def test_synthesis_requires_the_ticket_branch(self) -> None:
        with self.assertRaises(commands.CommandRejected) as rejected:
            self.dispatch("truth.synthesize", {"project": "ECS", "ticket": "ECS-4", "bunker_id": self.bunker_id})
        self.assertEqual(rejected.exception.code, "synthesis_branch_required")

    def test_find_bunker_ticket_matches_only_the_attached_checkout(self) -> None:
        found = bunkerdb.find_bunker_ticket(self.repo, "Ada/ECS-4-automatic")
        self.assertEqual((found["bunker_id"], found["ticket_id"], found["repository_id"]), (self.bunker_id, "ECS-4", self.repository_id))
        self.assertIsNone(bunkerdb.find_bunker_ticket(self.repo, "Ada/ECS-9-elsewhere"))
        self.assertIsNone(bunkerdb.find_bunker_ticket(self.root / "other", "Ada/ECS-4-automatic"))
        self.assertIsNone(bunkerdb.find_bunker_ticket(self.repo, "main"))

    def test_report_publication_is_revision_checked_and_skips_unchanged_reports(self) -> None:
        proposal = bunkerdb.propose_report_publication(
            self.bunker_id, ticket_id="ECS-4", repository_id=self.repository_id,
            branch="Ada/ECS-4-automatic", child="# child\n", master="# master\n",
        )
        self.assertEqual([item["action"] for item in proposal["results"]], ["created", "created"])
        self.assertEqual([change["expected_revision"] for change in proposal["changes"]], ["", ""])
        first = bunkerdb.apply_report_publication(self.bunker_id, proposal)
        self.assertEqual(first["created"], 2)

        again = bunkerdb.propose_report_publication(
            self.bunker_id, ticket_id="ECS-4", repository_id=self.repository_id,
            branch="Ada/ECS-4-automatic", child="# child\n", master="# master v2\n",
        )
        self.assertEqual({item["scope"]: item["action"] for item in again["results"]}, {"branch:Ada/ECS-4-automatic": "unchanged", "combined": "updated"})
        self.assertEqual(again["changes"][0]["expected_revision"], proposal["changes"][1]["revision"])

    def test_shared_bunker_reports_reach_the_service(self) -> None:
        store = service_storage.ServiceStore(self.root / "service.sqlite3")
        store.initialize()
        setup_token = store.issue_setup_token()
        server = service_server.serve(store, host="127.0.0.1", port=0, quiet=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.dispatch("application.profile.update", {"name": "Ada", "email": "ada@example.test", "branch_user": "Ada"})
            shared = self.dispatch(
                "application.bunker.share",
                {"bunker_id": self.bunker_id, "server_url": server.origin, "setup_token": setup_token},
            )["bunker"]
            self.assertEqual(shared["mode"], "remote")
            branch = self.start_work()
            with mock.patch.object(autoagent, "_spawn_worker"):
                self.dispatch("truth.synthesize", {"project": "ECS", "ticket": "ECS-4", "bunker_id": self.bunker_id})
            status = autoagent.run_worker(self.repo, debounce=0, once=True)
            last = autoagent.read_state(self.repo)["last"]
            self.assertEqual((status, last["status"]), (0, "pass"), last.get("detail"))

            adapter = remote.HTTPRemoteAdapter(
                server.origin,
                bearer=credentials.resolve(credentials.find(server.origin)),
                credential_reference=credentials.find(server.origin),
            )
            snapshot = adapter.pull(shared["remote"]["remote_bunker_id"], "")
            scopes = {report["scope"] for report in snapshot.reports}
            self.assertEqual(scopes, {"combined", f"branch:{branch}"})
            self.assertIn("<!-- tb:verified", next(r["content"] for r in snapshot.reports if r["scope"] != "combined"))
            self.assertEqual(bunkerdb.remote_connection(self.bunker_id)["sync_cursor"], "change-2")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
