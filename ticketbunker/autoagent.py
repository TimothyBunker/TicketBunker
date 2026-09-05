"""User-requested synthesis with lightweight Git lifecycle tracking.

Git hooks must stay fast and must never spend an agent call.  They record the
newest ticket-branch state below ``.git`` so the viewer can explain that work
changed after the last synthesis.  Only an explicit user request queues the
detached worker, which refreshes deterministic observations before running the
ordinary gated agent pipeline.  No alternate publish path exists.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import agent_runtime, commands, config, layout, platform_services
from .ledger import Ledger
from .processors import anchorcheck

try:
    import fcntl
except ImportError:  # Windows Git hooks still invoke the same Python worker.
    fcntl = None  # type: ignore[assignment]
    import msvcrt


HOOK_MARKER = "# TicketBunker automatic agent hook"
LEGACY_HOOK_MARKER = "# TicketBunker hook"
HOOK_NAMES = ("post-checkout", "post-commit", "post-merge", "post-rewrite")
DEBOUNCE_SECONDS = 1.0
STATE_DIR = "ticketbunker/automatic-agent"
FINGERPRINT_FILE = "fingerprints.json"
CHANGE_FILE = "changes.json"
RUNNING_FILE = "running.json"
USER_REQUEST_EVENT = "user-requested-synthesis"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def was_user_requested(value: dict[str, Any] | None) -> bool:
    """Whether one queued/running/finished state came from the explicit UI/CLI action.

    Older TicketBunker versions ran synthesis from Git events and may leave a
    failed ``last.json`` behind after upgrade.  Those records must remain local
    audit history without being presented as a request the user made.
    """
    if not isinstance(value, dict):
        return False
    if value.get("requested") is True:
        return True
    return USER_REQUEST_EVENT in (value.get("events") or [])


def _git(repo: Path, *args: str) -> tuple[str, int]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.stdout.strip(), proc.returncode


def _git_dir(repo: Path) -> Path:
    value, status = _git(repo, "rev-parse", "--absolute-git-dir")
    if status != 0:
        raise ValueError(f"{repo} is not a Git repository")
    return Path(value).resolve()


def _hooks_dir(repo: Path) -> Path:
    configured, status = _git(repo, "config", "--path", "--get", "core.hooksPath")
    if status == 0 and configured:
        path = Path(configured).expanduser()
        return path.resolve() if path.is_absolute() else (repo / path).resolve()
    common, common_status = _git(repo, "rev-parse", "--git-common-dir")
    if common_status != 0:
        raise ValueError(f"{repo} is not a Git repository")
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = repo / common_path
    return common_path.resolve() / "hooks"


def _state_dir(repo: Path) -> Path:
    return _git_dir(repo) / STATE_DIR


def log_path(repo: Path) -> Path:
    """Return the exact machine-local synthesis log path."""
    return _state_dir(Path(repo).resolve()) / "agent.log"


def _launcher() -> list[str]:
    # Pin the hook to the installed/frozen command so GUI Git does not depend
    # on shell activation or a source checkout.
    return agent_runtime.cli_argv()


def _hook_body(event: str, original: Path) -> str:
    # Hooks are shared by linked worktrees; resolve the active one at runtime.
    windows = platform_services.current().is_windows
    shell_values = [value.replace("\\", "/") if windows else value for value in _launcher()]
    launch = " ".join(shlex.quote(value) for value in shell_values)
    original_call = ""
    if original.exists():
        original_value = str(original).replace("\\", "/") if windows else str(original)
        quoted = shlex.quote(original_value)
        original_call = (
            f"if [ -x {quoted} ]; then\n"
            f"  {quoted} \"$@\" || exit $?\n"
            "fi\n"
        )
    return (
        "#!/bin/sh\n"
        f"{HOOK_MARKER}\n"
        f"{original_call}"
        "if [ -z \"${TB_AGENT_ACTIVE:-}\" ] && [ -z \"${TB_AGENT_DISABLE:-}\" ]; then\n"
        "  tb_repo=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0\n"
        f"  {launch} _git-event --repo \"$tb_repo\" --event {shlex.quote(event)} "
        "--quiet >/dev/null 2>&1 || true\n"
        "fi\n"
        "exit 0\n"
    )


def install(repo: Path) -> dict[str, list[str]]:
    """Install lifecycle tracking hooks while preserving foreign hooks."""
    repo = Path(repo).resolve()
    hooks = _hooks_dir(repo)
    hooks.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    preserved: list[str] = []
    for name in HOOK_NAMES:
        path = hooks / name
        original = hooks / f"{name}.ticketbunker-original"
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
                ours = HOOK_MARKER in text or LEGACY_HOOK_MARKER in text
            except (OSError, UnicodeDecodeError):
                ours = False
            if not ours:
                if original.exists():
                    raise ValueError(
                        f"cannot preserve {path}: {original.name} already exists; "
                        "restore or rename it, then run tb init"
                    )
                path.replace(original)
                preserved.append(name)
        path.write_text(_hook_body(name, original), encoding="utf-8")
        path.chmod(path.stat().st_mode | 0o111)
        installed.append(name)
    state = _state_dir(repo)
    state.mkdir(parents=True, exist_ok=True)
    runtime = agent_runtime.discover()
    launchers = {}
    if runtime.ready:
        launchers["pi_bin"] = runtime.path
        launchers["pi_version"] = runtime.version
        launchers["pi_origin"] = runtime.origin
    codex = shutil.which("codex")
    if codex:
        launchers["codex_bin"] = codex
    if launchers:
        launchers["default_harness"] = "pi" if launchers.get("pi_bin") else "codex"
        _write_json(state / "profile.json", launchers)
    return {"installed": installed, "preserved": preserved}


def uninstall(repo: Path) -> dict[str, list[str]]:
    repo = Path(repo).resolve()
    hooks = _hooks_dir(repo)
    removed: list[str] = []
    restored: list[str] = []
    for name in HOOK_NAMES:
        path = hooks / name
        original = hooks / f"{name}.ticketbunker-original"
        try:
            ours = path.exists() and HOOK_MARKER in path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            ours = False
        if not ours:
            continue
        path.unlink()
        removed.append(name)
        if original.exists():
            original.replace(path)
            restored.append(name)
    return {"removed": removed, "restored": restored}


def hook_status(repo: Path) -> dict[str, Any]:
    repo = Path(repo).resolve()
    try:
        hooks = _hooks_dir(repo)
    except ValueError:
        return {"installed": [], "missing": list(HOOK_NAMES), "ready": False}
    installed = []
    for name in HOOK_NAMES:
        path = hooks / name
        try:
            if path.exists() and HOOK_MARKER in path.read_text(encoding="utf-8"):
                installed.append(name)
        except (OSError, UnicodeDecodeError):
            pass
    state = read_state(repo)
    return {
        "installed": installed,
        "missing": [name for name in HOOK_NAMES if name not in installed],
        "ready": len(installed) == len(HOOK_NAMES),
        "state": state,
    }


def _read_ticket(bunker: Path) -> dict[str, Any]:
    try:
        value = json.loads(layout.paths(bunker)["ticket"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def resolve_bunker(repo: Path, branch: str) -> Path | None:
    root = layout.find_root(repo)
    if root is None:
        from . import bunkerruntime

        return bunkerruntime.resolve(repo, branch)
    ticket_id = config.extract_ticket_id(branch)
    if ticket_id:
        candidate = layout.bunker_path(root, ticket_id)
        if candidate.is_dir():
            return candidate
        if (root / layout.LOCATOR_FILE).exists():
            # Fresh collaborator checkouts hold only the lightweight index.
            # Queue a known reference immediately; the background worker, not
            # the hook, hydrates its local sparse-checkout path.
            try:
                from . import project, ticketindex

                locator = project.load_locator(root) or {}
                project_key = str(locator.get("project") or "")
                index = ticketindex.read(project.tickets_dir(root, project_key)) or {}
                known = any(
                    value.get("id") == ticket_id
                    for value in index.get("tickets", [])
                    if isinstance(value, dict)
                )
                if known:
                    return candidate
            except Exception:
                return None
    assigned = []
    for bunker in layout.ticket_dirs(layout.bunkers_dir(root)):
        if branch in (_read_ticket(bunker).get("assigned_branches") or []):
            assigned.append(bunker)
    if len(assigned) == 1:
        return assigned[0]
    if not assigned:
        from . import bunkerruntime

        return bunkerruntime.resolve(repo, branch)
    return None


def _locked_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    _acquire_lock(handle)
    return handle


def _acquire_lock(handle, *, nonblocking: bool = False) -> None:
    if fcntl is not None:
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        fcntl.flock(handle.fileno(), flags)
        return
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write("\0")
        handle.flush()
    handle.seek(0)
    mode = msvcrt.LK_NBLCK if nonblocking else msvcrt.LK_LOCK
    try:
        msvcrt.locking(handle.fileno(), mode, 1)
    except OSError as exc:
        if nonblocking:
            raise BlockingIOError from exc
        raise


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _fingerprint_key(bunker: Path, branch: str) -> str:
    return f"{Path(bunker).resolve()}::{branch}"


def _fingerprints(repo: Path) -> dict[str, Any]:
    path = _state_dir(Path(repo).resolve()) / FINGERPRINT_FILE
    if not path.exists():
        return {"schema_version": 1, "branches": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": 1, "branches": {}}
    if not isinstance(value, dict) or not isinstance(value.get("branches"), dict):
        return {"schema_version": 1, "branches": {}}
    return value


def input_fingerprint(
    repo: Path,
    bunker: Path,
    branch: str,
    *,
    head: str = "",
) -> str:
    """Digest every durable input that can change one branch synthesis.

    The report itself and local conversation are deliberately excluded. A
    successful agent publication records this digest locally; later Git or
    ticket events can therefore skip the provider when the inputs are
    identical instead of paying for a no-op report rewrite.
    """
    repo = Path(repo).resolve()
    bunker = Path(bunker).resolve()
    if not head:
        head, _ = _git(repo, "rev-parse", branch)
    digest = hashlib.sha256()
    digest.update(f"ticketbunker-synthesis-v1\0{branch}\0{head}\0".encode())
    paths = layout.paths(bunker)
    candidates = [paths["ticket"], layout.session_config(bunker, branch)]
    candidates.extend(sorted(paths["context"].glob("*.md")))
    candidates.extend(sorted(paths["observations"].glob("*.json")))
    for path in candidates:
        try:
            content = path.read_bytes()
        except OSError:
            content = b""
        digest.update(str(path.relative_to(bunker)).encode(errors="replace"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def synthesis_input_state(repo: Path, bunker: Path, branch: str) -> dict[str, Any]:
    """Compare current inputs with the last successfully published run."""
    repo = Path(repo).resolve()
    bunker = Path(bunker).resolve()
    head, status = _git(repo, "rev-parse", branch)
    if status != 0 or not head:
        return {"status": "unavailable", "current": "", "published": ""}
    current = input_fingerprint(repo, bunker, branch, head=head)
    entry = (_fingerprints(repo).get("branches") or {}).get(
        _fingerprint_key(bunker, branch), {}
    )
    published = str(entry.get("fingerprint") or "") if isinstance(entry, dict) else ""
    if not layout.child_report(bunker, branch).is_file():
        status_name = "missing"
    elif not published:
        status_name = "unchecked"
    elif published != current:
        status_name = "changed"
    else:
        status_name = "current"
    return {
        "status": status_name,
        "current": current,
        "published": published,
        "head": head,
        "published_at": str(entry.get("published_at") or "") if isinstance(entry, dict) else "",
    }


def _record_input_fingerprint(
    repo: Path,
    bunker: Path,
    branch: str,
    fingerprint: str,
    head: str,
) -> None:
    target = _state_dir(Path(repo).resolve())
    target.mkdir(parents=True, exist_ok=True)
    state = _fingerprints(repo)
    branches = state.setdefault("branches", {})
    branches[_fingerprint_key(bunker, branch)] = {
        "fingerprint": fingerprint,
        "head": head,
        "published_at": _now(),
    }
    _write_json(target / FINGERPRINT_FILE, state)


def _spawn_worker(repo: Path) -> None:
    state = _state_dir(repo)
    state.mkdir(parents=True, exist_ok=True)
    log = log_path(repo).open("ab")
    env = dict(os.environ)
    # A PyInstaller one-file parent removes its extraction directory on exit.
    # The detached worker outlives that parent, so it must unpack independently.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    package_parent = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (package_parent, env.get("PYTHONPATH", "")) if value
    )
    try:
        kwargs: dict[str, Any] = {
            "cwd": str(repo),
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": subprocess.STDOUT,
            "env": env,
            "close_fds": True,
        }
        kwargs.update(
            platform_services.current().background_process_options(detached=True)
        )
        subprocess.Popen(
            [*_launcher(), "_agent-worker", "--repo", str(repo)],
            **kwargs,
        )
    finally:
        log.close()


def enqueue(
    repo: Path, event: str, *, spawn: bool = True, bunker: Path | None = None
) -> dict[str, Any]:
    """Coalesce one explicit synthesis request and start its worker."""
    repo = Path(repo).resolve()
    selected = config.load().get("agent") or {}
    if isinstance(selected, dict) and selected.get("enabled") is False:
        return {"queued": False, "reason": "agent_disabled"}
    branch, status = _git(repo, "branch", "--show-current")
    if status != 0 or not branch:
        return {"queued": False, "reason": "detached_or_missing_branch"}
    bunker = Path(bunker) if bunker is not None else resolve_bunker(repo, branch)
    if bunker is None:
        return {"queued": False, "reason": "branch_has_no_ticket", "branch": branch}
    head, _ = _git(repo, "rev-parse", "HEAD")
    state = _state_dir(repo)
    queue = state / "queue.json"
    with _locked_file(state / "queue.lock"):
        prior: dict[str, Any] = {}
        if queue.exists():
            try:
                loaded = json.loads(queue.read_text(encoding="utf-8"))
                prior = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                prior = {}
        same_target = prior.get("branch") == branch and prior.get("head") == head
        events = set(prior.get("events") or []) if same_target else set()
        events.add(event)
        payload = {
            "schema_version": 1,
            "requested": True,
            "repo": str(repo),
            "bunker": str(bunker.resolve()),
            "branch": branch,
            "head": head,
            "events": sorted(events),
            "queued_at": _now(),
            "generation": time.time_ns(),
        }
        _write_json(queue, payload)
    if spawn:
        _spawn_worker(repo)
    return {"queued": True, **payload}


def record_change(repo: Path, event: str) -> dict[str, Any]:
    """Record a Git lifecycle change without queuing or invoking an agent."""
    repo = Path(repo).resolve()
    branch, status = _git(repo, "branch", "--show-current")
    if status != 0 or not branch:
        return {"recorded": False, "reason": "detached_or_missing_branch"}
    bunker = resolve_bunker(repo, branch)
    if bunker is None:
        return {"recorded": False, "reason": "branch_has_no_ticket", "branch": branch}
    head, _ = _git(repo, "rev-parse", "HEAD")
    state = _state_dir(repo)
    path = state / CHANGE_FILE
    with _locked_file(state / "changes.lock"):
        prior: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                prior = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                prior = {}
        same_target = prior.get("branch") == branch and prior.get("head") == head
        events = set(prior.get("events") or []) if same_target else set()
        events.add(event)
        payload = {
            "schema_version": 1,
            "repo": str(repo),
            "bunker": str(bunker.resolve()),
            "branch": branch,
            "head": head,
            "events": sorted(events),
            "changed_at": _now(),
        }
        _write_json(path, payload)
    return {"recorded": True, **payload}


def _claim(repo: Path) -> dict[str, Any] | None:
    state = _state_dir(repo)
    queue = state / "queue.json"
    with _locked_file(state / "queue.lock"):
        if not queue.exists():
            return None
        try:
            value = json.loads(queue.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            queue.unlink(missing_ok=True)
            return None
        queue.unlink(missing_ok=True)
    return value if isinstance(value, dict) else None


def _profile(repo: Path, bunker: Path, branch: str) -> dict[str, str]:
    configured = config.load().get("agent") or {}
    if not isinstance(configured, dict):
        configured = {}
    stamp: dict[str, str] = {}
    report = layout.child_report(bunker, branch)
    if report.exists():
        try:
            stamp = anchorcheck.verified_stamp(report.read_text(encoding="utf-8")) or {}
        except OSError:
            stamp = {}
    local: dict[str, Any] = {}
    local_path = _state_dir(repo) / "profile.json"
    if local_path.exists():
        try:
            loaded = json.loads(local_path.read_text(encoding="utf-8"))
            local = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            local = {}
    profile: dict[str, str] = {}
    sources = {
        "provider": ("TB_AGENT_PROVIDER", "provider"),
        "model": ("TB_AGENT_MODEL", "model"),
        "cmd": ("TB_AGENT_COMMAND", "command"),
        "pi_bin": ("TB_AGENT_PI_BIN", "pi_bin"),
        "codex_bin": ("TB_AGENT_CODEX_BIN", "codex_bin"),
    }
    for key, (environment, setting) in sources.items():
        value = os.environ.get(environment) or configured.get(setting) or local.get(key)
        if not value and key in {"provider", "model"}:
            value = stamp.get(key)
        if value:
            profile[key] = str(value)
    runtime = agent_runtime.discover()
    if "pi_bin" not in profile and runtime.ready:
        profile["pi_bin"] = runtime.path
    harness = os.environ.get("TB_AGENT_HARNESS") or configured.get("harness")
    if not harness and stamp.get("harness") in {"pi", "codex"}:
        harness = stamp["harness"]
    if not harness:
        harness = local.get("default_harness")
    if not harness:
        harness = "pi" if runtime.ready else "codex" if shutil.which("codex") else "pi"
    profile["harness"] = str(harness)
    if stamp.get("harness") != profile["harness"]:
        if not (os.environ.get("TB_AGENT_PROVIDER") or configured.get("provider")):
            profile.pop("provider", None)
        if not (os.environ.get("TB_AGENT_MODEL") or configured.get("model")):
            profile.pop("model", None)
    if profile["harness"] != "pi":
        profile.pop("provider", None)
    if profile["harness"] == "command":
        profile.pop("model", None)
    return profile


def _cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env["TB_AGENT_ACTIVE"] = "1"
    # The run's stdout and stderr share one log; unbuffered output keeps
    # findings in the order they happened.
    env["PYTHONUNBUFFERED"] = "1"
    package_parent = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (package_parent, env.get("PYTHONPATH", "")) if value
    )
    return env


def _run_pipeline(repo: Path, payload: dict[str, Any]) -> tuple[bool, str]:
    branch = str(payload.get("branch") or "")
    bunker = Path(str(payload.get("bunker") or ""))
    current, status = _git(repo, "branch", "--show-current")
    if status != 0 or current != branch:
        return False, f"branch moved to {current or 'detached HEAD'} before synthesis; switch back and retry"
    head, _ = _git(repo, "rev-parse", "HEAD")
    if head != payload.get("head"):
        return False, "HEAD changed before synthesis started; press Update synthesis to use the newer state"
    profile = _profile(repo, bunker, branch)
    base = _launcher()
    refresh = [*base, "refresh", "--repo", str(repo), "--quiet", "--strict"]
    env = _cli_env()
    refreshed = subprocess.run(
        refresh,
        cwd=str(repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **platform_services.current().headless_process_options(),
    )
    if refreshed.returncode != 0:
        output = (refreshed.stdout or "").strip()
        if output:
            print(output, flush=True)
        useful = [
            line.strip()
            for line in output.splitlines()
            if line.strip() and not line.strip().casefold().startswith("diagnostic:")
        ]
        detail = " ".join(useful[:2]) or "Deterministic refresh failed. Run tb refresh to see the failed Git check."
        return False, f"{detail} No agent session was started."
    fingerprint = input_fingerprint(repo, bunker, branch, head=head)
    prior = synthesis_input_state(repo, bunker, branch)
    if prior.get("status") == "current" and prior.get("published") == fingerprint:
        return True, f"synthesis already current for {branch}; provider was not called"
    pending = _pending_verified_report(repo, bunker, branch, head, prior)
    if pending is not None:
        return pending
    command = [
        *base,
        "run",
        str(bunker),
        "--repo",
        str(repo),
        "--branch",
        branch,
        "--expected-head",
        str(payload.get("head") or ""),
    ]
    for key, flag in (
        ("harness", "--harness"),
        ("provider", "--provider"),
        ("model", "--model"),
        ("cmd", "--cmd"),
        ("pi_bin", "--pi-bin"),
        ("codex_bin", "--codex-bin"),
    ):
        if profile.get(key):
            command.extend([flag, profile[key]])
    completed = subprocess.run(
        command,
        cwd=str(repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **platform_services.current().headless_process_options(),
    )
    output = completed.stdout or ""
    if output.strip():
        print(output.rstrip(), flush=True)
    if completed.returncode != 0:
        latest, _ = _git(repo, "rev-parse", "HEAD")
        if latest and latest != payload.get("head"):
            return False, "HEAD changed during synthesis; press Update synthesis to use the newer state"
        return False, failure_detail(output, profile["harness"])
    _record_input_fingerprint(repo, bunker, branch, fingerprint, head)
    return True, f"published {branch} through {profile['harness']}"


def _pending_verified_report(
    repo: Path, bunker: Path, branch: str, head: str, prior: dict[str, Any]
) -> tuple[bool, str] | None:
    """Share a report the model already produced for this exact commit.

    When a run passed every gate but the Bunker (typically its service) did
    not accept the report, the verified child sits in the runtime directory
    with no recorded publication. The next request shares it instead of
    paying for another model run.
    """
    from . import bunkerruntime

    if bunkerruntime.marker(bunker) is None or prior.get("status") != "unchecked":
        return None
    report = layout.child_report(bunker, branch)
    try:
        stamp = anchorcheck.verified_stamp(report.read_text(encoding="utf-8"))
    except OSError:
        return None
    commit = str(stamp.get("commit") or "")
    if not commit or not head.startswith(commit):
        return None
    try:
        bunkerruntime.publish_reports(bunker, branch, Ledger(layout.paths(bunker)["ledger"]))
    except commands.CommandRejected as exc:
        return False, (
            f"the verified report for {branch} is kept on this computer, but the Bunker "
            f"did not accept it: {exc.message}"
        )
    _record_input_fingerprint(repo, bunker, branch, input_fingerprint(repo, bunker, branch, head=head), head)
    return True, f"shared the verified report for {branch}; provider was not called"


def failure_detail(output: str, harness: str) -> str:
    """Name what stopped a synthesis from the run's own output, bounded.

    Blocking findings and the run's own summary come first; warnings never
    do, because they do not stop a publish. Provider and harness messages
    ("usage limit has been reached", "Bad credentials") are what a person
    acts on; file paths and generic exit lines are not.
    """
    skip = ("error: harness exited", "form left at", "draft:", "transcript:", "session complete", "Use View log")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("[error]")]
    summary = next(
        (
            line for line in reversed(lines)
            if line.startswith("Synthesis stopped") or "did not accept" in line
        ),
        "",
    )
    if errors:
        first = errors[0][len("[error]"):].strip()
        lead = summary or "the report failed its checks"
        return f"{harness} synthesis failed: {lead[:150]} First finding: {first[:220]}"
    if summary:
        return f"{harness} synthesis failed: {summary[:240]}"
    candidates = [
        line for line in lines
        if not line.startswith("[warn]") and not any(marker in line for marker in skip)
    ]
    for line in reversed(candidates):
        lowered = line.casefold()
        if any(word in lowered for word in ("error", "limit", "denied", "unauthori", "credential", "quota", "stopped", "rejected", "timed out", "not found")):
            return f"{harness} synthesis failed: {line[:240]}"
    if candidates:
        return f"{harness} synthesis failed: {candidates[-1][:240]}"
    return f"{harness} synthesis failed"


def run_worker(repo: Path, *, debounce: float = DEBOUNCE_SECONDS, once: bool = False) -> int:
    """Drain queued states. Only one worker per checkout may run at a time."""
    repo = Path(repo).resolve()
    state = _state_dir(repo)
    state.mkdir(parents=True, exist_ok=True)
    worker_lock = (state / "worker.lock").open("a+", encoding="utf-8")
    acquired = False
    try:
        try:
            _acquire_lock(worker_lock, nonblocking=True)
        except BlockingIOError:
            return 0
        acquired = True
        while True:
            if debounce:
                time.sleep(debounce)
            payload = _claim(repo)
            if payload is None:
                return 0
            bunker = Path(str(payload.get("bunker") or ""))
            if not bunker.is_dir() and (repo / layout.LOCATOR_FILE).exists():
                try:
                    from . import project

                    locator = project.load_locator(repo) or {}
                    bunker = project.materialize_ticket(
                        repo,
                        str(locator.get("project") or ""),
                        bunker.name,
                    )
                    payload["bunker"] = str(bunker)
                except Exception as exc:
                    result = {
                        "status": "fail",
                        "requested": was_user_requested(payload),
                        "branch": payload.get("branch"),
                        "head": payload.get("head"),
                        "events": payload.get("events") or [],
                        "detail": f"could not load the ticket for automatic refresh: {exc}",
                        "finished_at": _now(),
                    }
                    _write_json(state / "last.json", result)
                    if once:
                        return 1
                    continue
            ledger = Ledger(layout.paths(bunker)["ledger"])
            running = {
                "status": "running",
                "branch": payload.get("branch"),
                "head": payload.get("head"),
                "events": payload.get("events") or [],
                "started_at": _now(),
            }
            _write_json(state / RUNNING_FILE, running)
            ledger.event(
                "synthesis_started",
                branch=payload.get("branch"),
                head=payload.get("head"),
                events=payload.get("events") or [],
            )
            try:
                ok, detail = _run_pipeline(repo, payload)
            finally:
                (state / RUNNING_FILE).unlink(missing_ok=True)
            result = {
                "status": "pass" if ok else "fail",
                "requested": was_user_requested(payload),
                "branch": payload.get("branch"),
                "head": payload.get("head"),
                "events": payload.get("events") or [],
                "detail": detail,
                "finished_at": _now(),
            }
            ledger.event("synthesis_finished", **result)
            if (repo / layout.LOCATOR_FILE).exists():
                try:
                    from . import project

                    project.publish_data(repo, f"Synthesize {payload.get('branch')}")
                except Exception as exc:
                    result["publication_warning"] = str(exc)
            _write_json(state / "last.json", result)
            if once:
                return 0 if ok else 1
            if not (state / "queue.json").exists():
                return 0
    finally:
        worker_lock.close()
        # Close the narrow lost-wakeup window where an event lands after the
        # final empty claim but before this worker releases its lock.
        if acquired and not once and (_state_dir(repo) / "queue.json").exists():
            _spawn_worker(repo)


def read_state(repo: Path) -> dict[str, Any]:
    state = _state_dir(Path(repo).resolve())
    value: dict[str, Any] = {}
    last = state / "last.json"
    queue = state / "queue.json"
    running = state / RUNNING_FILE
    changes = state / CHANGE_FILE
    if last.exists():
        try:
            loaded = json.loads(last.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                value["last"] = loaded
        except (OSError, ValueError):
            pass
    if queue.exists():
        try:
            loaded = json.loads(queue.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                value["queued"] = loaded
        except (OSError, ValueError):
            pass
    if running.exists():
        try:
            loaded = json.loads(running.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                value["running"] = loaded
        except (OSError, ValueError):
            pass
    if changes.exists():
        try:
            loaded = json.loads(changes.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                value["changes"] = loaded
        except (OSError, ValueError):
            pass
    log = log_path(repo)
    if log.exists():
        value["log"] = str(log)
    return value
