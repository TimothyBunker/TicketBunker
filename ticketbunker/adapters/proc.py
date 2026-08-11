"""Ledgered process execution for non-git commands (harness invocations).

Same rule as gitrun: running a command through this wrapper logs it as a
side effect of execution. The harness invocation itself is part of the
auditable record.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from ..ledger import Ledger


def run(
    argv: list[str],
    cwd: Path,
    ledger: Ledger,
    stage: str,
    env: dict | None = None,
    initiator: str = "auto",
) -> tuple[str, int]:
    start = time.monotonic()
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, env=env)
    duration_ms = int((time.monotonic() - start) * 1000)
    ledger.command(
        stage=stage,
        argv=argv,
        cwd=str(cwd),
        initiator=initiator,
        exit_status=proc.returncode,
        stdout=proc.stdout,
        duration_ms=duration_ms,
    )
    return proc.stdout.decode(errors="replace") + proc.stderr.decode(errors="replace"), proc.returncode
