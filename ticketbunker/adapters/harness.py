"""Harness adapter (DESIGN.md §3.1): run an agent session over a bunker.

The pipeline stays harness-neutral: compose tb.md, scaffold the form, hand
both to *some* harness, then publish what came back through typed intake.
The pi profile is the reference harness; the generic command profile runs
any CLI (a stub for testing, a different agent runner, etc.).
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from .. import layout
from ..contracts import StageContract, run_stage
from ..ledger import Ledger
from . import proc

RUN_CONTRACT = StageContract(
    name="agent_session",
    purpose="Run the ticket agent over the composed context and pre-filled form.",
    inputs="tb.md (session-scoped), the scaffolded form, the source repo, a harness.",
    process="Invoke the harness headless with tb.md appended to its system prompt and "
    "a prompt pointing at the form; the agent fills the agent areas and saves.",
    desired_outcome="A filled form on disk, ready for typed intake; every agent tool "
    "call mirrored to the ledger by the harness extension.",
)

PROMPT = """\
You are the TicketBunker agent for ticket {ticket_id}, branch {branch}.
Your context (rules, ontology, ticket, observations) is in your system prompt.

Your pre-filled report form is at: {form}
The source repo is at: {repo}

Follow the tool order from your context. Fill in every `<!-- agent: ... -->`
area of the form with tagged claims, leaving the [observation] lines exactly
as they are, and save the file in place. Do not publish; publishing happens
outside your session. When the form is complete, you are done.
"""

# The extension that mirrors agent tool calls into the bunker ledger.
PI_EXTENSION = Path(__file__).resolve().parent.parent.parent / "harness" / "pi" / "tb.ts"


def build_pi_argv(
    tb_md: Path,
    sessions_dir: Path,
    session_name: str,
    prompt: str,
    pi_bin: str = "pi",
    provider: str | None = None,
    model: str | None = None,
) -> list[str]:
    argv = [
        pi_bin,
        "--print",
        "--append-system-prompt", str(tb_md),
        "--extension", str(PI_EXTENSION),
        "--session-dir", str(sessions_dir),
        "--name", session_name,
    ]
    if provider:
        argv += ["--provider", provider]
    if model:
        argv += ["--model", model]
    argv.append(prompt)
    return argv


def run_session(
    bunker: Path,
    branch: str,
    repo: Path,
    ledger: Ledger,
    harness: str = "pi",
    command_template: str | None = None,
    pi_bin: str = "pi",
    codex_bin: str = "codex",
    provider: str | None = None,
    model: str | None = None,
) -> tuple[str, int]:
    """Run the agent session as a ledgered stage. Returns (output, exit)."""
    p = layout.paths(bunker)
    ticket_id = bunker.name.removesuffix("_bunker")
    form = layout.session_form(bunker, branch)
    prompt = PROMPT.format(ticket_id=ticket_id, branch=branch, form=form, repo=repo)

    if harness == "pi":
        argv = build_pi_argv(
            p["tb_md"], p["sessions"], f"{ticket_id}:{layout.branch_slug(branch)}",
            prompt, pi_bin=pi_bin, provider=provider, model=model,
        )
    elif harness == "codex":
        # Codex CLI has no system-prompt-file injection, so tb.md rides at
        # the top of the prompt itself — same content, same audit trail
        # (the full prompt is in the ledgered argv).
        full_prompt = (
            "Your context document follows; it is authoritative.\n\n"
            + p["tb_md"].read_text(encoding="utf-8")
            + "\n\n---\n\n"
            + prompt
        )
        # exec defaults to a read-only sandbox; the agent must be able to
        # write the form (which lives in .tb/ inside the workspace).
        argv = [codex_bin, "exec", "--cd", str(repo), "--sandbox", "workspace-write"]
        if model:
            argv += ["--model", model]
        argv.append(full_prompt)
    elif harness == "command":
        if not command_template:
            raise ValueError("harness 'command' requires a command template")
        argv = [
            part.format(form=str(form), tb_md=str(p["tb_md"]), repo=str(repo), prompt=prompt)
            for part in shlex.split(command_template)
        ]
    else:
        raise ValueError(f"unknown harness {harness!r} (known: pi, codex, command)")

    env = dict(os.environ)
    env["TB_BUNKER"] = str(Path(bunker).resolve())
    env["TB_BRANCH"] = branch

    def _run() -> tuple[str, int]:
        return proc.run(argv, cwd=Path(repo), ledger=ledger, stage="agent_session", env=env)

    return run_stage(RUN_CONTRACT, ledger, _run)
