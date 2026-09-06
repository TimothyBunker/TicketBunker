"""Suggest build and test commands from what a repository root contains.

A suggestion is only a starting point the owner confirms in the Project's
configuration; nothing runs until it is published as the ``checks``
document. Detection is deliberately shallow: the root's manifest files,
nothing recursive, no execution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# (marker files, tool, build command, test command)
DETECTORS: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (("CMakeLists.txt",), "CMake", "cmake -S . -B .tb-check-build && cmake --build .tb-check-build", "ctest --test-dir .tb-check-build --output-on-failure"),
    (("Cargo.toml",), "Cargo", "cargo build", "cargo test"),
    (("go.mod",), "Go", "go build ./...", "go test ./..."),
    (("package.json",), "npm", "npm run build --if-present", "npm test"),
    (("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"), "pytest", "", "python3 -m pytest -x -q"),
    (("Makefile", "makefile", "GNUmakefile"), "make", "make", "make test"),
)


def detect(root: Path | str) -> list[dict[str, Any]]:
    """Suggestions in preference order for the manifests found at ``root``."""
    base = Path(root)
    suggestions: list[dict[str, Any]] = []
    for markers, tool, build, test in DETECTORS:
        found = [marker for marker in markers if (base / marker).is_file()]
        if not found:
            continue
        suggestions.append({"tool": tool, "reason": f"{found[0]} at the repository root", "build": build, "test": test})
    return suggestions


__all__ = ["DETECTORS", "detect"]
