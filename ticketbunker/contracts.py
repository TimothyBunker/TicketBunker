"""Stage contracts (DESIGN.md §3.7).

Every stage declares purpose, input, process, and desired outcome — explicitly
and modularly. A stage's execution is bracketed by ledger markers emitted by
the runner, never by the agent (§3.8).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable


@dataclasses.dataclass(frozen=True)
class StageContract:
    name: str
    purpose: str
    inputs: str
    process: str
    desired_outcome: str

    def as_dict(self) -> dict[str, str]:
        return dataclasses.asdict(self)


def run_stage(
    contract: StageContract,
    ledger: "Ledger",  # noqa: F821 - imported lazily to avoid a cycle
    fn: Callable[[], Any],
) -> Any:
    """Execute a stage function with structural start/end markers.

    The markers are a side effect of execution itself: there is no way to run
    a stage through this runner without them appearing in the ledger.
    """
    ledger.stage_start(contract)
    try:
        result = fn()
    except Exception as exc:
        ledger.stage_end(contract.name, status="error", detail=repr(exc))
        raise
    ledger.stage_end(contract.name, status="ok")
    return result
