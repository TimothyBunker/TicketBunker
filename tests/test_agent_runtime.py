from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ticketbunker import agent_runtime, config, platform_services


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
        self.assertEqual(value["platforms"]["windows-x64"]["executable"], "pi.exe")
        self.assertEqual(value["platforms"]["windows-arm64"]["executable"], "pi.exe")

    def test_flat_windows_runtime_path_drives_bundle_and_managed_discovery(self) -> None:
        bundle = self.root / "bundle"
        data = self.root / "data"
        with mock.patch.object(
            agent_runtime,
            "manifest",
            return_value={
                "version": "0.84.2",
                "platforms": {"windows-x64": {"executable": "pi.exe"}},
            },
        ), mock.patch.object(
            agent_runtime, "platform_key", return_value="windows-x64"
        ), mock.patch.object(
            agent_runtime, "_bundle_roots", return_value=[bundle]
        ), mock.patch.object(
            agent_runtime, "data_root", return_value=data
        ), mock.patch.object(
            agent_runtime.platform_services,
            "current",
            return_value=platform_services.PlatformServices.detect(
                system="win32", machine="AMD64"
            ),
        ):
            candidates = agent_runtime._bundle_candidates()

        self.assertEqual(
            candidates,
            [
                (bundle / "libexec" / "ticketbunker" / "pi.exe", "bundled"),
                (bundle / "_runtime" / "pi.exe", "bundled"),
                (data / "runtime" / "pi" / "0.84.2" / "pi.exe", "managed"),
            ],
        )

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

    def test_packaged_auth_extension_keeps_pi_as_the_credential_writer(self) -> None:
        path = agent_runtime.auth_extension_path()

        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("runtime.login(provider, method", text)
        self.assertIn("TB_AUTH_EVENT", text)
        self.assertNotIn("auth.json", text)

    def test_frozen_auth_extension_is_materialized_outside_temporary_extraction(self) -> None:
        with mock.patch.object(agent_runtime.sys, "frozen", True, create=True):
            with mock.patch.object(agent_runtime, "data_root", return_value=self.root / "data"):
                path = agent_runtime.auth_extension_path()

        self.assertTrue(path.is_file())
        self.assertTrue(path.is_relative_to(self.root / "data" / "assets"))

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

    def test_desktop_resource_root_is_the_first_bundle_location(self) -> None:
        resources = self.root / "application-resources"
        with mock.patch.dict(os.environ, {"TB_BUNDLE_ROOT": str(resources)}):
            roots = agent_runtime._bundle_roots()

        self.assertEqual(roots[0], resources)

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

    def test_explicit_selection_is_validated_then_saved(self) -> None:
        runtime = agent_runtime.Runtime(
            str(self.pi), "override", version="0.84.2", ready=True
        )

        with self.environment():
            result = agent_runtime.configure_selection(
                runtime,
                provider="another-provider",
                model="model-2",
                repo=self.root,
            )

        self.assertTrue(result["ready"])
        saved = config.load()["agent"]
        self.assertEqual(saved["provider"], "another-provider")
        self.assertEqual(saved["model"], "model-2")

    def test_model_only_override_retains_the_current_provider(self) -> None:
        config.save(
            {"agent": {"provider": "example", "model": "model-1", "harness": "pi"}}
        )
        runtime = agent_runtime.Runtime(
            str(self.pi), "override", version="0.84.2", ready=True
        )

        with self.environment():
            result = agent_runtime.configure_selection(
                runtime,
                model="model-2",
                repo=self.root,
            )

        self.assertTrue(result["ready"])
        saved = config.load()["agent"]
        self.assertEqual(saved["provider"], "example")
        self.assertEqual(saved["model"], "model-2")

    def test_failed_selection_does_not_replace_the_working_model(self) -> None:
        config.save(
            {"agent": {"provider": "example", "model": "model-1", "harness": "pi"}}
        )
        runtime = agent_runtime.Runtime(
            str(self.pi), "override", version="0.84.2", ready=True
        )
        rejected = {
            "ready": False,
            "status": "missing",
            "detail": "model is unavailable",
        }

        with mock.patch.object(agent_runtime, "auth_check", return_value=rejected):
            result = agent_runtime.configure_selection(
                runtime,
                provider="another-provider",
                model="missing-model",
                repo=self.root,
            )

        self.assertFalse(result["ready"])
        saved = config.load()["agent"]
        self.assertEqual(saved["provider"], "example")
        self.assertEqual(saved["model"], "model-1")

    def test_interactive_model_change_adopts_pi_default_after_exit(self) -> None:
        def choose_model(*_args, **_kwargs):
            self.settings.write_text(
                json.dumps({"defaultProvider": "example", "defaultModel": "model-2"}),
                encoding="utf-8",
            )
            return True

        with self.environment():
            with mock.patch.object(agent_runtime, "_open_pi", side_effect=choose_model):
                result = agent_runtime.change_selection(self.root, interactive=True)

        self.assertTrue(result["ready"])
        self.assertEqual(config.load()["agent"]["model"], "model-2")

    def _runtime_archive(self, *, approved: bool = True):
        payload = self.root / "payload" / "pi"
        payload.mkdir(parents=True)
        executable = payload / ("pi.exe" if os.name == "nt" else "pi")
        executable.write_bytes(self.pi.read_bytes())
        asset = payload / "package.json"
        asset.write_text('{"version":"0.84.2"}\n', encoding="utf-8")
        archive = self.root / "pi.tar.gz"
        with tarfile.open(archive, "w:gz") as value:
            value.add(payload, arcname="pi")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        value = {
            "version": "0.84.2",
            "platforms": {
                "test-platform": {
                    "archive": archive.name,
                    "url": archive.as_uri(),
                    "sha256": digest if approved else "0" * 64,
                    "format": "tar.gz",
                    "executable": f"pi/{executable.name}",
                }
            },
        }
        return value, executable.name

    def test_missing_runtime_is_downloaded_verified_and_configured_automatically(self) -> None:
        runtime_manifest, executable_name = self._runtime_archive()
        messages = []

        def inspect(path, origin):
            resolved = Path(path).resolve()
            if origin == "managed" and resolved.is_file():
                error = agent_runtime._managed_integrity_error(resolved)
                return agent_runtime.Runtime(
                    str(resolved), origin, version="0.84.2", ready=not error,
                    detail=error, integrity="verified" if not error else "failed",
                )
            return agent_runtime.Runtime(str(resolved), origin, detail="not ready")

        environment = {
            "TB_DATA_HOME": str(self.root / "data"),
            "PI_CODING_AGENT_DIR": str(self.settings.parent),
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            with mock.patch.object(agent_runtime, "manifest", return_value=runtime_manifest):
                with mock.patch.object(agent_runtime, "platform_key", return_value="test-platform"):
                    with mock.patch.object(agent_runtime, "_inspect", side_effect=inspect):
                        installed = agent_runtime.ensure_runtime(messages.append)

        expected = self.root / "data" / "runtime" / "pi" / "0.84.2" / "pi" / executable_name
        self.assertTrue(installed.ready)
        self.assertTrue(Path(installed.path).samefile(expected))
        self.assertTrue(Path(config.load()["agent"]["pi_bin"]).samefile(expected))
        self.assertTrue((expected.parents[1] / "manifest.json").is_file())
        self.assertTrue(any("downloading verified Pi" in value for value in messages))
        self.assertTrue(any("installed and configured" in value for value in messages))

    def test_bad_runtime_download_is_rejected_with_exact_manual_route(self) -> None:
        runtime_manifest, _executable_name = self._runtime_archive(approved=False)
        environment = {"TB_DATA_HOME": str(self.root / "data")}

        with mock.patch.dict(os.environ, environment, clear=False):
            with mock.patch.object(agent_runtime, "manifest", return_value=runtime_manifest):
                with mock.patch.object(agent_runtime, "platform_key", return_value="test-platform"):
                    with mock.patch.object(
                        agent_runtime, "discover",
                        return_value=agent_runtime.Runtime("", "missing", detail="not found"),
                    ):
                        with self.assertRaises(agent_runtime.RuntimeInstallError) as caught:
                            agent_runtime.ensure_runtime()

        entry = runtime_manifest["platforms"]["test-platform"]
        self.assertIn("checksum", caught.exception.summary)
        self.assertIn(entry["url"], caught.exception.action)
        self.assertIn("verify SHA-256", caught.exception.action)
        self.assertNotIn("pi_bin", config.load().get("agent") or {})


if __name__ == "__main__":
    unittest.main()
