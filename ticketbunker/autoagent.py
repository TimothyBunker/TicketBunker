"""Automatic agent refreshes driven by Git lifecycle events.

Git hooks must stay fast and must never make a commit, checkout, merge, or
rebase depend on an agent provider.  A hook therefore records the newest
ticket-branch state below ``.git`` and starts a detached worker.  The worker
coalesces bursts of events, refreshes deterministic observations first, then
runs the ordinary gated agent pipeline.  No alternate publish path exists.
"""

from __future__ import annotations

import datetime
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import agent_runtime, config, layout
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


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


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


def _launcher() -> list[str]:
    # Pin the hook to the installed/frozen command so GUI Git does not depend
    # on shell activation or a source checkout.
    return agent_runtime.cli_argv()


def _hook_body(event: str, original: Path) -> str:
    # Hooks are shared by linked worktrees; resolve the active one at runtime.
    shell_values = [value.replace("\\", "/") if os.name == "nt" else value for value in _launcher()]
    launch = " ".join(shlex.quote(value) for value in shell_values)
    original_call = ""
    if original.exists():
        original_value = str(original).replace("\\", "/") if os.name == "nt" else str(original)
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
    """Install all automatic-agent hooks while preserving foreign hooks."""
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
        return None
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
    return assigned[0] if len(assigned) == 1 else None


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


def _spawn_worker(repo: Path) -> None:
    state = _state_dir(repo)
    state.mkdir(parents=True, exist_ok=True)
    log = (state / "agent.log").open("ab")
    env = dict(os.environ)
    # A PyInstaller one-file parent removes its extraction directory on exit.
    # The detached worker outlives that parent, so it must unpack independently.
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    package_parent = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (package_parent, env.get("PYTHONPATH", "")) if value
    )
    try:
        subprocess.Popen(
            [*_launcher(), "_agent-worker", "--repo", str(repo)],
            cwd=str(repo),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log.close()


def enqueue(repo: Path, event: str, *, spawn: bool = True) -> dict[str, Any]:
    """Coalesce one Git event and ensure a background worker exists."""
    repo = Path(repo).resolve()
    selected = config.load().get("agent") or {}
    if isinstance(selected, dict) and selected.get("enabled") is False:
        return {"queued": False, "reason": "agent_disabled"}
    branch, status = _git(repo, "branch", "--show-current")
    if status != 0 or not branch:
        return {"queued": False, "reason": "detached_or_missing_branch"}
    bunker = resolve_bunker(repo, branch)
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
        return False, f"branch moved to {current or 'detached HEAD'} before refresh; the next checkout will retry"
    head, _ = _git(repo, "rev-parse", "HEAD")
    if head != payload.get("head"):
        enqueue(repo, "head-advanced", spawn=False)
        return False, "HEAD advanced while the event was queued; coalesced the newer state"
    profile = _profile(repo, bunker, branch)
    base = _launcher()
    refresh = [*base, "refresh", "--repo", str(repo), "--quiet", "--strict"]
    env = _cli_env()
    refreshed = subprocess.run(refresh, cwd=str(repo), env=env)
    if refreshed.returncode != 0:
        return False, "deterministic refresh failed; no agent session was published"
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
    completed = subprocess.run(command, cwd=str(repo), env=env)
    if completed.returncode != 0:
        latest, _ = _git(repo, "rev-parse", "HEAD")
        if latest and latest != payload.get("head"):
            enqueue(repo, "head-advanced", spawn=False)
        return False, f"automatic {profile['harness']} agent session failed; inspect the local agent log"
    return True, f"published {branch} through {profile['harness']}"


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
            ledger.event(
                "automatic_agent_started",
                branch=payload.get("branch"),
                head=payload.get("head"),
                events=payload.get("events") or [],
            )
            ok, detail = _run_pipeline(repo, payload)
            result = {
                "status": "pass" if ok else "fail",
                "branch": payload.get("branch"),
                "head": payload.get("head"),
                "events": payload.get("events") or [],
                "detail": detail,
                "finished_at": _now(),
            }
            ledger.event("automatic_agent_finished", **result)
            if (repo / layout.LOCATOR_FILE).exists():
                try:
                    from . import project

                    project.publish_data(repo, f"Automatic agent: {payload.get('branch')}")
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
    log = state / "agent.log"
    if log.exists():
        value["log"] = str(log)
    return value
