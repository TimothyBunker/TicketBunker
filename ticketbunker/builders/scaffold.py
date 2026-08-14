"""Report form scaffolding (DESIGN.md §3.9).

A report component is a form, not a blank page: the pipeline pre-fills the
[observation] lines from ticket config and bootstrap output, and the agent
fills in the agent-specific areas — [real] finds, ranked [speculation], the
Why synthesis. The form's structure is ours; the observations are the
pipeline's; only the fill is the agent's.
"""

from __future__ import annotations

import re

MAX_PREFILL_FILES = 15

AGENT_AREA = "<!-- agent: {instruction} -->"


def build(ticket: dict, observations: dict, branch: str) -> str:
    mh = observations.get("merge_history", {}).get(branch, {})
    au = observations.get("authorship", {}).get(branch, {})
    df = observations.get("diff", {}).get(branch, {})
    ws = observations.get("work_separation", {}).get(branch, {})

    lines: list[str] = []

    # The brief is agent-authored interpretation over the evidence below. It
    # never replaces or mutates an observation: the complete pre-filled trail
    # remains in Who/What/Where/Why/State and the brief keeps the same trust
    # labels and anchor requirements as every other claim.
    lines += ["## Reader brief", ""]
    lines += ["### Who", ""]
    lines.append(AGENT_AREA.format(
        instruction="reader brief Who: one or two concise [real]/[speculation] claims naming the people "
        "most important to this ticket and why, highest apparent importance first; use ticket/authorship "
        "evidence, do not merely list everyone"
    ))
    lines += ["", "### What", ""]
    lines.append(AGENT_AREA.format(
        instruction="reader brief What: one or two concise [real]/[speculation] claims explaining the most "
        "important work or result in plain language, highest apparent importance first, grounded in the "
        "diff and observations"
    ))
    lines += ["", "### Where", ""]
    lines.append(AGENT_AREA.format(
        instruction="reader brief Where: one short [real]/[speculation] claim naming up to three paths worth "
        "checking first, in importance order, and why; [real] paths need anchors, inferred recommendations need a grounding rank"
    ))
    lines += ["", "### Why", ""]
    lines.append(AGENT_AREA.format(
        instruction="reader brief Why: one or two concise [real]/[speculation] claims explaining the ticket's "
        "purpose and your best-supported interpretation, highest apparent importance first, grounded in "
        "primary ticket and code evidence"
    ))
    lines.append("")

    lines += ["## Who", ""]
    if ticket.get("assignee"):
        lines.append(f"- [observation] Assignee: {ticket['assignee']} (ticket config).")
    if ticket.get("reporter"):
        lines.append(f"- [observation] Reporter: {ticket['reporter']} (ticket config).")
    for poi in ticket.get("people_of_interest") or []:
        lines.append(f"- [observation] Person of interest: {poi} (ticket config).")
    for author, v in (au.get("authors") or {}).items():
        lines.append(
            f"- [observation] {author}: {v['commits']} work commit(s), {v['first']} → {v['last']} (authorship)."
        )
    lines.append(AGENT_AREA.format(instruction="add [real]/[speculation] people context only if the code points at someone not listed"))
    lines.append("")

    lines += ["## What", ""]
    files = df.get("files") or []
    work_commits = ws.get("work_commits") or []
    lines.append(
        f"- [observation] {df.get('file_count', 0)} file(s) touched in {len(work_commits)} work commit(s); "
        f"{ws.get('merge_commits', 0)} merge commit(s) separated out (diff, work_separation)."
    )
    for f in files[:MAX_PREFILL_FILES]:
        lines.append(f"- [observation] {f['kind']} {f['path']} (+{f['added']}/-{f['deleted']}) (diff).")
    if len(files) > MAX_PREFILL_FILES:
        lines.append(f"- [observation] ...and {len(files) - MAX_PREFILL_FILES} more file(s) — see observations/diff.json.")
    if df.get("patch"):
        lines.append(f"- [observation] Full patch text: {df['patch']}"
                     + (" (truncated)" if df.get("patch_truncated") else "") + " (diff).")
    lines.append(AGENT_AREA.format(instruction="characterize the actual code changes from the patch — what the code does, not just which files moved; [speculation] with grounding"))
    lines.append("")

    lines += ["## Where", ""]
    for f in files[:MAX_PREFILL_FILES]:
        lines.append(f"- [observation] Edit already happened: {f['path']} (diff).")
    if not files:
        lines.append("- [observation] No edits on this branch yet relative to the merge-base (diff).")
    lines.append(AGENT_AREA.format(instruction="[real] located ticket-related information: symbols/paths from the ticket found in the baseline, with anchors"))
    lines.append(AGENT_AREA.format(instruction="[speculation] where changes may be needed to solve the ticket, ranked grounded/adjacent/ungrounded; if nothing concrete, say so and name the tag-up person"))
    lines.append("")

    lines += ["## Why", ""]
    if ticket.get("description"):
        lines.append(f"- [observation] Ticket description: {ticket['description']} (ticket).")
    if ticket.get("title"):
        lines.append(f"- [observation] Ticket title: {ticket['title']} (ticket).")
    lines.append(AGENT_AREA.format(instruction="primary sources first; your [speculation] synthesis at the bottom, composed from [real] findings and tagged"))
    lines.append("")

    lines += ["## State", ""]
    lines.append(
        f"- [observation] Branch {branch}: {mh.get('ahead_of_merge_base', '?')} commit(s) of work, "
        f"{mh.get('behind_baseline', '?')} behind baseline, merged back: {mh.get('merged_back', '?')} (merge_history)."
    )
    lines.append(f"- [observation] Last sync with baseline: {mh.get('last_sync_date', '?')} (merge_history).")
    if au.get("recency_of_development"):
        lines.append(f"- [observation] Recency of development: {au['recency_of_development']} (authorship).")
    lines.append(AGENT_AREA.format(instruction="[speculation] apparent progress toward the ticket goal, with grounding"))
    lines.append("")

    return "\n".join(lines)


