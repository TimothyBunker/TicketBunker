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


import subprocess as _subprocess

# Indirection so tests can stand in for the agent without touching every
# other subprocess in the process.
_run_process = _subprocess.run


class DiscoveryError(RuntimeError):
    """The agent could not propose commands; the message says what to do."""


DISCOVERY_TIMEOUT_SECONDS = 180
DISCOVERY_PROMPT = """\
You are TicketBunker's build discovery helper. You may read, search, and list
files in the repository at {root}. You must not run anything or change any file.

Find how this repository is built and tested from the command line, from its
own files (manifests, CI workflows, scripts, READMEs). {focus}

Reply with exactly one JSON object and nothing else:
{{"build": "<one shell command line, or empty>", "test": "<one shell command line, or empty>", "reason": "<one sentence naming the files you relied on>"}}

Rules: each command is one line that runs from the repository root in a fresh
checkout; prefer the repository's own scripts over guesses; leave a command
empty rather than invent one; never include placeholders.
"""


def discover_with_agent(root: Path, *, ticket: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the pinned Pi runtime, with read-only tools, for build/test commands.

    For a ticket, the prompt names its title and description so the agent
    can narrow the build to the part of the repository the ticket touches;
    otherwise it asks for the whole project's commands. Nothing is written or
    executed by the agent, and the caller decides what to keep.
    """
    import json
    import os
    import subprocess

    from . import agent_runtime, platform_services

    readiness = agent_runtime.state(root)
    if not readiness.get("ready") or readiness.get("enabled") is False:
        raise DiscoveryError(
            f"{readiness.get('summary') or 'The local agent is not ready.'} Next: {readiness.get('action') or 'Open Settings, Agent.'}"
        )
    runtime = readiness.get("runtime") or {}
    ticket = ticket or {}
    if ticket:
        focus = (
            f"Narrow the commands to what ticket {ticket.get('id') or ''} needs: "
            f"title {json.dumps(str(ticket.get('title') or ''))}, description "
            f"{json.dumps(str(ticket.get('description') or '')[:1500])}. If the build has targets, "
            "name only the targets and tests that cover the ticket's area; if it does not, give the project's commands."
        )
    else:
        focus = "Give the commands for the whole project."
    manifests = detect(root)
    hint = ("Manifests found at the root: " + "; ".join(f"{m['tool']} ({m['reason']})" for m in manifests)) if manifests else "No manifest was recognized at the root; look deeper."
    prompt = DISCOVERY_PROMPT.format(root=root, focus=focus) + "\n" + hint
    argv = [
        str(runtime.get("path") or ""),
        "--print",
        "--provider",
        str(readiness.get("provider") or ""),
        "--model",
        str(readiness.get("model") or ""),
        "--tools",
        "read,grep,find,ls",
        "--no-context-files",
        "--no-skills",
        "--no-prompt-templates",
        "--no-extensions",
        "--no-approve",
        prompt,
    ]
    try:
        completed = _run_process(
            argv,
            cwd=str(root),
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ),
            timeout=DISCOVERY_TIMEOUT_SECONDS,
            **platform_services.current().headless_process_options(),
        )
    except subprocess.TimeoutExpired as exc:
        raise DiscoveryError("The agent did not answer within three minutes. Next: enter the commands by hand, or retry.") from exc
    output = (completed.stdout or b"").decode("utf-8", errors="replace") + (completed.stderr or b"").decode("utf-8", errors="replace")
    if completed.returncode != 0:
        raise DiscoveryError("The agent did not finish. Next: run 'tb agent status', then retry.")
    value = _parse_discovery(output)
    if value is None:
        raise DiscoveryError("The agent's answer was not a build and test command. Next: enter the commands by hand, or retry.")
    return {**value, "manifests": manifests, "scope": "ticket" if ticket else "project"}


def _parse_discovery(output: str) -> dict[str, str] | None:
    import json
    import re

    candidates = [output.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", output, re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1))
    brace = re.search(r"\{[^{}]*\}", output, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        build = str(value.get("build") or "").strip()
        test = str(value.get("test") or "").strip()
        if "\n" in build or "\n" in test or not (build or test):
            continue
        return {"build": build[:500], "test": test[:500], "reason": str(value.get("reason") or "").strip()[:300]}
    return None


__all__ = ["DETECTORS", "DiscoveryError", "detect", "discover_with_agent"]
