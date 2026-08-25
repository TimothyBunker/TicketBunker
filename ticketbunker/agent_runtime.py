"""Pinned pi runtime discovery and local agent readiness.

TicketBunker's shared data remains harness-neutral.  This module owns the
machine-local boundary: finding the pi executable shipped beside ``tb``,
checking its integrity/version, and asking pi whether a selected provider and
model are authenticated.  Credential values never cross this boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from . import config


MANIFEST_RESOURCE = ("runtime", "pi-runtime.json")
EXTENSION_RESOURCE = ("harness", "pi", "tb.ts")
AUTH_TIMEOUT_SECONDS = 20
VERSION_TIMEOUT_SECONDS = 10
DOWNLOAD_TIMEOUT_SECONDS = 90


@dataclass(frozen=True)
class Runtime:
    path: str
    origin: str
    version: str = ""
    ready: bool = False
    detail: str = ""
    integrity: str = "unverified"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimeInstallError(Exception):
    def __init__(self, summary: str, action: str):
        super().__init__(summary)
        self.summary = summary
        self.action = action


def manifest() -> dict[str, Any]:
    target = resources.files("ticketbunker")
    for part in MANIFEST_RESOURCE:
        target = target.joinpath(part)
    return json.loads(target.read_text(encoding="utf-8"))


def pinned_pi_version() -> str:
    return str(manifest()["version"])


def platform_key() -> str | None:
    systems = {"linux": "linux", "darwin": "darwin", "win32": "windows"}
    machines = {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    system = systems.get(sys.platform)
    machine = machines.get(platform.machine().lower())
    return f"{system}-{machine}" if system and machine else None


def data_root() -> Path:
    override = os.environ.get("TB_DATA_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "TicketBunker"
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "ticketbunker"


def cli_argv() -> list[str]:
    """Return a stable invocation for hooks and detached child processes."""
    explicit = os.environ.get("TB_LAUNCHER")
    if explicit:
        return [str(Path(explicit).expanduser().resolve())]
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]
    invoked = Path(sys.argv[0]).expanduser()
    if invoked.name in {"tb", "tb.exe"} and invoked.exists():
        return [str(invoked.resolve())]
    return [sys.executable, "-m", "ticketbunker.cli"]


def _bundle_roots() -> list[Path]:
    roots: list[Path] = []
    executable = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).expanduser()
    try:
        executable = executable.resolve()
    except OSError:
        pass
    if executable.name in {"tb", "tb.exe"}:
        roots.append(executable.parent.parent)
    package_root = Path(__file__).resolve().parent
    roots.extend((package_root, package_root.parent))
    found: list[Path] = []
    for root in roots:
        if root not in found:
            found.append(root)
    return found


def _bundle_candidates() -> list[tuple[Path, str]]:
    executable = "pi.exe" if sys.platform == "win32" else "pi"
    candidates: list[tuple[Path, str]] = []
    for root in _bundle_roots():
        candidates.extend(
            (
                (root / "libexec" / "ticketbunker" / "pi" / executable, "bundled"),
                (root / "_runtime" / "pi" / executable, "bundled"),
            )
        )
    local = data_root() / "runtime" / "pi" / pinned_pi_version() / "pi" / executable
    candidates.append((local, "managed"))
    return candidates


def managed_root() -> Path:
    return data_root() / "runtime" / "pi" / pinned_pi_version()


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _bundle_integrity_error(path: Path) -> str:
    for parent in path.parents:
        candidate = parent / "manifest.json"
        if not candidate.exists():
            continue
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
            files = value.get("files") or {}
            root = Path(str(value.get("root") or parent)).resolve()
            relative = path.resolve().relative_to(root).as_posix()
            entry = files.get(relative) or {}
            if not entry.get("sha256"):
                return "bundle manifest has no pi executable checksum"
            prefix = relative.rsplit("/", 1)[0] + "/"
            runtime_files = {name: details for name, details in files.items() if name.startswith(prefix)}
            if not runtime_files:
                return "bundle manifest has no pi runtime files"
            for name, details in runtime_files.items():
                bundled = root / name
                if not bundled.is_file():
                    return f"bundled pi file is missing: {name}"
                if _sha256(bundled) != str(details.get("sha256") or ""):
                    return f"bundled pi checksum does not match: {name}"
            return ""
        except (OSError, ValueError, TypeError):
            return "bundle manifest is invalid"
    return "bundle manifest is missing"


def _managed_integrity_error(path: Path) -> str:
    root = managed_root()
    candidate = root / "manifest.json"
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
        files = value.get("files") or {}
        relative = path.resolve().relative_to(root.resolve()).as_posix()
        if relative not in files:
            return "managed runtime manifest has no pi executable checksum"
        prefix = relative.rsplit("/", 1)[0] + "/"
        runtime_files = {name: details for name, details in files.items() if name.startswith(prefix)}
        if not runtime_files:
            return "managed runtime manifest has no pi files"
        for name, details in runtime_files.items():
            managed = root / name
            if not managed.is_file():
                return f"managed pi file is missing: {name}"
            if _sha256(managed) != str(details.get("sha256") or ""):
                return f"managed pi checksum does not match: {name}"
        return ""
    except FileNotFoundError:
        return "managed runtime manifest is missing"
    except (OSError, ValueError, TypeError):
        return "managed runtime manifest is invalid"


def _inspect(path: str | Path, origin: str) -> Runtime:
    resolved = Path(path).expanduser()
    if not resolved.exists():
        return Runtime(str(resolved), origin, detail="executable not found")
    integrity = "not-applicable"
    if origin == "bundled":
        integrity_error = _bundle_integrity_error(resolved)
        if integrity_error:
            state = "missing" if "missing" in integrity_error or "no pi" in integrity_error else "failed"
            return Runtime(str(resolved.resolve()), origin, detail=integrity_error, integrity=state)
        integrity = "verified"
    elif origin == "managed":
        integrity_error = _managed_integrity_error(resolved)
        if integrity_error:
            state = "missing" if "missing" in integrity_error or "no pi" in integrity_error else "failed"
            return Runtime(str(resolved.resolve()), origin, detail=integrity_error, integrity=state)
        integrity = "verified"
    try:
        completed = subprocess.run(
            [str(resolved), "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Runtime(str(resolved.resolve()), origin, detail=f"cannot run pi: {exc}", integrity=integrity)
    output = (completed.stdout or completed.stderr).strip()
    version = output.splitlines()[0].strip() if output else ""
    if completed.returncode != 0:
        detail = output.splitlines()[-1] if output else f"pi exited {completed.returncode}"
        return Runtime(str(resolved.resolve()), origin, version=version, detail=detail, integrity=integrity)
    if origin in {"bundled", "managed"} and version != pinned_pi_version():
        return Runtime(
            str(resolved.resolve()), origin, version=version,
            detail=f"expected pi {pinned_pi_version()}, found {version or 'an unknown version'}",
            integrity=integrity,
        )
    return Runtime(str(resolved.resolve()), origin, version=version, ready=True, integrity=integrity)


def discover() -> Runtime:
    """Find pi in deterministic priority order and prove it can start."""
    configured = config.load().get("agent") or {}
    if not isinstance(configured, dict):
        configured = {}
    explicit = os.environ.get("TB_AGENT_PI_BIN") or os.environ.get("TB_PI_BIN")
    if explicit:
        return _inspect(explicit, "override")
    for path, origin in _bundle_candidates():
        if path.is_file():
            return _inspect(path, origin)
    if configured.get("pi_bin"):
        return _inspect(str(configured["pi_bin"]), "configured")
    found = shutil.which("pi")
    if found:
        return _inspect(found, "system")
    return Runtime("", "missing", detail="no pi runtime found")


def _safe_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise ValueError(f"unsafe archive member: {name}")
    value = PurePosixPath(name)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"unsafe archive member: {name}")
    return value


def _safe_extract(archive: Path, destination: Path, archive_format: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if archive_format == "zip":
        with zipfile.ZipFile(archive) as source:
            for info in source.infolist():
                member = _safe_member(info.filename)
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
                member = _safe_member(info.name)
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


def _write_managed_manifest(root: Path, platform_name: str, archive_sha256: str) -> None:
    files = {}
    for path in sorted(value for value in root.rglob("*") if value.is_file() and value.name != "manifest.json"):
        files[path.relative_to(root).as_posix()] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    value = {
        "schema_version": 1,
        "pi_version": pinned_pi_version(),
        "platform": platform_name,
        "upstream_archive_sha256": archive_sha256,
        "files": files,
    }
    (root / "manifest.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _save_runtime_path(runtime: Runtime) -> None:
    cfg = config.load()
    selected = cfg.get("agent") or {}
    if not isinstance(selected, dict):
        selected = {}
    cfg["agent"] = {
        **selected,
        "enabled": True,
        "harness": "pi",
        "pi_bin": runtime.path,
    }
    config.save(cfg)


def _record_runtime_path(runtime: Runtime) -> None:
    try:
        _save_runtime_path(runtime)
    except OSError as exc:
        raise RuntimeInstallError(
            f"Pi is ready, but TicketBunker could not save its path: {exc}",
            f"make {config.GLOBAL_CONFIG.parent} writable, then run tb init again",
        ) from exc


def _manual_runtime_action(entry: dict[str, Any], executable: Path) -> str:
    return (
        f"download {entry['url']}, verify SHA-256 {entry['sha256']}, and extract it so "
        f"{executable} exists; then run tb init again, or use tb init --core-only"
    )


def ensure_runtime(reporter: Callable[[str], None] | None = None) -> Runtime:
    """Return a healthy pi runtime, installing the pinned distribution if needed."""
    current = discover()
    explicit = os.environ.get("TB_AGENT_PI_BIN") or os.environ.get("TB_PI_BIN")
    pinned_or_deliberate = current.origin != "system" or current.version == pinned_pi_version()
    if current.ready and (pinned_or_deliberate or explicit):
        _record_runtime_path(current)
        return current
    if explicit:
        raise RuntimeInstallError(
            f"The explicit Pi runtime cannot start ({current.detail}).",
            f"fix or unset TB_AGENT_PI_BIN={explicit!r}, then run tb init again",
        )
    platform_name = platform_key()
    runtime_manifest = manifest()
    entry = (runtime_manifest.get("platforms") or {}).get(platform_name or "")
    destination = managed_root()
    executable = destination / Path(str(entry["executable"])) if isinstance(entry, dict) else destination / "pi"
    if not platform_name or not isinstance(entry, dict):
        supported = ", ".join(sorted((runtime_manifest.get("platforms") or {}).keys()))
        raise RuntimeInstallError(
            f"No automatic Pi runtime is published for {sys.platform}/{platform.machine()}.",
            f"supported platforms: {supported}; provide TB_AGENT_PI_BIN or run tb init --core-only",
        )
    if reporter:
        reporter(f"agent runtime: downloading verified Pi {pinned_pi_version()} for {platform_name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".pi-install-", dir=destination.parent) as temporary_name:
            temporary = Path(temporary_name)
            archive = temporary / str(entry["archive"])
            request = urllib.request.Request(
                str(entry["url"]),
                headers={"User-Agent": "TicketBunker runtime installer"},
            )
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
            actual = _sha256(archive)
            if actual != str(entry["sha256"]):
                raise ValueError(f"downloaded archive checksum was {actual}, expected {entry['sha256']}")
            staging = temporary / "runtime"
            _safe_extract(archive, staging, str(entry["format"]))
            staged_executable = staging / Path(str(entry["executable"]))
            if not staged_executable.is_file():
                raise ValueError(f"archive lacks {entry['executable']}")
            staged_executable.chmod(
                staged_executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
            _write_managed_manifest(staging, platform_name, str(entry["sha256"]))
            if destination.exists():
                concurrent = _inspect(executable, "managed") if executable.is_file() else None
                if concurrent is not None and concurrent.ready:
                    _record_runtime_path(concurrent)
                    if reporter:
                        reporter(f"agent runtime: another init installed and configured {concurrent.path}")
                    return concurrent
                backup = destination.with_name(f"{destination.name}.invalid-{os.getpid()}")
                if backup.exists():
                    raise ValueError(f"cannot preserve invalid runtime because {backup} already exists")
                destination.replace(backup)
                if reporter:
                    reporter(f"agent runtime: preserved the invalid prior copy at {backup}")
            staging.replace(destination)
    except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile, urllib.error.URLError) as exc:
        raise RuntimeInstallError(
            f"Could not install Pi {pinned_pi_version()}: {exc}",
            _manual_runtime_action(entry, executable),
        ) from exc
    installed = _inspect(executable, "managed")
    if not installed.ready:
        raise RuntimeInstallError(
            f"Pi was downloaded but cannot start ({installed.detail}).",
            _manual_runtime_action(entry, executable),
        )
    _record_runtime_path(installed)
    if reporter:
        reporter(f"agent runtime: installed and configured {installed.path}")
    return installed


def _pi_settings_dir() -> Path:
    override = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(override).expanduser() if override else Path.home() / ".pi" / "agent"


def pi_defaults() -> tuple[str, str]:
    """Read only pi's non-secret default provider/model selection."""
    path = _pi_settings_dir() / "settings.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    if not isinstance(value, dict):
        return "", ""
    return str(value.get("defaultProvider") or ""), str(value.get("defaultModel") or "")