# --- merge form (DESIGN.md §3.13) ------------------------------------------
# The merge session's form embeds every component verbatim between markers;
# the agent edits the bodies in place and fills the Reconciliation area. The
# markers let the runner split the reconciled bodies back out per branch.

MERGE_COMPONENT_BEGIN = "<!-- tb:component branch={branch} -->"
MERGE_COMPONENT_END = "<!-- tb:end-component -->"
_COMPONENT_RE = re.compile(
    r"<!-- tb:component branch=(?P<branch>\S+) -->\n(?P<body>.*?)<!-- tb:end-component -->",
    re.DOTALL,
)


def build_merge(ticket: dict, components: list[tuple[str, str]], overlap_paths: list[str]) -> str:
    lines: list[str] = [
        f"<!-- tb:merge-form ticket={ticket.get('id', '?')} -->",
        "",
        "## Reconciliation",
        "",
        f"- [observation] Components under reconciliation: {', '.join(br for br, _ in components)} (reports/branches/).",
    ]
    for path in overlap_paths:
        lines.append(f"- [observation] More than one component anchors claims to {path} (anchor overlap).")
    if not overlap_paths:
        lines.append("- [observation] No anchor-path overlap between components — disagreement, if any, is not mechanically indicated (anchor overlap).")
    lines.append(AGENT_AREA.format(
        instruction="each semantic disagreement about the same code: what the components claim, "
        "what the code shows, how you resolved it — [real] with anchors for what you verified, "
        "[speculation] ranked otherwise; if the components do not disagree, say so"
    ))
    lines.append("")
    for branch, text in components:
        lines.append(MERGE_COMPONENT_BEGIN.format(branch=branch))
        lines.append(text.rstrip())
        lines.append(MERGE_COMPONENT_END)
        lines.append("")
    return "\n".join(lines)


def split_merge(text: str) -> dict[str, str]:
    """branch -> reconciled component body, from a filled merge form."""
    return {
        m.group("branch"): m.group("body").strip("\n") + "\n"
        for m in _COMPONENT_RE.finditer(text)
    }


def merge_head(text: str) -> str:
    """The form text before the first embedded component — where the
    Reconciliation section lives."""
    idx = text.find("<!-- tb:component ")
    return text[:idx] if idx >= 0 else text
