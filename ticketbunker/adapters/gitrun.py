"""Ledgered git execution.

Every git command in the system runs through `git()`, which logs to the ledger
as a side effect of execution (§3.8): an unlogged command is impossible by
construction. The agent never calls the logging itself.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from ..ledger import Ledger


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
) -> tuple[str, int]:
    """Like git(), but never raises; returns (stdout, exit_status).

    For commands whose answer *is* the exit status (e.g. merge-base
    --is-ancestor).
    """
    argv = ["git", "-C", str(repo), *args]
    start = time.monotonic()
    proc = subprocess.run(argv, capture_output=True)
    duration_ms = int((time.monotonic() - start) * 1000)
    ledger.command(
        stage=stage,
        argv=argv,
        cwd=str(repo),
        initiator=initiator,
        exit_status=proc.returncode,
        stdout=proc.stdout,
        duration_ms=duration_ms,
    )
    return proc.stdout.decode(errors="replace"), proc.returncode
