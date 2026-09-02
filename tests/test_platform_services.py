from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import platform_services


class PlatformServicesTests(unittest.TestCase):
    def test_runtime_keys_are_stable_release_contracts(self) -> None:
        self.assertEqual(
            platform_services.PlatformServices.detect(
                system="linux", machine="x86_64"
            ).runtime_key,
            "linux-x64",
        )
        self.assertEqual(
            platform_services.PlatformServices.detect(
                system="win32", machine="AMD64"
            ).runtime_key,
            "windows-x64",
        )
        self.assertEqual(
            platform_services.PlatformServices.detect(
                system="darwin", machine="arm64"
            ).runtime_key,
            "darwin-arm64",
        )

    def test_each_host_uses_its_native_application_data_location(self) -> None:
        home = Path("/people/tim")
        linux = platform_services.PlatformServices.detect(
            system="linux", machine="x86_64"
        )
        windows = platform_services.PlatformServices.detect(
            system="win32", machine="AMD64"
        )
        macos = platform_services.PlatformServices.detect(
            system="darwin", machine="arm64"
        )

        self.assertEqual(
            linux.data_home(environ={}, home=home),
            home / ".local" / "share" / "ticketbunker",
        )
        self.assertEqual(
            windows.data_home(environ={}, home=home),
            home / "AppData" / "Local" / "TicketBunker",
        )
        self.assertEqual(
            macos.data_home(environ={}, home=home),
            home / "Library" / "Application Support" / "TicketBunker",
        )

    def test_data_home_override_is_shared_by_every_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expected = Path(temporary).resolve()
            for system in ("linux", "win32", "darwin"):
                host = platform_services.PlatformServices.detect(
                    system=system, machine="x86_64"
                )
                self.assertEqual(
                    host.data_home(environ={"TB_DATA_HOME": temporary}), expected
                )

    def test_executable_suffix_is_a_platform_service(self) -> None:
        windows = platform_services.PlatformServices.detect(
            system="win32", machine="AMD64"
        )
        linux = platform_services.PlatformServices.detect(
            system="linux", machine="x86_64"
        )

        self.assertEqual(windows.executable_name("tb"), "tb.exe")
        self.assertEqual(linux.executable_name("tb"), "tb")

    def test_windows_command_parsing_removes_only_balanced_outer_quotes(self) -> None:
        windows = platform_services.PlatformServices.detect(
            system="win32", machine="AMD64"
        )

        self.assertEqual(
            windows.split_command(
                '"C:\\Program Files\\Editor\\code.exe" --goto {file}:{line}'
            ),
            ["C:\\Program Files\\Editor\\code.exe", "--goto", "{file}:{line}"],
        )

    def test_background_process_shape_is_selected_at_the_boundary(self) -> None:
        linux = platform_services.PlatformServices.detect(
            system="linux", machine="x86_64"
        )
        windows = platform_services.PlatformServices.detect(
            system="win32", machine="AMD64"
        )

        self.assertEqual(
            linux.background_process_options(detached=True),
            {"start_new_session": True},
        )
        with mock.patch.object(
            platform_services.subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            2,
            create=True,
        ), mock.patch.object(
            platform_services.subprocess, "DETACHED_PROCESS", 8, create=True
        ):
            self.assertEqual(
                windows.background_process_options(detached=True),
                {"creationflags": 10},
            )

    def test_headless_helpers_do_not_allocate_a_windows_console(self) -> None:
        linux = platform_services.PlatformServices.detect(
            system="linux", machine="x86_64"
        )
        windows = platform_services.PlatformServices.detect(
            system="win32", machine="AMD64"
        )

        self.assertEqual(linux.headless_process_options(), {})
        with mock.patch.object(
            platform_services.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True
        ):
            self.assertEqual(
                windows.headless_process_options(),
                {"creationflags": 0x08000000},
            )

    def test_linux_terminal_discovery_is_not_a_domain_decision(self) -> None:
        linux = platform_services.PlatformServices.detect(
            system="linux", machine="x86_64"
        )

        value = linux.terminal_argv(
            ["/usr/bin/vim", "+4", "/work/a.py"],
            which=lambda name: "/usr/bin/gnome-terminal"
            if name == "gnome-terminal"
            else None,
        )

        self.assertEqual(
            value,
            [
                "/usr/bin/gnome-terminal",
                "--",
                "/usr/bin/vim",
                "+4",
                "/work/a.py",
            ],
        )

    def test_unknown_host_fails_runtime_selection_without_guessing(self) -> None:
        host = platform_services.PlatformServices.detect(
            system="plan9", machine="mips"
        )

        self.assertEqual(host.family, "unsupported")
        self.assertIsNone(host.runtime_key)


if __name__ == "__main__":
    unittest.main()
