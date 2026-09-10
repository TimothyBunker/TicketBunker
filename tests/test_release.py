from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent


def publication_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    # WindowsApps/System32 bash.exe may be a WSL launcher: that is a different
    # OS environment and does not preserve this native process's arguments or
    # environment contract. Prefer Git for Windows' own Bash when installed.
    git = shutil.which("git")
    if git:
        for parent in list(Path(git).parents)[:3]:
            candidate = parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    return None


@unittest.skipUnless(publication_bash(), "Ubuntu release shell needs Bash (native Git Bash on Windows; a WSL launcher is not equivalent)")
class ReleasePublicationTests(unittest.TestCase):
    def test_artifact_only_publication_names_repository_and_preserves_tag_flags(self) -> None:
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        publish = workflow.split("      - name: Publish GitHub release\n", 1)[1]
        script = textwrap.dedent(publish.split("        run: |\n", 1)[1])
        # Execute the actual workflow shell in a directory without a checkout.
        # The fake CLI records arguments only; no release or network request occurs.
        with tempfile.TemporaryDirectory() as temporary:
            for tag, prerelease in (("v0.6.0", False), ("v0.6.0-rc1", True)):
                with self.subTest(tag=tag):
                    result = subprocess.run(
                        [publication_bash(), "--noprofile", "--norc", "-e"],
                        input="gh() { printf '%s\\n' \"$@\"; }\n" + script,
                        cwd=temporary,
                        env={**os.environ, "GITHUB_REPOSITORY": "example/ticketbunker",
                             "GITHUB_REF_NAME": tag, "GH_TOKEN": "test-only"},
                        capture_output=True, text=True, check=True,
                    )
                    args = result.stdout.splitlines()
                    self.assertEqual(args[:3], ["release", "create", tag])
                    self.assertIn("--repo", args)
                    self.assertEqual(args[args.index("--repo") + 1], "example/ticketbunker")
                    self.assertIn("--generate-notes", args)
                    self.assertIn("--verify-tag", args)
                    self.assertEqual("--prerelease" in args, prerelease)


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

            with mock.patch.object(builder, "smoke_auth_bridge") as smoke:
                output = builder.build(
                    argparse.Namespace(
                        platform="linux-x64",
                        tb=fake_tb,
                        pi_archive=archive,
                        output_dir=output_dir,
                    )
                )

            smoke.assert_called_once()

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

    def test_auth_bridge_smoke_requires_the_typed_catalog_event(self) -> None:
        builder = load_builder()
        completed = mock.Mock(
            returncode=0,
            stdout=(
                '{"method":"notify","message":"TB_AUTH_EVENT:'
                '{\\"kind\\":\\"catalog\\",\\"providers\\":[]}"}\n'
            ),
        )

        with mock.patch.object(builder.subprocess, "run", return_value=completed):
            builder.smoke_auth_bridge(Path("pi"), Path("tb_auth.ts"))

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
