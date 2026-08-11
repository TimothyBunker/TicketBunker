"""Execution ledger (DESIGN.md §3.8).

Append-only JSONL. Locally it lives at <bunker>/ledger.jsonl; the same entries
are what gets mirrored to the backend API. Diagnosis is reading the ledger:
stages appear as start/end brackets, commands as entries between them, so a
failed or skipped stage is visible as an unclosed or absent bracket.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

from .contracts import StageContract


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def digest(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, entry: dict) -> None:
        entry = {"ts": _now(), **entry}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def stage_start(self, contract: StageContract) -> None:
        self._write({"type": "stage_start", "stage": contract.name, "contract": contract.as_dict()})

    def stage_end(self, stage: str, status: str, detail: str | None = None) -> None:
        entry = {"type": "stage_end", "stage": stage, "status": status}
        if detail:
            entry["detail"] = detail
        self._write(entry)

    def command(
        self,
        stage: str,
        argv: list[str],
        cwd: str,
        initiator: str,  # "auto" (harness/pipeline) or "agent"
        exit_status: int,
        stdout: bytes,
        duration_ms: int,
    ) -> None:
        self._write(
            {
                "type": "command",
                "stage": stage,
                "argv": argv,
                "cwd": cwd,
                "initiator": initiator,
                "exit_status": exit_status,
                "stdout_digest": digest(stdout),
                "stdout_bytes": len(stdout),
                "duration_ms": duration_ms,
            }
        )

    def event(self, kind: str, **fields) -> None:
        self._write({"type": kind, **fields})

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
