from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_builder():
    spec = importlib.util.spec_from_file_location("ticketbunker_build_release", ROOT / "tools" / "build_release.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReleaseBuilderTests(unittest.TestCase):
    def test_verified_pi_archive_becomes_a_self_contained_bundle(self) -> None:
        builder = load_builder()
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            pi_tree = root / "payload" / "pi"
            pi_tree.mkdir(parents=True)
            fake_pi = pi_tree / "pi"
            fake_pi.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"--version\" ]; then echo 0.84.2; exit 0; fi\n"
                "echo '{\"type\":\"extension_ui_request\",\"method\":\"notify\",\"message\":\"TB_AUTH_EVENT:{\\\"kind\\\":\\\"catalog\\\",\\\"providers\\\":[]}\"}'\n",
                encoding="utf-8",
            )
            archive = root / "pi.tar.gz"
            with tarfile.open(archive, "w:gz") as value:
                value.add(pi_tree, arcname="pi")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            pi_manifest = root / "pi-runtime.json"
            pi_manifest.write_text(
                json.dumps(
                    {
                        "version": "0.84.2",
                        "platforms": {
                            "linux-x64": {
                                "archive": archive.name,
                                "url": "https://example.invalid/pi.tar.gz",
                                "sha256": digest,
                                "format": "tar.gz",
                                "executable": "pi/pi",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            fake_tb = root / "tb"
            fake_tb.write_text("#!/bin/sh\necho tb\n", encoding="utf-8")
            fake_tb.chmod(0o755)
            output_dir = root / "out"
            builder.PI_MANIFEST = pi_manifest

            output = builder.build(
                argparse.Namespace(
                    platform="linux-x64",
                    tb=fake_tb,
                    pi_archive=archive,
                    output_dir=output_dir,
                )
            )

            self.assertTrue(output.is_file())
            self.assertTrue(output.with_name(f"{output.name}.sha256").is_file())
            extracted = root / "extracted"
            with tarfile.open(output, "r:gz") as value:
                value.extractall(extracted, filter="data")
            bundle = next(extracted.iterdir())
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            pi_path = bundle / "libexec" / "ticketbunker" / "pi" / "pi"
            expected = manifest["files"]["libexec/ticketbunker/pi/pi"]["sha256"]
            self.assertEqual(hashlib.sha256(pi_path.read_bytes()).hexdigest(), expected)
            self.assertTrue((bundle / "share" / "ticketbunker" / "harness" / "pi" / "tb.ts").is_file())
            self.assertTrue((bundle / "share" / "ticketbunker" / "harness" / "pi" / "tb_auth.ts").is_file())
            self.assertTrue((bundle / "share" / "licenses" / "pi" / "LICENSE.txt").is_file())
            self.assertTrue((bundle / "share" / "ticketbunker" / "pi-runtime.json").is_file())

    def test_wrong_upstream_checksum_stops_before_bundle_creation(self) -> None:
        builder = load_builder()
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            archive = root / "pi.tar.gz"
            archive.write_bytes(b"not the approved runtime")
            builder.PI_MANIFEST = root / "manifest.json"
            builder.PI_MANIFEST.write_text(
                json.dumps(
                    {
                        "version": "0.84.2",
                        "platforms": {
                            "linux-x64": {
                                "archive": archive.name,
                                "url": "https://example.invalid/pi.tar.gz",
                                "sha256": "0" * 64,
                                "format": "tar.gz",
                                "executable": "pi/pi",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            fake_tb = root / "tb"
            fake_tb.write_text("tb", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                builder.build(
                    argparse.Namespace(
                        platform="linux-x64",
                        tb=fake_tb,
                        pi_archive=archive,
                        output_dir=root / "out",
                    )
                )


if __name__ == "__main__":
    unittest.main()