def auth_check(runtime: Runtime, provider: str, model: str, *, repo: Path | None = None) -> dict[str, Any]:
    if not runtime.ready:
        return {"ready": False, "status": "runtime-unavailable", "detail": runtime.detail}
    try:
        completed = subprocess.run(
            [runtime.path, "auth", "check", "--provider", provider, "--model", model, "--json", "--no-refresh"],
            cwd=str(repo) if repo else None,
            capture_output=True,
            text=True,
            timeout=AUTH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ready": False, "status": "auth-check-failed", "detail": str(exc)}
    try:
        value = json.loads(completed.stdout.strip())
    except (ValueError, TypeError):
        value = {}
    status = str(value.get("status") or "")
    ready = completed.returncode == 0 and status == "ready"
    return {
        "ready": ready,
        "status": status or "auth-check-failed",
        "provider": provider,
        "model": model,
        "auth_type": str(value.get("authType") or ""),
        "detail": "" if ready else ((completed.stderr or completed.stdout).strip().splitlines() or ["pi login is not ready"])[-1],
    }


def state(repo: Path | None = None) -> dict[str, Any]:
    cfg = config.load()
    selected = cfg.get("agent") or {}
    if not isinstance(selected, dict):
        selected = {}
    if selected.get("enabled") is False:
        return {
            "ready": True,
            "enabled": False,
            "status": "disabled",
            "summary": "Automatic agent execution is disabled on this machine.",
            "action": "run tb init without --core-only to configure it",
        }
    runtime = discover()
    provider = str(selected.get("provider") or "")
    model = str(selected.get("model") or "")
    if not provider or not model:
        default_provider, default_model = pi_defaults()
        provider = provider or default_provider
        model = model or default_model
    result: dict[str, Any] = {
        "ready": False,
        "enabled": True,
        "status": "runtime-unavailable",
        "runtime": runtime.as_dict(),
        "provider": provider,
        "model": model,
    }
    if not runtime.ready:
        explicit = os.environ.get("TB_AGENT_PI_BIN") or os.environ.get("TB_PI_BIN")
        action = (
            f"fix or unset TB_AGENT_PI_BIN={explicit!r}, then run tb init again"
            if explicit
            else "run tb init to download and configure the pinned Pi runtime automatically, or use tb init --core-only"
        )
        result.update(
            summary=f"Pi cannot start ({runtime.detail}).",
            action=action,
        )
        return result
    if not provider or not model:
        result.update(
            status="selection-required",
            summary="No Pi provider and model have been selected.",
            action="run tb init in an interactive terminal and choose /login then /model",
        )
        return result
    authentication = auth_check(runtime, provider, model, repo=repo)
    result["authentication"] = authentication
    if not authentication["ready"]:
        result.update(
            status="login-required",
            summary=f"Pi is not logged in for {provider} / {model}.",
            action="run tb init in an interactive terminal and choose /login then /model",
        )
        return result
    result.update(
        ready=True,
        status="ready",
        summary=f"Pi {runtime.version} · {provider} / {model} · {authentication.get('auth_type') or 'authenticated'}",
        action="",
    )
    return result


def save_selection(provider: str, model: str) -> None:
    cfg = config.load()
    prior = cfg.get("agent") or {}
    if not isinstance(prior, dict):
        prior = {}
    cfg["agent"] = {
        **prior,
        "enabled": True,
        "harness": "pi",
        "provider": provider,
        "model": model,
    }
    config.save(cfg)


def set_core_only() -> None:
    cfg = config.load()
    prior = cfg.get("agent") or {}
    if not isinstance(prior, dict):
        prior = {}
    cfg["agent"] = {**prior, "enabled": False}
    config.save(cfg)


def onboard(repo: Path, *, interactive: bool) -> dict[str, Any]:
    """Adopt a ready pi selection or open pi once for login/model setup."""
    current = state(repo)
    if not current.get("enabled", True):
        cfg = config.load()
        selected = cfg.get("agent") or {}
        if not isinstance(selected, dict):
            selected = {}
        cfg["agent"] = {**selected, "enabled": True}
        config.save(cfg)
        current = state(repo)
    if current["ready"] and current.get("enabled", True):
        if current.get("provider") and current.get("model"):
            save_selection(str(current["provider"]), str(current["model"]))
        return current
    runtime_value = current.get("runtime") or {}
    runtime = Runtime(**runtime_value) if runtime_value else discover()
    if not runtime.ready or not interactive:
        return current
    print("agent setup: Pi will open once; use /login, then /model, then /exit")
    try:
        completed = subprocess.run([runtime.path], cwd=str(Path(repo).resolve()))
    except OSError:
        return state(repo)
    if completed.returncode not in (0, 130):
        return state(repo)
    provider, model = pi_defaults()
    if provider and model:
        save_selection(provider, model)
    return state(repo)


def extension_path() -> Path:
    """Return the packaged adapter as a real file, extracting zipapps once."""
    target = resources.files("ticketbunker")
    for part in EXTENSION_RESOURCE:
        target = target.joinpath(part)
    direct = Path(str(target))
    if direct.is_file():
        return direct.resolve()
    content = target.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    cached = data_root() / "assets" / digest / "harness" / "pi" / "tb.ts"
    if not cached.exists() or _sha256(cached) != digest:
        cached.parent.mkdir(parents=True, exist_ok=True)
        temporary = cached.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(cached)
    return cached
