from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ticketbunker import api, commands, config, layout, project, viewer, workflow


def run_git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


class TicketWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.source = base / "source"
        self.source_remote = base / "source.git"
        self.bunker_remote = base / "ticketbunker.git"
        self.old_global_config = config.GLOBAL_CONFIG
        config.GLOBAL_CONFIG = base / "tbconfig.json"
        subprocess.run(["git", "init", "--bare", str(self.source_remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", "--bare", str(self.bunker_remote)], check=True, capture_output=True)
        self.source.mkdir()
        run_git(self.source, "init", "-b", "ecs")
        run_git(self.source, "config", "user.name", "Ada")
        run_git(self.source, "config", "user.email", "ada@example.com")
        (self.source / "README.md").write_text("mock voxel\n", encoding="utf-8")
        run_git(self.source, "add", "README.md")
        run_git(self.source, "commit", "-m", "initial")
        run_git(self.source, "remote", "add", "origin", str(self.source_remote))
        run_git(self.source, "push", "-u", "origin", "ecs")
        commands.dispatch(
            "project.initialize",
            {"project": "ECS", "bunker_repo": str(self.bunker_remote), "base_branch": "ecs"},
            commands.CommandContext(self.source),
        )
        run_git(self.source, "add", project.LOCATOR_FILE, ".gitignore")
        run_git(self.source, "commit", "-m", "Connect TicketBunker")
        run_git(self.source, "push")
        commands.dispatch(
            "ticket.upsert",
            {
                "ticket": {
                    "id": "ECS-4",
                    "key": "ECS-4",
                    "title": "Position system",
                    "type": "task",
                    "status": "todo",
                }
            },
            commands.CommandContext(self.source),
        )

    def tearDown(self) -> None:
        config.GLOBAL_CONFIG = self.old_global_config
        self.tmp.cleanup()

    def test_start_refreshes_base_creates_switches_and_initializes_session(self) -> None:
        result = commands.dispatch(
            "workflow.start",
            {"ticket": "ECS-4", "name": "position-system", "user": "Ada"},
            commands.CommandContext(self.source),
        )

        self.assertEqual(result["branch"], "Ada/ECS-4-position-system")
        self.assertEqual(result["base"], "ecs")
        self.assertEqual(run_git(self.source, "branch", "--show-current"), result["branch"])
        session = json.loads(
            layout.session_config(project.tickets_dir(self.source) / "ECS-4", result["branch"]).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(session["user"], "Ada")
        self.assertEqual(session["baseline"], "ecs")
        opened = api.TicketCatalog(self.source).get_ticket("ECS", "ECS-4")
        self.assertEqual([(branch.name, branch.state) for branch in opened.branches], [(result["branch"], "unobserved")])

    def test_dirty_worktree_is_rejected_before_checkout(self) -> None:
        (self.source / "dirty.txt").write_text("unfinished\n", encoding="utf-8")
        before = run_git(self.source, "branch", "--show-current")

        with self.assertRaises(commands.CommandRejected) as caught:
            commands.dispatch(
                "workflow.start",
                {"ticket": "ECS-4", "name": "position-system"},
                commands.CommandContext(self.source),
            )

        self.assertEqual(caught.exception.code, "worktree_dirty")
        self.assertIn("Commit or stash", str(caught.exception))
        self.assertEqual(run_git(self.source, "branch", "--show-current"), before)
        self.assertNotIn("Ada/ECS-4-position-system", run_git(self.source, "branch", "--format=%(refname:short)"))

    def test_start_fast_forwards_base_from_origin_before_branching(self) -> None:
        updater = Path(self.tmp.name) / "updater"
        subprocess.run(
            ["git", "clone", "--branch", "ecs", str(self.source_remote), str(updater)],
            check=True,
            capture_output=True,
        )
        run_git(updater, "config", "user.name", "Lin")
        run_git(updater, "config", "user.email", "lin@example.com")
        (updater / "REMOTE.md").write_text("latest base\n", encoding="utf-8")
        run_git(updater, "add", "REMOTE.md")
        run_git(updater, "commit", "-m", "Advance base")
        run_git(updater, "push")
        remote_head = run_git(updater, "rev-parse", "HEAD")

        result = commands.dispatch(
            "workflow.start",
            {"ticket": "ECS-4", "name": "from-latest"},
            commands.CommandContext(self.source),
        )

        self.assertEqual(run_git(self.source, "rev-parse", f"{result['branch']}^{{commit}}"), remote_head)
        self.assertTrue((self.source / "REMOTE.md").exists())

    def test_attach_rejects_misnamed_branch_and_accepts_ticket_key(self) -> None:
        run_git(self.source, "branch", "unrelated-work")
        run_git(self.source, "branch", "Lin/ECS-4-existing")

        with self.assertRaises(commands.CommandRejected) as caught:
            commands.dispatch(
                "workflow.attach",
                {"ticket": "ECS-4", "branch": "unrelated-work"},
                commands.CommandContext(self.source),
            )
        attached = commands.dispatch(
            "workflow.attach",
            {"ticket": "ECS-4", "branch": "Lin/ECS-4-existing", "user": "Lin"},
            commands.CommandContext(self.source),
        )

        self.assertEqual(caught.exception.code, "branch_name_invalid")
        self.assertEqual(attached["branch"], "Lin/ECS-4-existing")

    def test_work_state_exposes_clean_preflight_and_configured_base(self) -> None:
        result = workflow.state(self.source, "ECS-4")

        self.assertTrue(result["clean"])
        self.assertEqual(result["base_branch"], "ecs")
        self.assertEqual(result["base_branches"][0], "ecs")
        self.assertEqual(result["current_branch"], "ecs")

    def test_viewer_work_control_uses_csrf_protected_shared_command(self) -> None:
        server = viewer.serve(self.source, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            page = urlopen(f"{base}/project/ECS/ticket/ECS-4").read().decode("utf-8")
            state = json.loads(urlopen(f"{base}/work/state/ECS-4").read().decode("utf-8"))
            token = re.search(r'data-token="([^"]+)"', page).group(1)
            body = json.dumps({"ticket": "ECS-4", "name": "viewer-work", "base": "ecs"}).encode()
            with self.assertRaises(HTTPError) as rejected:
                urlopen(
                    Request(
                        f"{base}/work",
                        data=body,
                        headers={"Content-Type": "application/json", "X-TicketBunker-Token": "wrong"},
                    )
                )
            response = json.loads(
                urlopen(
                    Request(
                        f"{base}/work",
                        data=body,
                        headers={"Content-Type": "application/json", "X-TicketBunker-Token": token},
                    )
                ).read().decode("utf-8")
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertIn('aria-label="Start a branch"', page)
        self.assertIn('data-work-form data-ticket="ECS-4"', page)
        self.assertTrue(state["clean"])
        self.assertEqual(rejected.exception.code, 403)
        self.assertEqual(response["branch"], "Ada/ECS-4-viewer-work")
        self.assertEqual(run_git(self.source, "branch", "--show-current"), response["branch"])


if __name__ == "__main__":
    unittest.main()
