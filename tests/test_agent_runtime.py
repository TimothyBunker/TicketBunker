from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import agent_runtime, config


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_config = config.GLOBAL_CONFIG
        config.GLOBAL_CONFIG = self.root / "tbconfig.json"
        self.settings = self.root / "pi" / "settings.json"
        self.settings.parent.mkdir()
        self.settings.write_text(
            json.dumps({"defaultProvider": "example", "defaultModel": "model-1"}),
            encoding="utf-8",
        )
        if os.name == "nt":
            self.pi = self.root / "pi.cmd"
            self.pi.write_text(
                "@echo off\r\n"
                "if \"%1\"==\"--version\" (echo 0.84.2 & exit /b 0)\r\n"
                "if \"%1\"==\"auth\" (echo {\"status\":\"ready\",\"provider\":\"example\",\"authType\":\"oauth\"} & exit /b 0)\r\n"
                "exit /b 1\r\n",
                encoding="utf-8",
            )
        else:
            self.pi = self.root / "pi-bin"
            self.pi.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"--version\" ]; then echo 0.84.2; exit 0; fi\n"
                "if [ \"$1\" = \"auth\" ]; then "
                "echo '{\"status\":\"ready\",\"provider\":\"example\",\"authType\":\"oauth\"}'; exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            self.pi.chmod(0o755)

    def tearDown(self) -> None:
        config.GLOBAL_CONFIG = self.old_config
        self.tmp.cleanup()

    def environment(self):
        return mock.patch.dict(
            os.environ,
            {"TB_AGENT_PI_BIN": str(self.pi), "PI_CODING_AGENT_DIR": str(self.settings.parent)},
            clear=False,
        )

    def test_manifest_covers_every_release_platform_with_pinned_hashes(self) -> None:
        value = agent_runtime.manifest()

        self.assertEqual(value["version"], "0.84.2")
        self.assertEqual(
            set(value["platforms"]),
            {
                "darwin-arm64", "darwin-x64", "linux-arm64",
                "linux-x64", "windows-arm64", "windows-x64",
            },
        )
        for entry in value["platforms"].values():
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn("/v0.84.2/", entry["url"])

    def test_ready_state_adopts_pi_selection_without_reading_credentials(self) -> None:
        with self.environment():
            result = agent_runtime.state(self.root)

        self.assertTrue(result["ready"])
        self.assertEqual(result["provider"], "example")
        self.assertEqual(result["model"], "model-1")
        self.assertEqual(result["authentication"]["auth_type"], "oauth")
        self.assertNotIn("credential", json.dumps(result).lower())

    def test_onboard_persists_only_harness_provider_and_model(self) -> None:
        with self.environment():
            result = agent_runtime.onboard(self.root, interactive=False)

        saved = config.load()["agent"]
        self.assertTrue(result["ready"])
        self.assertEqual(saved["harness"], "pi")
        self.assertEqual(saved["provider"], "example")
        self.assertEqual(saved["model"], "model-1")
        self.assertNotIn("token", json.dumps(saved).lower())

    def test_broken_runtime_is_actionable_and_never_reported_ready(self) -> None:
        if os.name == "nt":
            self.pi.write_text("@echo broken 1>&2\r\n@exit /b 2\r\n", encoding="utf-8")
        else:
            self.pi.write_text("#!/bin/sh\necho broken >&2\nexit 2\n", encoding="utf-8")
        with self.environment():
            result = agent_runtime.state(self.root)

        self.assertFalse(result["ready"])
        self.assertEqual(result["status"], "runtime-unavailable")
        self.assertIn("TB_AGENT_PI_BIN", result["action"])

    def test_packaged_extension_is_a_real_file(self) -> None:
        path = agent_runtime.extension_path()

        self.assertTrue(path.is_file())
        self.assertIn("agent_tool_call", path.read_text(encoding="utf-8"))
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            (Path(__file__).resolve().parent.parent / "harness" / "pi" / "tb.ts").read_text(encoding="utf-8"),
        )

    def test_bundled_runtime_verifies_every_distribution_file(self) -> None:
        bundle = self.root / "bundle"
        runtime_dir = bundle / "libexec" / "ticketbunker" / "pi"
        runtime_dir.mkdir(parents=True)
        executable = runtime_dir / self.pi.name
        shutil.copy2(self.pi, executable)
        asset = runtime_dir / "package.json"
        asset.write_text('{"version":"0.84.2"}\n', encoding="utf-8")
        files = {}
        for path in (executable, asset):
            files[path.relative_to(bundle).as_posix()] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        (bundle / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")

        ready = agent_runtime._inspect(executable, "bundled")
        asset.write_text("tampered\n", encoding="utf-8")
        rejected = agent_runtime._inspect(executable, "bundled")

        self.assertTrue(ready.ready)
        self.assertEqual(ready.integrity, "verified")
        self.assertFalse(rejected.ready)
        self.assertIn("package.json", rejected.detail)

    def test_core_only_is_an_explicit_local_choice(self) -> None:
        agent_runtime.set_core_only()

        result = agent_runtime.state(self.root)
        self.assertTrue(result["ready"])
        self.assertFalse(result["enabled"])

    def test_later_ordinary_init_reenables_a_ready_selection(self) -> None:
        config.save(
            {
                "agent": {
                    "enabled": False,
                    "harness": "pi",
                    "provider": "example",
                    "model": "model-1",
                }
            }
        )

        with self.environment():
            result = agent_runtime.onboard(self.root, interactive=False)

        self.assertTrue(result["ready"])
        self.assertTrue(config.load()["agent"]["enabled"])


if __name__ == "__main__":
    unittest.main()
