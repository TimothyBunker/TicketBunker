#!/usr/bin/env python3
"""Build a portable TicketBunker + pinned pi release archive.

The official pi archive is verified before extraction.  The resulting bundle
also contains a per-file manifest, allowing installed TicketBunker to verify
the complete pi distribution every time it discovers the bundled runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent.parent
PI_MANIFEST = ROOT / "ticketbunker" / "runtime" / "pi-runtime.json"
PACKAGE_VERSION = ROOT / "pyproject.toml"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_version() -> str:
    for line in PACKAGE_VERSION.read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split("=", 1)[1].strip().strip('"')
    raise ValueError("pyproject.toml has no project version")


def current_platform() -> str:
    import platform

    systems = {"linux": "linux", "darwin": "darwin", "win32": "windows"}
    machines = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}
    try:
        return f"{systems[sys.platform]}-{machines[platform.machine().lower()]}"
    except KeyError as exc:
        raise ValueError(f"unsupported release platform: {sys.platform}/{platform.machine()}") from exc


def safe_member(name: str) -> PurePosixPath:
    value = PurePosixPath(name)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"unsafe archive member: {name}")
    return value


def safe_extract(archive: Path, destination: Path, archive_format: str) -> None:
    """Extract regular files/directories only, bounded below destination."""
    destination.mkdir(parents=True, exist_ok=True)
    if archive_format == "zip":
        with zipfile.ZipFile(archive) as source:
            for info in source.infolist():
                member = safe_member(info.filename)
                target = destination.joinpath(*member.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                unix_mode = (info.external_attr >> 16) & 0o170000
                if unix_mode == stat.S_IFLNK:
                    raise ValueError(f"archive links are not permitted: {info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(info) as incoming, target.open("wb") as output:
                    shutil.copyfileobj(incoming, output)
    else:
        with tarfile.open(archive, "r:gz") as source:
            for info in source.getmembers():
                member = safe_member(info.name)
                target = destination.joinpath(*member.parts)
                if info.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not info.isfile() or info.issym() or info.islnk():
                    raise ValueError(f"archive links and special files are not permitted: {info.name}")
                incoming = source.extractfile(info)
                if incoming is None:
                    raise ValueError(f"cannot read archive member: {info.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with incoming, target.open("wb") as output:
                    shutil.copyfileobj(incoming, output)
                target.chmod(info.mode & 0o777)


def obtain_archive(entry: dict[str, str], supplied: Path | None, temporary: Path) -> Path:
    if supplied is not None:
        archive = supplied.resolve()
    else:
        archive = temporary / entry["archive"]
        print(f"downloading {entry['url']}")
        try:
            with urllib.request.urlopen(entry["url"], timeout=90) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
        except (OSError, urllib.error.URLError) as exc:
            raise ValueError(f"could not download pi: {exc}; pass --pi-archive after downloading it") from exc
    if not archive.is_file():
        raise ValueError(f"pi archive does not exist: {archive}")
    actual = sha256(archive)
    if actual != entry["sha256"]:
        raise ValueError(f"pi archive checksum mismatch: expected {entry['sha256']}, got {actual}")
    return archive


def copy_file(source: Path, destination: Path, executable: bool = False) -> None:
    if not source.is_file():
        raise ValueError(f"required build input is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if executable:
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_bundle_manifest(bundle: Path, tb_version: str, pi_version: str, platform_name: str) -> None:
    files = {}
    for path in sorted(value for value in bundle.rglob("*") if value.is_file() and value.name != "manifest.json"):
        files[path.relative_to(bundle).as_posix()] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    value = {
        "schema_version": 1,
        "ticketbunker_version": tb_version,
        "pi_version": pi_version,
        "platform": platform_name,
        "files": files,
    }
    (bundle / "manifest.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def make_archive(bundle: Path, output_dir: Path, platform_name: str) -> Path:
    if platform_name.startswith("windows-"):
        output = output_dir / f"{bundle.name}.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(value for value in bundle.rglob("*") if value.is_file()):
                archive.write(path, f"{bundle.name}/{path.relative_to(bundle).as_posix()}")
    else:
        output = output_dir / f"{bundle.name}.tar.gz"
        with tarfile.open(output, "w:gz") as archive:
            archive.add(bundle, arcname=bundle.name)
    digest = sha256(output)
    output.with_name(f"{output.name}.sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    return output


def build(args: argparse.Namespace) -> Path:
    pi = json.loads(PI_MANIFEST.read_text(encoding="utf-8"))
    if args.platform not in pi["platforms"]:
        raise ValueError(f"unsupported platform {args.platform!r}; choose {', '.join(sorted(pi['platforms']))}")
    entry = pi["platforms"][args.platform]
    tb_version = package_version()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    windows = args.platform.startswith("windows-")
    bundle_name = f"ticketbunker-{tb_version}-{args.platform}"
    with tempfile.TemporaryDirectory(prefix="ticketbunker-release-") as temporary_name:
        temporary = Path(temporary_name)
        bundle = temporary / bundle_name
        bundle.mkdir()
        archive = obtain_archive(entry, args.pi_archive, temporary)
        tb_name = "tb.exe" if windows else "tb"
        copy_file(args.tb.resolve(), bundle / "bin" / tb_name, executable=True)
        safe_extract(archive, bundle / "libexec" / "ticketbunker", entry["format"])
        pi_executable = bundle / "libexec" / "ticketbunker" / Path(entry["executable"])
        if not pi_executable.is_file():
            raise ValueError(f"pi archive lacks its declared executable: {entry['executable']}")
        pi_executable.chmod(pi_executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        copy_file(ROOT / "ticketbunker" / "harness" / "pi" / "tb.ts", bundle / "share" / "ticketbunker" / "harness" / "pi" / "tb.ts")
        copy_file(ROOT / "ticketbunker" / "licenses" / "pi-MIT.txt", bundle / "share" / "licenses" / "pi" / "LICENSE.txt")
        copy_file(PI_MANIFEST, bundle / "share" / "ticketbunker" / "pi-runtime.json")
        copy_file(ROOT / "THIRD_PARTY_NOTICES.md", bundle / "THIRD_PARTY_NOTICES.md")
        copy_file(ROOT / "tools" / "install.sh", bundle / "install.sh", executable=True)
        copy_file(ROOT / "tools" / "install.ps1", bundle / "install.ps1")
        (bundle / "README.txt").write_text(
            "TicketBunker portable release\n\n"
            "Unix: ./install.sh\nWindows PowerShell: .\\install.ps1\n\n"
            "The bundle includes the official pinned pi standalone runtime. "
            "Run `tb init` in a Git checkout to connect storage, log in, and select an agent.\n",
            encoding="utf-8",
        )
        write_bundle_manifest(bundle, tb_version, pi["version"], args.platform)
        output = make_archive(bundle, output_dir, args.platform)
    print(f"built {output} ({output.stat().st_size} bytes)")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", default=current_platform())
    parser.add_argument("--tb", type=Path, default=ROOT / "dist" / ("tb.exe" if sys.platform == "win32" else "tb"))
    parser.add_argument("--pi-archive", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "releases")
    return parser.parse_args()


def main() -> int:
    try:
        build(parse_args())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
