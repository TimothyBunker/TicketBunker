from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from ticketbunker import config, platform_services, release_updates


class ReleaseUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_patch = mock.patch.object(
            config, "GLOBAL_CONFIG", self.root / ".tbconfig"
        )
        self.config_patch.start()

    def tearDown(self) -> None:
        self.config_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def payload(**overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "tag_name": "v9.8.7",
            "html_url": "https://github.com/TimothyBunker/TicketBunker/releases/tag/v9.8.7",
            "assets": [
                {
                    "name": "TicketBunker_9.8.7_x64-setup.exe",
                    "browser_download_url": "https://github.com/TimothyBunker/TicketBunker/releases/download/v9.8.7/TicketBunker_9.8.7_x64-setup.exe",
                },
                {
                    "name": "TicketBunker_9.8.7_amd64.deb",
                    "browser_download_url": "https://github.com/TimothyBunker/TicketBunker/releases/download/v9.8.7/TicketBunker_9.8.7_amd64.deb",
                },
            ],
        }
        value.update(overrides)
        return value

    def test_check_selects_the_host_installer_and_persists_only_validated_urls(self) -> None:
        host = platform_services.PlatformServices("windows", "windows", "x64")

        with mock.patch.object(release_updates.platform_services, "current", return_value=host):
            result = release_updates.check(fetcher=lambda _request, _timeout: self.payload())

        self.assertTrue(result["available"])
        self.assertEqual(result["latest"], "9.8.7")
        self.assertEqual(result["asset"], "TicketBunker_9.8.7_x64-setup.exe")
        self.assertTrue(result["download_url"].endswith("x64-setup.exe"))
        cached = json.loads(config.GLOBAL_CONFIG.read_text(encoding="utf-8"))
        self.assertNotIn("assets", cached["release_check"])
        self.assertEqual(release_updates.state()["latest"], "9.8.7")

    def test_check_rejects_redirected_release_pages_and_downloads(self) -> None:
        with self.assertRaisesRegex(release_updates.ReleaseCheckError, "unsafe"):
            release_updates.check(
                fetcher=lambda _request, _timeout: self.payload(
                    html_url="https://example.test/fake-release"
                )
            )

        payload = self.payload()
        payload["assets"] = [
            {
                "name": "TicketBunker_9.8.7_amd64.deb",
                "browser_download_url": "https://example.test/TicketBunker.deb",
            }
        ]
        host = platform_services.PlatformServices("linux", "linux", "x64")
        with mock.patch.object(release_updates.platform_services, "current", return_value=host):
            result = release_updates.check(fetcher=lambda _request, _timeout: payload)
        self.assertEqual(result["download_url"], "")
        self.assertEqual(result["release_url"].split("/tag/")[-1], "v9.8.7")

    def test_network_failures_are_actionable_and_do_not_leak_transport_detail(self) -> None:
        def unavailable(_request: object, _timeout: float) -> dict[str, object]:
            raise urllib.error.URLError("credential-bearing-private-detail")

        with self.assertRaises(release_updates.ReleaseCheckError) as raised:
            release_updates.check(fetcher=unavailable)

        self.assertIn("Check your connection", str(raised.exception))
        self.assertNotIn("private-detail", str(raised.exception))
        cached = json.loads(config.GLOBAL_CONFIG.read_text(encoding="utf-8"))
        self.assertTrue(cached["release_check"]["attempted_at"])
        self.assertNotIn("latest", cached["release_check"])

    def test_missing_public_release_is_distinct_from_a_connection_failure(self) -> None:
        def missing(_request: object, _timeout: float) -> dict[str, object]:
            raise urllib.error.HTTPError(
                release_updates.LATEST_RELEASE_API, 404, "Not Found", {}, None
            )

        with self.assertRaisesRegex(
            release_updates.ReleaseCheckError, "No public TicketBunker release"
        ):
            release_updates.check(fetcher=missing)

    def test_cached_newer_version_is_recomputed_against_this_build(self) -> None:
        config.save(
            {
                "release_check": {
                    "latest": "0.1.0",
                    "checked_at": "2026-08-31T00:00:00+00:00",
                    "release_url": "https://github.com/TimothyBunker/TicketBunker/releases/tag/v0.1.0",
                }
            }
        )

        self.assertFalse(release_updates.state()["available"])

    def test_locally_poisoned_cached_urls_are_never_returned_to_the_app(self) -> None:
        state = release_updates.state(
            {
                "release_check": {
                    "latest": "9.8.7",
                    "release_url": "https://example.test/fake-release",
                    "download_url": "https://example.test/fake-installer.exe",
                }
            }
        )

        self.assertTrue(state["available"])
        self.assertEqual(state["release_url"], "")
        self.assertEqual(state["download_url"], "")


if __name__ == "__main__":
    unittest.main()
