"""Ledgered git execution.

Every git command in the system runs through `git()`, which logs to the ledger
as a side effect of execution (§3.8): an unlogged command is impossible by
construction. The agent never calls the logging itself.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

from ..ledger import Ledger


_LOCKS_GUARD = threading.Lock()
_REPOSITORY_LOCKS: dict[str, threading.RLock] = {}


@contextmanager
def repository_lock(repo: Path) -> Iterator[None]:
    """Keep one process from interleaving Git transactions in a checkout."""
    key = os.path.normcase(str(Path(repo).resolve()))
    with _LOCKS_GUARD:
        lock = _REPOSITORY_LOCKS.setdefault(key, threading.RLock())
    with lock:
        yield


class GitError(RuntimeError):
    def __init__(self, argv: list[str], exit_status: int, stderr: str):
        super().__init__(f"git command failed ({exit_status}): {' '.join(argv)}\n{stderr.strip()}")
        self.argv = argv
        self.exit_status = exit_status
        self.stderr = stderr


def git(
    repo: Path,
    args: list[str],
    ledger: Ledger,
    stage: str,
    initiator: str = "auto",
    check: bool = True,
) -> str:
    out, status = git_status(repo, args, ledger, stage, initiator)
    if check and status != 0:
        raise GitError(["git", "-C", str(repo), *args], status, out)
    return out


def git_status(
    repo: Path,
    args: list[str],
    ledger: Ledger,
    stage: str,
    initiator: str = "auto",
    *,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> tuple[str, int]:
    """Like git(), but never raises; returns (stdout, exit_status).

    For commands whose answer *is* the exit status (e.g. merge-base
    --is-ancestor).
    """
    with repository_lock(repo):
        argv = ["git", "-C", str(repo), *args]
        start = time.monotonic()
        process_env = None
        if env is not None:
            process_env = {**os.environ, **env}
        try:
            proc = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=process_env,
                timeout=timeout,
            )
            output = proc.stdout
            status = proc.returncode
        except subprocess.TimeoutExpired as exc:
            prior = exc.stdout if isinstance(exc.stdout, bytes) else str(exc.stdout or "").encode()
            output = prior + f"\nGit command timed out after {timeout:g} seconds".encode()
            status = 124
        duration_ms = int((time.monotonic() - start) * 1000)
        ledger.command(
            stage=stage,
            argv=argv,
            cwd=str(repo),
            initiator=initiator,
            exit_status=status,
            stdout=output,
            duration_ms=duration_ms,
        )
        return output.decode(errors="replace"), status
