from __future__ import annotations

import hashlib
import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "ticketbunker_build_desktop", ROOT / "tools" / "build_desktop.py"
)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class DesktopReleaseTests(unittest.TestCase):
    def test_stage_places_the_frozen_core_and_verified_runtime_for_tauri(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            source = root / "source"
            (source / "ticketbunker" / "harness" / "pi").mkdir(parents=True)
            (source / "ticketbunker" / "licenses").mkdir(parents=True)
            (source / "ticketbunker" / "runtime").mkdir(parents=True)
            (source / "desktop" / "src-tauri").mkdir(parents=True)
            (source / "ticketbunker" / "harness" / "pi" / "tb.ts").write_text("adapter")
            (source / "ticketbunker" / "harness" / "pi" / "tb_auth.ts").write_text("auth adapter")
            (source / "ticketbunker" / "licenses" / "pi-MIT.txt").write_text("license")
            (source / "THIRD_PARTY_NOTICES.md").write_text("notice")
            (source / "pyproject.toml").write_text('[project]\nversion = "9.8.7"\n')
            tb = root / "tb"
            tb.write_text("core")
            payload = root / "payload" / "pi"
            payload.mkdir(parents=True)
            (payload / "pi").write_text("runtime")
            archive = root / "pi.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                output.add(payload, arcname="pi")
            pi_manifest = source / "ticketbunker" / "runtime" / "pi-runtime.json"
            pi_manifest.write_text(
                json.dumps(
                    {
                        "version": "1.2.3",
                        "platforms": {
                            "linux-x64": {
                                "archive": archive.name,
                                "url": "https://example.invalid/pi.tar.gz",
                                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                                "format": "tar.gz",
                                "executable": "pi/pi",
                            }
                        },
                    }
                )
            )

            with mock.patch.object(builder, "ROOT", source):
                with mock.patch.object(builder, "DESKTOP", source / "desktop"):
                    with mock.patch.object(builder, "PI_MANIFEST", pi_manifest):
                        with mock.patch.object(builder.build_release, "PACKAGE_VERSION", source / "pyproject.toml"):
                            sidecar, resources = builder.stage(
                                platform_name="linux-x64",
                                triple="x86_64-unknown-linux-gnu",
                                tb=tb,
                                pi_archive=archive,
                            )

            self.assertEqual(sidecar.name, "tb-x86_64-unknown-linux-gnu")
            self.assertEqual(sidecar.read_text(), "core")
            self.assertEqual(
                (resources / "libexec" / "ticketbunker" / "pi" / "pi").read_text(),
                "runtime",
            )
            manifest = json.loads((resources / "manifest.json").read_text())
            self.assertEqual(manifest["ticketbunker_version"], "9.8.7")
            self.assertIn("libexec/ticketbunker/pi/pi", manifest["files"])
            self.assertEqual(
                (resources / "share" / "ticketbunker" / "harness" / "pi" / "tb_auth.ts").read_text(),
                "auth adapter",
            )

    def test_publish_native_packages_flattens_installers_with_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            bundle = root / "bundle"
            (bundle / "deb").mkdir(parents=True)
            (bundle / "app").mkdir(parents=True)
            (bundle / "deb" / "TicketBunker_1.0_amd64.deb").write_bytes(b"package")
            (bundle / "deb" / "TicketBunker_0.9_amd64.deb").write_bytes(b"stale")
            (bundle / "app" / "TicketBunker.app").write_text("directory marker")
            release = root / "release"

            published = builder.publish_native_packages(bundle, release, version="1.0")

            package = release / "TicketBunker_1.0_amd64.deb"
            checksum = release / "TicketBunker_1.0_amd64.deb.sha256"
            self.assertEqual(published, [package, checksum])
            self.assertEqual(package.read_bytes(), b"package")
            self.assertEqual(
                checksum.read_text(),
                f"{hashlib.sha256(b'package').hexdigest()}  {package.name}\n",
            )
            self.assertFalse((release / "TicketBunker_0.9_amd64.deb").exists())


if __name__ == "__main__":
    unittest.main()
