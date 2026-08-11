#!/usr/bin/env python3
"""Build dist/tb — a single-file executable of the TicketBunker CLI.

Uses stdlib zipapp only: the output is one file with a python3 shebang that
runs anywhere Python 3.10+ exists, with no install step. Drop it on PATH:

    python3 tools/build_tb.py
    cp dist/tb ~/bin/tb
    tb status TB-1_bunker
"""

from __future__ import annotations

import py_compile
import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    pkg = ROOT / "ticketbunker"
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / "tb"

    with tempfile.TemporaryDirectory() as td:
        staging = Path(td)
        dest = staging / "ticketbunker"
        shutil.copytree(pkg, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for src in dest.rglob("*.py"):
            py_compile.compile(str(src), doraise=True)  # fail the build on syntax errors
        (staging / "__main__.py").write_text(
            "from ticketbunker.cli import main\nraise SystemExit(main())\n", encoding="utf-8"
        )
        zipapp.create_archive(staging, out, interpreter="/usr/bin/env python3")

    out.chmod(0o755)
    print(f"built {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
