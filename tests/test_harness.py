from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import platform_services
from ticketbunker.adapters import harness


class HarnessTests(unittest.TestCase):
    def test_command_harness_preserves_windows_paths(self) -> None:
        windows = platform_services.PlatformServices.detect(
            system="win32", machine="AMD64"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bunker = root / "DEMO-7_bunker"
            form = bunker / "session" / "form.md"
            ledger = mock.Mock()
            with mock.patch.object(
                harness.platform_services, "current", return_value=windows
            ), mock.patch.object(
                harness, "run_stage", side_effect=lambda _contract, _ledger, call: call()
            ), mock.patch.object(
                harness.proc, "run", return_value=("", 0)
            ) as run:
                harness.run_session(
                    bunker,
                    "Ada/DEMO-7-work",
                    root,
                    ledger,
                    harness="command",
                    command_template=(
                        '"C:\\Program Files\\Agent\\agent.exe" --form "{form}"'
                    ),
                    form=form,
                )

        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "C:\\Program Files\\Agent\\agent.exe")
        self.assertEqual(argv[1:], ["--form", str(form)])


if __name__ == "__main__":
    unittest.main()
