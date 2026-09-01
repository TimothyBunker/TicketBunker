"""Bounded release discovery for the standalone application.

The app never loads remote HTML into its privileged window and never executes
an update payload.  This module reads one small, typed GitHub release document,
selects an installer for the current host, and returns a validated HTTPS URL
for the operating system browser to open.
"""

from __future__ import annotations

import datetime
import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import urlsplit

from . import __version__, config, platform_services


LATEST_RELEASE_API = (
    "https://api.github.com/repos/TimothyBunker/TicketBunker/releases/latest"
)
RELEASE_PAGE_PREFIX = "/TimothyBunker/TicketBunker/releases/tag/"
RELEASE_DOWNLOAD_PREFIX = "/TimothyBunker/TicketBunker/releases/download/"
MAX_RELEASE_BYTES = 256 * 1024
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class ReleaseCheckError(ValueError):
    """The release service was unavailable or returned an unsafe shape."""


def _version(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value.strip())
    if not match:
        raise ReleaseCheckError("The release service returned an invalid version.")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _official_url(raw: Any, prefix: str) -> str:
    value = str(raw or "").strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or not parsed.path.startswith(prefix)
        or parsed.query
        or parsed.fragment
    ):
        raise ReleaseCheckError("The release service returned an unsafe download address.")
    return value


def _asset_score(name: str, runtime_key: str) -> int:
    lowered = name.casefold()
    architecture = runtime_key.rsplit("-", 1)[-1]
    if runtime_key.startswith("windows-"):
        if not lowered.endswith("-setup.exe"):
            return -1
        expected = "arm64" if architecture == "arm64" else "x64"
        return 100 if expected in lowered else -1
    if runtime_key.startswith("linux-"):
        expected = ("arm64", "aarch64") if architecture == "arm64" else ("amd64", "x86_64")
        if not any(value in lowered for value in expected):
            return -1
        if lowered.endswith(".deb"):
            return 100
        if lowered.endswith(".appimage"):
            return 80
        return -1
    if runtime_key.startswith("darwin-") and lowered.endswith(".dmg"):
        expected = "aarch64" if architecture == "arm64" else "x64"
        return 100 if expected in lowered else 60
    return -1


def _selected_asset(payload: dict[str, Any], runtime_key: str) -> dict[str, str] | None:
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return None
    candidates: list[tuple[int, str, str]] = []
    for value in assets:
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or "")
        score = _asset_score(name, runtime_key)
        if score < 0:
            continue
        try:
            url = _official_url(value.get("browser_download_url"), RELEASE_DOWNLOAD_PREFIX)
        except ReleaseCheckError:
            continue
        candidates.append((score, name, url))
    if not candidates:
        return None
    _score, name, url = max(candidates, key=lambda item: item[0])
    return {"name": name, "url": url}


def state(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    stored = (settings if settings is not None else config.load()).get("release_check")
    cached = stored if isinstance(stored, dict) else {}
    latest = str(cached.get("latest") or "")
    try:
        available = bool(latest and _version(latest) > _version(__version__))
    except ReleaseCheckError:
        available = False
    try:
        release_url = _official_url(cached.get("release_url"), RELEASE_PAGE_PREFIX)
    except ReleaseCheckError:
        release_url = ""
    try:
        download_url = _official_url(
            cached.get("download_url"), RELEASE_DOWNLOAD_PREFIX
        )
    except ReleaseCheckError:
        download_url = ""
    return {
        "current": __version__,
        "latest": latest,
        "available": available,
        "checked_at": str(cached.get("checked_at") or ""),
        "attempted_at": str(cached.get("attempted_at") or ""),
        "release_url": release_url,
        "download_url": download_url,
        "asset": str(cached.get("asset") or ""),
    }


def _download_json(request: urllib.request.Request, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > MAX_RELEASE_BYTES:
            raise ReleaseCheckError("The release response was too large.")
        raw = response.read(MAX_RELEASE_BYTES + 1)
    if len(raw) > MAX_RELEASE_BYTES:
        raise ReleaseCheckError("The release response was too large.")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ReleaseCheckError("The release service returned an invalid response.")
    return value


def check(
    *,
    timeout: float = 5.0,
    fetcher: Callable[[urllib.request.Request, float], dict[str, Any]] = _download_json,
) -> dict[str, Any]:
    settings = config.load()
    cached = settings.get("release_check")
    attempt = dict(cached) if isinstance(cached, dict) else {}
    attempted_at = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds"
    )
    attempt["attempted_at"] = attempted_at
    settings["release_check"] = attempt
    config.save(settings)
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"TicketBunker/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        payload = fetcher(request, timeout)
        latest = str(payload.get("tag_name") or "").removeprefix("v")
        _version(latest)
        release_url = _official_url(payload.get("html_url"), RELEASE_PAGE_PREFIX)
        platform = platform_services.current()
        asset = _selected_asset(payload, platform.runtime_key or "unsupported")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ReleaseCheckError(
                "No public TicketBunker release is available yet."
            ) from exc
        raise ReleaseCheckError(
            "TicketBunker could not check for updates. Check your connection and retry."
        ) from exc
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        if isinstance(exc, ReleaseCheckError):
            raise
        raise ReleaseCheckError(
            "TicketBunker could not check for updates. Check your connection and retry."
        ) from exc

    settings["release_check"] = {
        "latest": latest,
        "checked_at": attempted_at,
        "attempted_at": attempted_at,
        "release_url": release_url,
        "download_url": str((asset or {}).get("url") or ""),
        "asset": str((asset or {}).get("name") or ""),
    }
    config.save(settings)
    return state(settings)


__all__ = ["LATEST_RELEASE_API", "ReleaseCheckError", "check", "state"]
