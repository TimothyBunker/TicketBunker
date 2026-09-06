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

from ticketbunker import autoagent, bunkerdb, bunkerruntime, commands, config, credentials, layout, remote
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

    def test_coworker_reads_bunker_ticket(self) -> None:
        self.start_work()
        value = self.dispatch(
            "coworker.state",
            {"project": "ECS", "ticket": "ECS-4", "bunker_id": self.bunker_id, "category": "who", "scope": "combined"},
        )
        self.assertEqual(value["ticket"], "ECS-4")
        self.assertEqual(value["messages"], [])
        self.assertTrue(bunkerruntime.ticket_dir(self.bunker_id, "ECS-4").is_dir())

    def test_checks_travel_from_configuration_to_the_runtime_marker(self) -> None:
        from ticketbunker import checks

        (self.repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        detected = self.dispatch("application.project.checks.detect", {"bunker_id": self.bunker_id, "project_id": self.project_id})
        self.assertEqual([item["tool"] for item in detected["suggestions"]], ["pytest"])
        self.assertEqual(checks.detect(self.repo)[0]["test"], "python3 -m pytest -x -q")

        self.assertEqual(bunkerruntime.materialize(self.bunker_id, "ECS-4")["checks"], {})
        published = self.dispatch(
            "application.bunker.configuration.replace",
            {"bunker_id": self.bunker_id, "project_id": self.project_id, "kind": "checks", "expected_revision": "",
             "content": {"build": "", "test": "python3 -m pytest -x -q", "timeout_seconds": 120}},
        )
        self.assertEqual(published["documents"]["checks"]["content"]["test"], "python3 -m pytest -x -q")
        info = bunkerruntime.materialize(self.bunker_id, "ECS-4")
        self.assertEqual(info["checks"]["test"], "python3 -m pytest -x -q")
        self.assertEqual(json.loads((info["bunker"] / bunkerruntime.MARKER).read_text())["checks"]["timeout_seconds"], 120)
        with self.assertRaises(commands.CommandRejected):
            self.dispatch(
                "application.bunker.configuration.replace",
                {"bunker_id": self.bunker_id, "project_id": self.project_id, "kind": "checks",
                 "expected_revision": published["documents"]["checks"]["revision"], "content": {"build": "make", "timeout_seconds": 5}},
            )

    def test_ticket_checks_override_project_and_discovery_fills_in(self) -> None:
        from ticketbunker import checks

        (self.repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        # Nothing configured: no checks, source none.
        self.assertEqual(bunkerruntime.effective_checks(self.bunker_id, self.project_id, "ECS-4", str(self.repo))["source"], "none")
        # Discovery on, nothing configured: the manifest's commands apply.
        self.dispatch(
            "application.bunker.configuration.replace",
            {"bunker_id": self.bunker_id, "project_id": self.project_id, "kind": "checks", "expected_revision": "",
             "content": {"build": "", "test": "", "discover": True, "timeout_seconds": 90}},
        )
        found = bunkerruntime.effective_checks(self.bunker_id, self.project_id, "ECS-4", str(self.repo))
        self.assertEqual((found["source"], found["test"], found["timeout_seconds"]), ("discovered", "python3 -m pytest -x -q", 90))
        # A ticket override wins over everything and rides in the same document.
        value = self.dispatch("application.ticket.checks.set", {"bunker_id": self.bunker_id, "ticket_id": "ECS-4", "build": "make position", "test": ""})
        self.assertEqual((value["checks"]["source"], value["checks"]["build"]), ("ticket", "make position"))
        detail = self.dispatch("application.ticket.get", {"bunker_id": self.bunker_id, "ticket_id": "ECS-4"})
        self.assertEqual(detail["checks"]["source"], "ticket")
        info = bunkerruntime.materialize(self.bunker_id, "ECS-4")
        self.assertEqual(info["checks"]["build"], "make position")
        # Clearing the override falls back to discovery again.
        cleared = self.dispatch("application.ticket.checks.set", {"bunker_id": self.bunker_id, "ticket_id": "ECS-4", "build": "", "test": ""})
        self.assertEqual(cleared["checks"]["source"], "discovered")
        # Agent discovery parses one JSON object and refuses nonsense.
        self.assertEqual(checks._parse_discovery('Here you go:\n```json\n{"build": "cmake --build b", "test": "ctest", "reason": "CMakeLists.txt"}\n```'), {"build": "cmake --build b", "test": "ctest", "reason": "CMakeLists.txt"})
        self.assertIsNone(checks._parse_discovery("I could not tell."))
        fake = mock.Mock(returncode=0, stdout=b'{"build": "make", "test": "make test", "reason": "Makefile"}', stderr=b"")
        with mock.patch.object(checks, "detect", return_value=[]), \
             mock.patch("ticketbunker.agent_runtime.state", return_value={"ready": True, "enabled": True, "runtime": {"path": "/bin/pi"}, "provider": "p", "model": "m"}), \
             mock.patch("ticketbunker.checks._run_process", return_value=fake) as run:
            value = self.dispatch("application.checks.discover", {"bunker_id": self.bunker_id, "ticket_id": "ECS-4"})
        self.assertEqual((value["build"], value["scope"]), ("make", "ticket"))
        self.assertIn("ECS-4", run.call_args.args[0][-1])

    def test_coworker_names_missing_database_without_bunker(self) -> None:
        with self.assertRaises(commands.CommandRejected) as caught:
            self.dispatch("coworker.state", {"project": "", "ticket": "ECS-4", "category": "who", "scope": "combined"})
        self.assertEqual(caught.exception.code, "ticket_database_missing")

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

    def test_fresh_runtime_is_seeded_from_accepted_reports(self) -> None:
        import shutil

        branch = self.start_work()
        with mock.patch.object(autoagent, "_spawn_worker"):
            self.dispatch("truth.synthesize", {"project": "ECS", "ticket": "ECS-4", "bunker_id": self.bunker_id})
        self.assertEqual(autoagent.run_worker(self.repo, debounce=0, once=True), 0)
        runtime = bunkerruntime.ticket_dir(self.bunker_id, "ECS-4")
        shutil.rmtree(runtime)

        # Another machine, an imported copy, or a replica: the database has
        # the reports and the runtime has nothing yet.
        info = bunkerruntime.materialize(self.bunker_id, "ECS-4")
        child = layout.child_report(info["bunker"], branch)
        self.assertTrue(child.is_file())
        self.assertIn("<!-- tb:verified", child.read_text(encoding="utf-8"))
        self.assertTrue(layout.paths(info["bunker"])["master"].is_file())
        value = self.dispatch(
            "coworker.state",
            {"project": "ECS", "ticket": "ECS-4", "bunker_id": self.bunker_id, "category": "what", "scope": branch},
        )
        self.assertEqual(value["ticket"], "ECS-4")

        # A working copy that already exists is left alone.
        child.write_text("# local working copy\n", encoding="utf-8")
        bunkerruntime.materialize(self.bunker_id, "ECS-4")
        self.assertEqual(child.read_text(encoding="utf-8"), "# local working copy\n")

    def test_bunker_citations_resolve_however_the_agent_spells_them(self) -> None:
        from ticketbunker.processors import anchorcheck

        info = bunkerruntime.materialize(self.bunker_id, "ECS-4")
        bunker = info["bunker"]
        text = (
            "## Who\n"
            "- [real] The ticket names its assignee (@runtime/ECS-4_bunker/ticket.json).\n"
            f"- [real] The ticket names its reporter ({bunker}/ticket.json).\n"
            "- [real] Observations exist (.tb/ECS-4_bunker/observations/branch_resolution.json).\n"
            "- [real] The plain form works (ticket.json).\n"
            "- [real] A missing file still fails (observations/nothing.json).\n"
            f"- [real] The form is not evidence ({bunker}/sessions/tim__ECS-4.form.md).\n"
            "- [real] Nor is the prompt (tb.md).\n"
        )
        (bunker / "observations" / "branch_resolution.json").write_text("{}\n", encoding="utf-8")
        (bunker / "sessions").mkdir(exist_ok=True)
        (bunker / "sessions" / "tim__ECS-4.form.md").write_text("## Who\n", encoding="utf-8")
        (bunker / "tb.md").write_text("prompt\n", encoding="utf-8")

        errors = anchorcheck.verify(text, self.repo, ["main"], bunker)

        self.assertEqual(len(errors), 3, errors)
        self.assertIn("observations/nothing.json", errors[0])
        self.assertIn("form.md", errors[1])
        self.assertIn("tb.md", errors[2])

    def test_a_verified_report_the_bunker_did_not_accept_is_shared_without_another_run(self) -> None:
        branch = self.start_work()
        with mock.patch.object(autoagent, "_spawn_worker"):
            self.dispatch("truth.synthesize", {"project": "ECS", "ticket": "ECS-4", "bunker_id": self.bunker_id})
        self.assertEqual(autoagent.run_worker(self.repo, debounce=0, once=True), 0)
        # Simulate the service having refused the publication: the verified
        # child exists locally, nothing was recorded.
        (self.repo / ".git" / autoagent.STATE_DIR / autoagent.FINGERPRINT_FILE).unlink()
        with bunkerdb._database(self.bunker_id) as connection:
            connection.execute("DELETE FROM reports WHERE ticket_id = 'ECS-4'")

        with mock.patch.object(autoagent, "_spawn_worker"):
            self.dispatch("truth.synthesize", {"project": "ECS", "ticket": "ECS-4", "bunker_id": self.bunker_id})
        with mock.patch("ticketbunker.adapters.harness.run_session", side_effect=AssertionError("the provider must not run")):
            status = autoagent.run_worker(self.repo, debounce=0, once=True)

        last = autoagent.read_state(self.repo)["last"]
        self.assertEqual((status, last["status"]), (0, "pass"), last.get("detail"))
        self.assertIn("provider was not called", last["detail"])
        detail = self.dispatch("application.ticket.get", {"bunker_id": self.bunker_id, "ticket_id": "ECS-4"})
        self.assertEqual({report["scope"] for report in detail["reports"]}, {"combined", f"branch:{branch}"})

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
