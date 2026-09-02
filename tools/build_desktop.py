#!/usr/bin/env python3
"""Stage the frozen TicketBunker core and verified Pi runtime, then bundle the app."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from tools import build_release
except ModuleNotFoundError:  # direct `python tools/build_desktop.py`
    import build_release


ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "desktop"
PI_MANIFEST = ROOT / "ticketbunker" / "runtime" / "pi-runtime.json"
NATIVE_PACKAGE_SUFFIXES = {".appimage", ".deb", ".dmg", ".exe", ".msi", ".rpm"}


def target_triple() -> str:
    process = subprocess.run(
        ["rustc", "--print", "host-tuple"], capture_output=True, text=True
    )
    if process.returncode != 0 or not process.stdout.strip():
        raise ValueError("rustc could not identify the desktop target triple")
    return process.stdout.strip()


def default_bundles() -> str:
    if sys.platform == "darwin":
        return "app,dmg"
    if sys.platform == "win32":
        return "nsis"
    return "deb,appimage"


def build_tool(name: str) -> str:
    """Resolve command shims such as npm.cmd before launching without a shell."""
    executable = shutil.which(name)
    if not executable:
        raise ValueError(
            f"{name} is required to build the desktop app but was not found on PATH"
        )
    return executable


def _reset_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def stage(
    *,
    platform_name: str,
    triple: str,
    tb: Path,
    pi_archive: Path | None,
) -> tuple[Path, Path]:
    manifest = json.loads(PI_MANIFEST.read_text(encoding="utf-8"))
    if platform_name not in manifest["platforms"]:
        raise ValueError(f"the Pi manifest does not support {platform_name}")
    entry = manifest["platforms"][platform_name]
    binaries = DESKTOP / "src-tauri" / "binaries"
    resources = DESKTOP / "src-tauri" / "resources"
    _reset_directory(binaries)
    _reset_directory(resources)

    windows = platform_name.startswith("windows-")
    extension = ".exe" if windows else ""
    sidecar = binaries / f"tb-{triple}{extension}"
    build_release.copy_file(tb.resolve(), sidecar, executable=True)

    with tempfile.TemporaryDirectory(prefix="ticketbunker-desktop-") as temporary_name:
        temporary = Path(temporary_name)
        archive = build_release.obtain_archive(entry, pi_archive, temporary)
        build_release.safe_extract(
            archive,
            resources / "libexec" / "ticketbunker",
            entry["format"],
        )

    pi_executable = resources / "libexec" / "ticketbunker" / Path(entry["executable"])
    if not pi_executable.is_file():
        raise ValueError(f"Pi archive lacks its declared executable: {entry['executable']}")
    pi_executable.chmod(
        pi_executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    build_release.copy_file(
        ROOT / "ticketbunker" / "harness" / "pi" / "tb.ts",
        resources / "share" / "ticketbunker" / "harness" / "pi" / "tb.ts",
    )
    build_release.copy_file(
        ROOT / "ticketbunker" / "harness" / "pi" / "tb_auth.ts",
        resources / "share" / "ticketbunker" / "harness" / "pi" / "tb_auth.ts",
    )
    build_release.copy_file(
        ROOT / "ticketbunker" / "licenses" / "pi-MIT.txt",
        resources / "share" / "licenses" / "pi" / "LICENSE.txt",
    )
    build_release.copy_file(
        PI_MANIFEST,
        resources / "share" / "ticketbunker" / "pi-runtime.json",
    )
    build_release.copy_file(
        ROOT / "THIRD_PARTY_NOTICES.md", resources / "THIRD_PARTY_NOTICES.md"
    )
    build_release.write_bundle_manifest(
        resources,
        build_release.package_version(),
        str(manifest["version"]),
        platform_name,
    )
    return sidecar, resources


def publish_native_packages(
    bundle_root: Path, release_root: Path, *, version: str
) -> list[Path]:
    """Flatten this build's native installers with adjacent checksums."""
    packages = sorted(
        path
        for path in bundle_root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in NATIVE_PACKAGE_SUFFIXES
        and version in path.name
    )
    if not packages:
        raise ValueError("Tauri produced no native installer to publish")
    release_root.mkdir(parents=True, exist_ok=True)
    published: list[Path] = []
    for package in packages:
        destination = release_root / package.name
        shutil.copy2(package, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        checksum = destination.with_name(f"{destination.name}.sha256")
        checksum.write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
        published.extend((destination, checksum))
    return published


def build(args: argparse.Namespace) -> None:
    sidecar, resources = stage(
        platform_name=args.platform,
        triple=args.target_triple,
        tb=args.tb,
        pi_archive=args.pi_archive,
    )
    print(f"staged core: {sidecar}")
    print(f"staged runtime: {resources}")
    build_release.smoke_auth_bridge(
        resources / "libexec" / "ticketbunker" / Path(
            json.loads(PI_MANIFEST.read_text(encoding="utf-8"))["platforms"][args.platform]["executable"]
        ),
        resources / "share" / "ticketbunker" / "harness" / "pi" / "tb_auth.ts",
    )
    print("agent auth bridge: verified")
    if args.stage_only:
        return
    command = [
        build_tool("npm"),
        "run",
        "tauri",
        "--",
        "build",
        "--config",
        "src-tauri/tauri.bundle.conf.json",
        "--bundles",
        args.bundles,
    ]
    environment = os.environ.copy()
    environment.setdefault("RUST_MIN_STACK", "33554432")
    environment.setdefault("CARGO_BUILD_JOBS", "1")
    process = subprocess.run(command, cwd=DESKTOP, env=environment)
    if process.returncode != 0:
        raise ValueError("Tauri could not build the native TicketBunker bundle")
    published = publish_native_packages(
        DESKTOP / "src-tauri" / "target" / "release" / "bundle",
        ROOT / "dist" / "releases",
        version=build_release.package_version(),
    )
    for artifact in published:
        print(f"release asset: {artifact}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", default=build_release.current_platform())
    parser.add_argument("--target-triple", default=target_triple())
    parser.add_argument(
        "--tb",
        type=Path,
        default=ROOT / "dist" / ("tb.exe" if sys.platform == "win32" else "tb"),
    )
    parser.add_argument("--pi-archive", type=Path)
    parser.add_argument("--bundles", default=default_bundles())
    parser.add_argument("--stage-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        build(parse_args())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"desktop build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
