from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import autoagent, cli, config, handlers, layout
from ticketbunker.ledger import Ledger
from tools.stub_agent import fill


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


class AutomaticAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "source"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Ada")
        git(self.repo, "config", "user.email", "ada@example.com")
        (self.repo / "README.md").write_text("automatic agent\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "initial")
        self.bunker = self.repo / ".tb" / "ECS-4_bunker"
        layout.init_dirs(self.bunker)
        handlers.intake_ticket(
            self.bunker,
            {
                "id": "ECS-4",
                "key": "ECS-4",
                "title": "Automatic report",
                "type": "task",
                "status": "in-progress",
                "repo": str(self.repo),
                "assigned_branches": [],
            },
            Ledger(layout.paths(self.bunker)["ledger"]),
        )
        git(self.repo, "switch", "-c", "Ada/ECS-4-automatic")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_install_covers_git_lifecycle_and_restores_existing_hook(self) -> None:
        hooks = self.repo / ".git" / "hooks"
        existing = hooks / "post-commit"
        original_text = "#!/bin/sh\necho existing\n"
        existing.write_text(original_text, encoding="utf-8")
        existing.chmod(0o755)

        result = autoagent.install(self.repo)

        self.assertEqual(result["installed"], list(autoagent.HOOK_NAMES))
        self.assertEqual(result["preserved"], ["post-commit"])
        for name in autoagent.HOOK_NAMES:
            text = (hooks / name).read_text(encoding="utf-8")
            self.assertIn(autoagent.HOOK_MARKER, text)
            self.assertIn(f"--event {name}", text)
        self.assertEqual(
            (hooks / "post-commit.ticketbunker-original").read_text(encoding="utf-8"),
            original_text,
        )

        removed = autoagent.uninstall(self.repo)

        self.assertEqual(removed["removed"], list(autoagent.HOOK_NAMES))
        self.assertEqual(removed["restored"], ["post-commit"])
        self.assertEqual(existing.read_text(encoding="utf-8"), original_text)

    def test_repeated_requests_coalesce_at_the_same_head(self) -> None:
        first = autoagent.enqueue(self.repo, "viewer-synthesis", spawn=False)
        second = autoagent.enqueue(self.repo, "cli-synthesis", spawn=False)

        self.assertTrue(first["queued"])
        self.assertTrue(second["queued"])
        queued = autoagent.read_state(self.repo)["queued"]
        self.assertEqual(queued["branch"], "Ada/ECS-4-automatic")
        self.assertEqual(queued["events"], ["cli-synthesis", "viewer-synthesis"])
        self.assertGreater(queued["generation"], first["generation"])

    def test_indexed_sparse_ticket_can_queue_before_its_body_is_loaded(self) -> None:
        (self.repo / layout.LOCATOR_FILE).write_text(
            json.dumps({"project": "ECS", "bunker_repository": "unused"}),
            encoding="utf-8",
        )
        scope = self.repo / ".tb" / "data" / "projects" / "ECS" / "tickets"
        scope.mkdir(parents=True)
        (scope / "index.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "project": {"key": "ECS", "name": "ECS"},
                    "tickets": [{"id": "ECS-4"}],
                }
            ),
            encoding="utf-8",
        )

        resolved = autoagent.resolve_bunker(self.repo, "Ada/ECS-4-automatic")

        self.assertEqual(resolved.name, "ECS-4")
        self.assertTrue(resolved.parent.samefile(scope))
        self.assertFalse(resolved.exists())

    def test_event_arriving_during_worker_exit_is_not_stranded(self) -> None:
        state = self.repo / ".git" / autoagent.STATE_DIR
        state.mkdir(parents=True)

        def final_empty_claim(_repo: Path):
            (state / "queue.json").write_text('{"generation": 2}\n', encoding="utf-8")
            return None

        with mock.patch.object(autoagent, "_claim", side_effect=final_empty_claim):
            with mock.patch.object(autoagent, "_spawn_worker") as spawned:
                status = autoagent.run_worker(self.repo, debounce=0)

        self.assertEqual(status, 0)
        spawned.assert_called_once_with(self.repo.resolve())

    def test_detached_worker_gets_an_independent_frozen_runtime(self) -> None:
        state = self.repo / ".git" / autoagent.STATE_DIR
        with mock.patch.object(autoagent, "_state_dir", return_value=state):
            with mock.patch.object(autoagent.subprocess, "Popen") as started:
                autoagent._spawn_worker(self.repo)

        environment = started.call_args.kwargs["env"]
        self.assertEqual(environment["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_requested_worker_uses_the_normal_gated_pipeline(self) -> None:
        source = self.repo / "position.py"
        source.write_text("POSITION = (0, 0, 0)\n", encoding="utf-8")
        git(self.repo, "add", "position.py")
        git(self.repo, "commit", "-m", "Add position state")
        config_path = Path(self.tmp.name) / "tbconfig.json"
        config_path.write_text("{}\n", encoding="utf-8")
        command = f"{sys.executable} {ROOT / 'tools' / 'stub_agent.py'} {{form}}"

        with mock.patch.dict(
            os.environ,
            {
                "TB_CONFIG": str(config_path),
                "TB_AGENT_HARNESS": "command",
                "TB_AGENT_COMMAND": command,
            },
        ):
            autoagent.enqueue(self.repo, "user-requested-synthesis", spawn=False)
            status = autoagent.run_worker(self.repo, debounce=0, once=True)

        state = autoagent.read_state(self.repo)["last"]
        report = layout.child_report(self.bunker, "Ada/ECS-4-automatic")
        self.assertEqual(status, 0)
        self.assertEqual(state["status"], "pass")
        self.assertIn("through command", state["detail"])
        self.assertTrue(report.exists())
        self.assertIn("<!-- tb:verified", report.read_text(encoding="utf-8"))
        events = Ledger(layout.paths(self.bunker)["ledger"]).entries()
        self.assertTrue(any(value.get("type") == "synthesis_started" for value in events))
        self.assertTrue(any(value.get("type") == "synthesis_finished" for value in events))

    def test_refresh_uses_companion_projects_configured_base_on_a_new_ticket(self) -> None:
        companion = self.repo / ".tb" / "data" / "projects" / "ECS" / "tickets" / "ECS-4"
        companion.parent.mkdir(parents=True)
        shutil.move(str(self.bunker), companion)
        self.bunker = companion
        (layout.paths(self.bunker)["observations"] / "baseline.json").write_text(
            json.dumps({"baseline": "main", "per_branch": {}}),
            encoding="utf-8",
        )
        (self.repo / layout.LOCATOR_FILE).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "project": "ECS",
                    "bunker_repository": "unused",
                    "config_branch": "ticketbunker/config",
                    "data_branch": "ticketbunker/data",
                    "base_branch": "ecs",
                }
            ),
            encoding="utf-8",
        )
        root_commit = git(self.repo, "rev-list", "--max-parents=0", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/ecs", root_commit)

        with mock.patch.dict(os.environ, {"TB_AGENT_ACTIVE": "1"}):
            status = cli.main(
                ["refresh", "--repo", str(self.repo), "--quiet", "--strict"]
            )

        baseline = json.loads(
            (layout.paths(self.bunker)["observations"] / "baseline.json").read_text(encoding="utf-8")
        )
        self.assertEqual(status, 0)
        self.assertEqual(baseline["configured_baseline"], "ecs")
        self.assertEqual(baseline["baseline"], "origin/ecs")

    def test_worker_preserves_refresh_reason_and_action_in_failure_state(self) -> None:
        refresh_output = (
            "deterministic refresh stopped: the configured base branch 'ecs' is unavailable.\n"
            "Next: Run 'git fetch --prune origin', then retry synthesis.\n"
            "diagnostic: raw command detail"
        )
        completed = subprocess.CompletedProcess([], 1, stdout=refresh_output)
        with mock.patch.object(
            autoagent,
            "_git",
            side_effect=[("Ada/ECS-4-automatic", 0), ("abc123", 0)],
        ):
            with mock.patch.object(autoagent, "_profile", return_value={"harness": "pi"}):
                with mock.patch.object(autoagent, "_launcher", return_value=["tb"]):
                    with mock.patch.object(autoagent.subprocess, "run", return_value=completed):
                        stdout = io.StringIO()
                        with contextlib.redirect_stdout(stdout):
                            ok, detail = autoagent._run_pipeline(
                                self.repo,
                                {
                                    "branch": "Ada/ECS-4-automatic",
                                    "bunker": str(self.bunker),
                                    "head": "abc123",
                                },
                            )

        self.assertFalse(ok)
        self.assertIn("configured base branch 'ecs' is unavailable", detail)
        self.assertIn("git fetch --prune origin", detail)
        self.assertIn("No agent session was started", detail)
        self.assertNotIn("raw command detail", detail)
        self.assertIn("diagnostic: raw command detail", stdout.getvalue())

    def test_strict_refresh_names_the_missing_base_and_exact_next_action(self) -> None:
        (layout.paths(self.bunker)["observations"] / "baseline.json").write_text(
            json.dumps({"baseline": "delivery", "per_branch": {}}),
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"TB_AGENT_ACTIVE": "1"}):
            with contextlib.redirect_stderr(stderr):
                status = cli.main(
                    ["refresh", "--repo", str(self.repo), "--quiet", "--strict"]
                )

        output = stderr.getvalue()
        self.assertEqual(status, 1)
        self.assertIn("configured base branch 'delivery' is unavailable", output)
        self.assertIn("git -C", output)
        self.assertIn("fetch --prune origin", output)
        self.assertIn("diagnostic:", output)

    def test_pi_report_gets_one_bounded_format_repair_before_intake(self) -> None:
        refreshed = cli.main(
            [
                "bootstrap",
                str(self.bunker),
                "--repo",
                str(self.repo),
                "--baseline",
                "main",
            ]
        )
        self.assertEqual(refreshed, 0)
        branch = "Ada/ECS-4-automatic"
        shutil.rmtree(layout.paths(self.bunker)["sessions"])
        form = layout.session_form(self.bunker, branch)
        calls = 0

        def generated_then_repaired(*_args: object, **kwargs: object) -> tuple[str, int]:
            nonlocal calls
            calls += 1
            if calls == 1:
                with mock.patch("tools.stub_agent._real_file", return_value="README.md"):
                    valid = fill(form.read_text(encoding="utf-8"))
                invalid = valid.replace(
                    "- [speculation] grounded: the ticket assignee and observed git authors",
                    "The ticket assignee and observed git authors",
                    1,
                )
                form.write_text(invalid, encoding="utf-8")
                return "generated an invalid Reader brief", 0
            self.assertEqual(kwargs["contract"].name, "agent_report_repair")
            self.assertIn("Reader brief 'Who' needs at least one tagged claim", kwargs["prompt"])
            # Preserve the pipeline-built evidence and repair only the malformed line.
            current = form.read_text(encoding="utf-8")
            form.write_text(
                current.replace(
                    "The ticket assignee and observed git authors",
                    "- [speculation] grounded: the ticket assignee and observed git authors",
                    1,
                ),
                encoding="utf-8",
            )
            return "repaired the report", 0

        with mock.patch(
            "ticketbunker.adapters.harness.run_session",
            side_effect=generated_then_repaired,
        ) as run_session:
            status = cli.main(
                [
                    "run",
                    str(self.bunker),
                    "--repo",
                    str(self.repo),
                    "--branch",
                    branch,
                    "--harness",
                    "pi",
                    "--pi-bin",
                    "/managed/pi",
                ]
            )

        self.assertEqual(status, 0)
        self.assertEqual(run_session.call_count, 2)
        self.assertTrue(layout.child_report(self.bunker, branch).exists())

    def test_unchanged_durable_inputs_skip_the_provider(self) -> None:
        source = self.repo / "position.py"
        source.write_text("POSITION = (0, 0, 0)\n", encoding="utf-8")
        git(self.repo, "add", "position.py")
        git(self.repo, "commit", "-m", "Add position state")
        config_path = Path(self.tmp.name) / "tbconfig.json"
        config_path.write_text("{}\n", encoding="utf-8")
        command = f"{sys.executable} {ROOT / 'tools' / 'stub_agent.py'} {{form}}"
        environment = {
            "TB_CONFIG": str(config_path),
            "TB_AGENT_HARNESS": "command",
            "TB_AGENT_COMMAND": command,
        }

        with mock.patch.dict(os.environ, environment):
            autoagent.enqueue(self.repo, "user-requested-synthesis", spawn=False)
            self.assertEqual(autoagent.run_worker(self.repo, debounce=0, once=True), 0)

            original_run = subprocess.run
            calls: list[list[str]] = []

            def record(argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess:
                calls.append(list(argv))
                return original_run(argv, *args, **kwargs)

            autoagent.enqueue(self.repo, "user-requested-synthesis", spawn=False)
            with mock.patch.object(autoagent.subprocess, "run", side_effect=record):
                self.assertEqual(autoagent.run_worker(self.repo, debounce=0, once=True), 0)

        ticketbunker_commands = [argv for argv in calls if "ticketbunker.cli" in argv]
        self.assertTrue(any("refresh" in argv for argv in ticketbunker_commands))
        self.assertFalse(any("run" in argv for argv in ticketbunker_commands))
        self.assertIn("provider was not called", autoagent.read_state(self.repo)["last"]["detail"])

    def test_command_override_does_not_claim_the_saved_pi_provider_or_model(self) -> None:
        config_path = Path(self.tmp.name) / "tbconfig.json"
        config_path.write_text(
            json.dumps(
                {
                    "agent": {
                        "harness": "pi",
                        "provider": "openai-codex",
                        "model": "gpt-5.6-sol",
                    }
                }
            ),
            encoding="utf-8",
        )

        with mock.patch.dict(
            os.environ,
            {
                "TB_CONFIG": str(config_path),
                "TB_AGENT_HARNESS": "command",
                "TB_AGENT_COMMAND": "stub {form}",
            },
        ):
            profile = autoagent._profile(self.repo, self.bunker, "Ada/ECS-4-automatic")

        self.assertEqual(profile["harness"], "command")
        self.assertNotIn("provider", profile)
        self.assertNotIn("model", profile)

    def test_superseded_agent_form_is_never_published_as_the_new_head(self) -> None:
        source = self.repo / "position.py"
        source.write_text("POSITION = (1, 2, 3)\n", encoding="utf-8")
        git(self.repo, "add", "position.py")
        git(self.repo, "commit", "-m", "Move position")
        command = f"{sys.executable} {ROOT / 'tools' / 'stub_agent.py'} {{form}}"
        env = {
            **os.environ,
            "TB_AGENT_ACTIVE": "1",
            "PYTHONPATH": os.pathsep.join(
                value for value in (str(ROOT), os.environ.get("PYTHONPATH", "")) if value
            ),
        }
        base = [sys.executable, "-m", "ticketbunker.cli"]
        refreshed = subprocess.run(
            [*base, "refresh", "--repo", str(self.repo), "--quiet", "--strict"],
            cwd=str(self.repo),
            env=env,
        )
        run = subprocess.run(
            [
                *base,
                "run",
                str(self.bunker),
                "--repo",
                str(self.repo),
                "--branch",
                "Ada/ECS-4-automatic",
                "--harness",
                "command",
                "--cmd",
                command,
                "--expected-head",
                "0" * 40,
            ],
            cwd=str(self.repo),
            env=env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(refreshed.returncode, 0)
        self.assertEqual(run.returncode, 1)
        self.assertIn("automatic session superseded", run.stderr)
        self.assertFalse(layout.child_report(self.bunker, "Ada/ECS-4-automatic").exists())
        events = Ledger(layout.paths(self.bunker)["ledger"]).entries()
        self.assertTrue(any(value.get("type") == "session_superseded" for value in events))

    def test_real_post_commit_hook_records_change_without_calling_agent(self) -> None:
        autoagent.install(self.repo)
        source = self.repo / "velocity.py"
        source.write_text("VELOCITY = 1\n", encoding="utf-8")
        git(self.repo, "add", "velocity.py")
        config_path = Path(self.tmp.name) / "tbconfig.json"
        config_path.write_text("{}\n", encoding="utf-8")
        command = f"{sys.executable} {ROOT / 'tools' / 'stub_agent.py'} {{form}}"
        env = {
            **os.environ,
            "TB_CONFIG": str(config_path),
            "TB_AGENT_HARNESS": "command",
            "TB_AGENT_COMMAND": command,
            "PYTHONPATH": os.pathsep.join(
                value for value in (str(ROOT), os.environ.get("PYTHONPATH", "")) if value
            ),
        }

        started = time.monotonic()
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-m", "Trigger automatic report"],
            check=True,
            capture_output=True,
            env=env,
        )
        commit_seconds = time.monotonic() - started
        report = layout.child_report(self.bunker, "Ada/ECS-4-automatic")
        state = autoagent.read_state(self.repo)
        self.assertLess(commit_seconds, 1.0)
        self.assertFalse(report.exists())
        self.assertEqual(state["changes"]["branch"], "Ada/ECS-4-automatic")
        self.assertEqual(state["changes"]["events"], ["post-commit"])
        self.assertNotIn("queued", state)


if __name__ == "__main__":
    unittest.main()


class FailureDetailTests(unittest.TestCase):
    def test_provider_lines_become_the_stop_reason(self) -> None:
        output = (
            "error: harness exited 1; form left at /tmp/x.form.md\n"
            "Codex error: The usage limit has been reached\n"
        )
        self.assertEqual(
            autoagent.failure_detail(output, "pi"),
            "pi synthesis failed: Codex error: The usage limit has been reached",
        )
        self.assertEqual(autoagent.failure_detail("", "codex"), "codex synthesis failed")
        self.assertEqual(
            autoagent.failure_detail("session complete: published /a\nsomething else\n", "command"),
            "command synthesis failed: something else",
        )

    def test_blocking_findings_outrank_warnings_whatever_the_order(self) -> None:
        output = (
            "[error] [real] claim has no resolvable anchor (checked b, sha and the bunker): x.json — in: - [real] The ticket\n"
            "Synthesis stopped after one automatic repair pass: the report still failed its safety checks; no shared report changed.\n"
            "  draft:      /tmp/x.form.md\n"
            "[warn] citation: section 'Why': grounded/adjacent speculation cites nothing checkable\n"
        )
        detail = autoagent.failure_detail(output, "pi")
        self.assertTrue(detail.startswith("pi synthesis failed: Synthesis stopped after one automatic repair pass"))
        self.assertIn("First finding: [real] claim has no resolvable anchor", detail)
        self.assertNotIn("[warn]", detail)
        only_warnings = "[warn] citation: something\nsession complete: published /a\n"
        self.assertEqual(autoagent.failure_detail(only_warnings, "pi"), "pi synthesis failed")

