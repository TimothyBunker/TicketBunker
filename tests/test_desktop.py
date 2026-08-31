from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import desktop


class DesktopLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_explicit_app_path_is_authoritative(self) -> None:
        executable = self.root / "TicketBunker"
        executable.touch()
        with mock.patch.dict(os.environ, {"TB_DESKTOP_APP": str(executable)}):
            self.assertEqual(desktop.launcher(), executable)

    def test_missing_explicit_app_path_has_an_actionable_error(self) -> None:
        missing = self.root / "missing-app"
        with mock.patch.dict(os.environ, {"TB_DESKTOP_APP": str(missing)}):
            with self.assertRaisesRegex(
                desktop.DesktopLaunchError, "unset TB_DESKTOP_APP"
            ):
                desktop.launcher()

    def test_uninstalled_app_allows_the_browser_fallback(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(sys, "executable", str(self.root / "python")):
                with mock.patch.object(desktop.shutil, "which", return_value=None):
                    self.assertIsNone(desktop.launcher())

    def test_app_launch_uses_structured_arguments_and_no_shell(self) -> None:
        executable = self.root / "ticketbunker-desktop"
        executable.touch()
        with mock.patch.object(desktop, "launcher", return_value=executable):
            with mock.patch.object(subprocess, "Popen") as opened:
                result = desktop.open_project(
                    self.root,
                    port=18499,
                    route="/source?path=src%2Fmain.py&line=7",
                )

        self.assertTrue(result)
        self.assertEqual(
            opened.call_args.args[0],
            [
                str(executable),
                "--repo",
                str(self.root),
                "--port",
                "18499",
                "--route",
                "/source?path=src%2Fmain.py&line=7",
            ],
        )
        self.assertNotIn("shell", opened.call_args.kwargs)

    def test_route_cannot_be_an_external_url(self) -> None:
        executable = self.root / "ticketbunker-desktop"
        executable.touch()
        with mock.patch.object(desktop, "launcher", return_value=executable):
            with self.assertRaisesRegex(desktop.DesktopLaunchError, "begin with one"):
                desktop.open_project(self.root, route="//example.com")


if __name__ == "__main__":
    unittest.main()
