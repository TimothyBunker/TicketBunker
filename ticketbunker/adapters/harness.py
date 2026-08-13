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

MERGE_CONTRACT = StageContract(
    name="merge_session",
    purpose="Reconcile divergent report components through a promoted agent session.",
    inputs="tb.md with the merge preamble, the merge form embedding every component, the source repo, a harness.",
    process="Invoke the harness headless; the agent verifies candidate disagreements against "
    "the code, edits the embedded components between their markers, and fills the "
    "Reconciliation area.",
    desired_outcome="A reconciled merge form on disk; every component republishes through the "
    "lint and anchor gates atomically — never a silent auto-resolution.",
)

SURVEY_CONTRACT = StageContract(
    name="survey_session",
    purpose="Run the root surveyor over the project to maintain tbroot.md.",
    inputs="tb.md with the surveyor preamble, a working copy of tbroot.md, the source repo, a harness.",
    process="Invoke the harness headless; the agent peruses the project and updates the working "
    "copy — real observations only, each anchored, gaps left as gaps.",
    desired_outcome="An updated working copy on disk, ready for the typed tbroot intake; "
    "installation happens outside the session through the conjecture and anchor lint gate.",
)

SURVEY_PROMPT = """\
You are the TicketBunker root surveyor for this project.
Your context (the surveyor rules) is in your system prompt.

Your working copy of tbroot.md is at: {form}
The project repo is at: {repo}

Follow the surveyor workflow from your context: peruse the project, update
the working copy in place — real observations only, each anchored to a
checkable path, file, or command; no conjecture; gaps stay gaps — and save
the file in place. Do not install it into the project; installation
happens outside your session. When the working copy is current, you are
done.
"""

MERGE_PROMPT = """\
You are the TicketBunker merge agent for ticket {ticket_id}.
Your context (rules, ontology, ticket, observations, and the merge session
preamble) is in your system prompt.

Your merge form is at: {form}
The source repo is at: {repo}

Follow the merge workflow from your context: verify each candidate
disagreement against the code, edit the embedded components in place
between their markers, fill the Reconciliation agent area, and save the
file in place. Do not publish; republication happens outside your session.
When the form is reconciled, you are done.
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
    form: Path | None = None,
    prompt: str | None = None,
    contract: StageContract = RUN_CONTRACT,
) -> tuple[str, int]:
    """Run an agent session as a ledgered stage. Returns (output, exit).

    Defaults run the ticket session; a promoted session (merge) passes its
    own form, prompt, and stage contract — same harness path, same ledger
    discipline, different job."""
    p = layout.paths(bunker)
    ticket_id = bunker.name.removesuffix("_bunker")
    form = form or layout.session_form(bunker, branch)
    prompt = prompt or PROMPT.format(ticket_id=ticket_id, branch=branch, form=form, repo=repo)

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
        return proc.run(argv, cwd=Path(repo), ledger=ledger, stage=contract.name, env=env)

    return run_stage(contract, ledger, _run)
