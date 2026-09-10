"""Report verification and checks follow the existing headless launch contract."""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

import test_bootstrap
from ticketbunker import platform_services
from ticketbunker.processors import anchorcheck, stages


class ReportProcessBoundaryTests(unittest.TestCase):
    def test_anchor_queries_request_no_console_on_windows(self):
        windows = platform_services.PlatformServices.detect(system="win32")
        root = Path.cwd()
        with patch.object(platform_services, "current", return_value=windows), \
                patch.object(anchorcheck.subprocess, "run", return_value=Mock(returncode=0, stdout="content\n")) as run:
            self.assertEqual(anchorcheck._file_at_ref(root, "HEAD", "file.py"), "content\n")
            anchorcheck.annotate("report", root, "HEAD", "test-time")
            run.side_effect = [Mock(returncode=1, stdout=""), Mock(returncode=0)]
            self.assertEqual(anchorcheck._resolves(root, ["HEAD"], root, "src", None), "HEAD")
        self.assertEqual(run.call_count, 4)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs.get("creationflags"), 0x08000000)

    def test_configured_check_uses_headless_options_and_keeps_its_result(self):
        lab = test_bootstrap.RepositoryOwnedDatabaseTests()
        lab.setUp()
        self.addCleanup(lab.tearDown)
        real_run = subprocess.run
        checks = []
        def run(argv, *args, **kwargs):
            flags = kwargs.pop("creationflags", None)
            if kwargs.get("shell"):
                checks.append((argv, flags))
            return real_run(argv, *args, **kwargs)
        with patch.object(platform_services.PlatformServices, "headless_process_options", return_value={"creationflags": 0x08000000}), \
                patch.object(stages.subprocess, "run", side_effect=run):
            result = stages.run_bootstrap(repo=lab.repo, ticket_key="TB-1", baseline="main",
                obs_dir=lab.observations, ledger=lab.ledger, checks={"build": "echo campaign-check"},
                check_branch="tim/TB-1-work")
        self.assertEqual(checks, [("echo campaign-check", 0x08000000)])
        self.assertTrue(result["check"]["tim/TB-1-work"]["build"]["passed"])


if __name__ == "__main__":
    unittest.main()
