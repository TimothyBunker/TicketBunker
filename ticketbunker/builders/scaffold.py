"""Report form scaffolding (DESIGN.md §3.9).

A report component is a form, not a blank page: the pipeline pre-fills the
[observation] lines from ticket config and bootstrap output, and the agent
fills in the agent-specific areas — [real] finds, ranked [speculation], the
Why synthesis. The form's structure is ours; the observations are the
pipeline's; only the fill is the agent's.
"""

from __future__ import annotations

import re
from collections import Counter

MAX_PREFILL_FILES = 15
MAX_PREFILL_PEOPLE = 6

AGENT_AREA = "<!-- agent: {instruction} -->"


def description_items(text: str) -> list[str]:
    """Flatten a ticket description into one line per item.

    Tracker descriptions arrive as nested bullet lists. A form claim is one
    line, so each bullet becomes its own item with its parents joined by
    " › ", and plain lines stay as they are. The raw text remains in
    ticket.json for the agent to cite.
    """
    items: list[str] = []
    stack: list[tuple[int, str]] = []
    for raw in str(text or "").splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        body = raw.strip()
        bullet = re.match(r"^(?:[-*+•]|\d+[.)])\s+(.*)$", body)
        if bullet:
            label = bullet.group(1).strip().rstrip(":").strip()
            if not label:
                continue
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parents = [name for _, name in stack]
            items.append(" › ".join([*parents, label]) if parents else label)
            stack.append((indent, label))
        else:
            stack.clear()
            items.append(body)
    return items


MAX_CHECKLIST_ITEMS = 12
DATE_ONLY_RE = re.compile(r"\[?\d{1,2}/\d{1,2}/\d{2,4}\]?")


def _readable_items(items: list[str]) -> list[str]:
    """Drop what says nothing on its own: bare dates, and a parent heading
    that only introduces the items that follow it."""
    kept: list[str] = []
    for index, item in enumerate(items):
        if DATE_ONLY_RE.fullmatch(item.strip()):
            continue
        following = items[index + 1] if index + 1 < len(items) else ""
        if following.startswith(item + " › "):
            continue
        kept.append(item)
    return kept


def checklist_items(ticket: dict) -> list[str]:
    """The ticket's own list of things to be true: acceptance criteria first,
    then the description's items, deduplicated and bounded."""
    items: list[str] = []
    seen: set[str] = set()
    criteria = [value for value in (ticket.get("acceptance_criteria") or []) if isinstance(value, str) and value.strip()]
    for item in [*criteria, *_readable_items(description_items(str(ticket.get("description") or "")))]:
        text = item.strip()
        key = text.casefold()
        # One-word markers are not checkable.
        if not text or key in seen or len(text.split()) < 2:
            continue
        seen.add(key)
        items.append(text)
    return items[:MAX_CHECKLIST_ITEMS]


def _check_lines(outcomes: dict) -> list[str]:
    """One observation per configured check that ran on this branch."""
    lines: list[str] = []
    for name in ("build", "test"):
        outcome = outcomes.get(name)
        if not isinstance(outcome, dict):
            continue
        label = name.capitalize()
        command = outcome.get("command", "")
        seconds = outcome.get("seconds", "?")
        if outcome.get("passed"):
            lines.append(f"- [observation] {label} check passed in {seconds}s: `{command}` (check).")
        elif outcome.get("timed_out"):
            lines.append(f"- [observation] {label} check timed out after {seconds}s: `{command}` (check, {outcome.get('log', '')}).")
        else:
            lines.append(
                f"- [observation] {label} check failed (exit {outcome.get('exit')}) in {seconds}s: `{command}` — "
                f"first error: {outcome.get('first_error') or 'no output'} (check, {outcome.get('log', '')})."
            )
    return lines


def build(ticket: dict, observations: dict, branch: str) -> str:
    mh = observations.get("merge_history", {}).get(branch, {})
    au = observations.get("authorship", {}).get(branch, {})
    df = observations.get("diff", {}).get(branch, {})
    ws = observations.get("work_separation", {}).get(branch, {})
    pe = observations.get("people", {}).get(branch, {})
    rk = observations.get("risk", {}).get(branch, {})
    lo = observations.get("locate", {}) or {}
    ck = observations.get("check", {}).get(branch, {}) or {}
    sc = observations.get("scan", {}).get(branch, {}) or {}
    syntax_errors = [item for item in (sc.get("syntax_errors") or []) if isinstance(item, dict)]
    check_lines = _check_lines(ck)
    check_failed = any(not outcome.get("passed") for outcome in ck.values() if isinstance(outcome, dict))
    no_branch_work = not (ws.get("work_commits") or []) and not (df.get("files") or [])
    baseline = str(observations.get("baseline") or "the baseline")

    lines: list[str] = []

    # The brief is agent-authored interpretation over the evidence below. It
    # never replaces or mutates an observation: the complete pre-filled trail
    # remains in Who/What/Where/Why/State and the brief keeps the same trust
    # labels and anchor requirements as every other claim.
    lines += ["## Reader brief", ""]
    # Standing answers the reader's first question before the four evidence
    # questions: where does this stand, what matters most, what next, whom to ask.
    lines += ["### Standing", ""]
    lines.append(AGENT_AREA.format(
        instruction="reader brief Standing: exactly one verdict claim first, written as `- [speculation] <rank> — Verdict: "
        "<not started | in progress | blocked | looks done> — <one sentence naming the single most important finding, "
        "with an anchor>` (use [real] with an anchor instead of [speculation] only when the verdict rests on a checked "
        "fact such as a failing build); then at most one `Next:` claim (the first concrete thing to do, with an anchor) "
        "and at most one `Ask:` claim (whom to talk to and why, citing (ticket) or (people)); nothing else in this part"
        + ("; a configured build or test check FAILED on this branch (see the check observations under What), so the "
           "verdict is blocked and its finding is that first error unless the code proves otherwise" if check_failed else "")
        + ("; the scan found a definite syntax error (see the scan observations under What), so the verdict is blocked "
           "and its finding is that error" if syntax_errors else "")
        + "; if you record any Defect: claim under What, the verdict is blocked and its finding is that defect"
        + ("; this branch has no work commits and no diff, so do not report the empty diff as the finding: judge the "
           "tree at the branch head against the ticket (the work may already live on the baseline) and say what state "
           "that code is in" if no_branch_work else "")
    ))
    lines += ["", "### Who", ""]
    lines.append(AGENT_AREA.format(
        instruction="reader brief Who: one or two concise [real]/[speculation] claims naming the people "
        "most important to this ticket and why, highest apparent importance first, drawn from ticket config, "
        "branch authorship, and the people observation (recent authors of the touched files, other branches of "
        "this ticket); cite (ticket), (authorship), or (people) rather than a source path; if the assignee and "
        "reporter are the only people known, say so in one plain sentence and cite (ticket); optionally one "
        "'Risk:' claim (an unclear or absent owner) and one 'Next:' claim (whom to involve)"
    ))
    lines += ["", "### What", ""]
    lines.append(AGENT_AREA.format(
        instruction="reader brief What: one or two concise [real]/[speculation] claims explaining the most "
        "important work or result in plain language, highest apparent importance first, grounded in the "
        "diff and observations; optionally one 'Defect:' claim ([real], anchored: an error you verified by reading, "
        "see the What area below), one 'Risk:' claim (a bug, regression, or untested change you suspect) and one "
        "'Next:' claim (one concrete thing to do about the work)"
    ))
    lines += ["", "### Where", ""]
    lines.append(AGENT_AREA.format(
        instruction="reader brief Where: one short [real]/[speculation] claim naming up to three paths worth "
        "checking first, in importance order, and why; write verified source locations as @[relative/path]:line "
        "so the viewer can open them; [real] paths need anchors, inferred recommendations need a grounding rank; "
        "optionally one 'Risk:' claim (where a change is likely to bite: a file also changed on the baseline, a "
        "caller left unchanged) and one 'Next:' claim (the first place to edit or check)"
    ))
    lines += ["", "### Why", ""]
    lines.append(AGENT_AREA.format(
        instruction="reader brief Why: one or two concise [real]/[speculation] claims explaining the ticket's "
        "purpose and your best-supported interpretation, highest apparent importance first, grounded in "
        "primary ticket and code evidence; optionally one 'Risk:' claim (where the change and the ticket's "
        "intent diverge or an acceptance criterion is unmet) and one 'Next:' claim (what would settle it)"
    ))
    checklist = checklist_items(ticket)
    if checklist:
        lines += ["", "### Checklist", ""]
        numbered = "; ".join(f"{index}) {item}" for index, item in enumerate(checklist, 1))
        lines.append(AGENT_AREA.format(
            instruction="reader brief Checklist: one claim per item below, in this order, each beginning `Done:`, "
            "`Partial:`, `Missing:`, or `Unclear:` followed by the item text and then the evidence — an anchor into "
            "the code for Done and Partial, an anchor or (ticket) for Missing and Unclear (a [speculation] item still "
            "carries its grounding rank before the mark: `- [speculation] grounded — Missing: ...`); judge each item against the "
            "whole tree at the branch head (what the code actually contains and whether it works), never against this "
            "branch's diff alone or the ticket's own wording; items: " + numbered
        ))
    lines += ["", "### Plan", ""]
    lines.append(AGENT_AREA.format(
        instruction="reader brief Plan: three to seven ordered `Step:` claims a person could follow to finish this ticket "
        "from where it stands, each one imperative sentence naming the place to act (`- [real] Step: <do this> "
        "(@[path]:line)`, or `[speculation] <rank> — Step:` when the place is inferred; a step without a source place "
        "may cite the evidence it rests on, such as (check) or (ticket)); order them so each unblocks the next: definite "
        "errors and anything that stops the code from building first, then the checklist's Missing and Partial items, "
        "then verification (the configured check, or the build and tests this project uses); if the ticket looks done, "
        "one step saying what to verify before closing it; never restate a Defect or checklist item without saying what to do"
    ))
    lines.append("")

    lines += ["## Who", ""]
    if ticket.get("assignee"):
        lines.append(f"- [observation] Assignee: {ticket['assignee']} (ticket config).")
    if ticket.get("reporter"):
        lines.append(f"- [observation] Reporter: {ticket['reporter']} (ticket config).")
    for poi in ticket.get("people_of_interest") or []:
        lines.append(f"- [observation] Person of interest: {poi} (ticket config).")
    for reviewer in ticket.get("reviewers") or []:
        if isinstance(reviewer, str) and reviewer:
            lines.append(f"- [observation] Reviewer: {reviewer} (ticket config).")
    commenters = Counter(
        str(c.get("author")) for c in (ticket.get("comments") or []) if isinstance(c, dict) and c.get("author")
    )
    for name, count in commenters.most_common():
        lines.append(f"- [observation] Commenter: {name} ({count} comment(s) on the ticket) (ticket).")
    for author, v in (au.get("authors") or {}).items():
        lines.append(
            f"- [observation] {author}: {v['commits']} work commit(s), {v['first']} → {v['last']} (authorship)."
        )
    file_authors = sorted(
        (pe.get("file_authors") or {}).items(), key=lambda item: (-item[1]["commits"], item[1]["last"]), reverse=False
    )
    for name, v in file_authors[:MAX_PREFILL_PEOPLE]:
        lines.append(
            f"- [observation] Recent author of the touched files on {baseline}: {name} — "
            f"{v['commits']} commit(s), last {str(v['last'])[:10]} (people)."
        )
    if len(file_authors) > MAX_PREFILL_PEOPLE:
        lines.append(f"- [observation] ...and {len(file_authors) - MAX_PREFILL_PEOPLE} more author(s) — see observations/people.json.")
    if pe.get("touched_paths") and not file_authors:
        lines.append(f"- [observation] The touched files have no earlier history on {baseline} (people).")
    for other in pe.get("other_branches") or []:
        if other.get("author"):
            lines.append(
                f"- [observation] Other branch for this ticket: {other['branch']} — last work commit by "
                f"{other['author']} on {str(other['date'])[:10]} (people)."
            )
        else:
            lines.append(f"- [observation] Other branch for this ticket: {other['branch']} — no work commits yet (people).")
    lines.append(AGENT_AREA.format(
        instruction="Who is about people: add a [real]/[speculation] claim only when it names a person and why they "
        "matter to this ticket (an owner or frequent author of a touched module, a reviewer or commenter named in "
        "the ticket, the author of a related change), citing (ticket), (authorship), or (people); a source path "
        "alone is never a Who claim, and 'no one else' is not a finding — the observations above already list "
        "everyone known, so if that is all, add nothing"
    ))
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
    lines.extend(check_lines)
    for item in syntax_errors:
        lines.append(f"- [observation] Syntax error in {item['path']}:{item['line']} — {item['message']} (scan).")
    for path, count in sorted((rk.get("added_markers") or {}).items()):
        lines.append(f"- [observation] Added {count} TODO/FIXME-style marker(s) in {path} (risk).")
    if rk.get("source_touched_without_tests"):
        source_count = len(files) - len(rk.get("tests_touched") or [])
        lines.append(f"- [observation] No test file changed alongside {source_count} source file(s) (risk).")
    lines.append(AGENT_AREA.format(
        instruction=("this branch has no work commits and no diff: describe the state of the ticket's code at the "
                     "branch head instead (read the located files; does it build, is it complete, what is wrong), "
                     "as [real] finds with anchors and [speculation] with grounding" if no_branch_work else
                     "characterize the actual code changes from the patch — what the code does, not just which "
                     "files moved; [speculation] with grounding")
        + "; read the located and touched files and record every definite error you can prove by reading both sides "
        "(declaration vs definition, call vs callee, duplicate definition, misspelled identifier, missing include or "
        "import) as `- [real] Defect: <what is wrong> (@[path]:line)`; a Defect is verified, a suspicion is a 'Risk:'"
        + "; optionally one 'Risk:' claim pointing at a bug, regression, or untested path, and one 'Next:' claim"
    ))
    lines.append("")

    lines += ["## Where", ""]
    for f in files[:MAX_PREFILL_FILES]:
        lines.append(f"- [observation] Edit already happened: {f['path']} (diff).")
    if not files:
        lines.append("- [observation] No edits on this branch yet relative to the merge-base (diff).")
    for path in rk.get("baseline_overlap") or []:
        lines.append(f"- [observation] Also changed on {baseline} since the merge-base: {path} — conflict risk on merge (risk).")
    for hit in (lo.get("files") or [])[:MAX_PREFILL_FILES]:
        terms = ", ".join(hit.get("terms") or [])
        lines.append(
            f"- [observation] Ticket terms found on {baseline}: {hit['path']} ({hit.get('matches', 0)} match(es): {terms}) (locate)."
        )
    if lo.get("terms") and not lo.get("files"):
        lines.append(f"- [observation] None of the ticket's terms ({', '.join(lo['terms'][:8])}) occur on {baseline} (locate).")
    lines.append(AGENT_AREA.format(
        instruction="[real] located ticket-related information: start from the locate hits above, confirm the symbols/paths "
        "from the ticket in the baseline, and cite them with anchors; do not re-search for terms the locate stage already placed"
    ))
    lines.append(AGENT_AREA.format(
        instruction="[speculation] where changes may be needed to solve the ticket, ranked grounded/adjacent/ungrounded; "
        "if nothing concrete, say so and name the tag-up person; optionally one 'Risk:' claim (where a change is likely "
        "to bite) and one 'Next:' claim (the first place to edit or check)"
    ))
    lines.append("")

    lines += ["## Why", ""]
    if ticket.get("title"):
        lines.append(f"- [observation] Ticket title: {ticket['title']} (ticket).")
    items = _readable_items(description_items(str(ticket.get("description") or "")))
    if len(items) == 1:
        lines.append(f"- [observation] Ticket description: {items[0]} (ticket).")
    elif items:
        for item in items:
            lines.append(f"- [observation] Ticket description item: {item} (ticket).")
    for criterion in ticket.get("acceptance_criteria") or []:
        if isinstance(criterion, str) and criterion.strip():
            lines.append(f"- [observation] Acceptance criterion: {criterion.strip()} (ticket).")
    lines.append(AGENT_AREA.format(
        instruction="primary sources first; your [speculation] synthesis at the bottom, composed from [real] findings "
        "and tagged; optionally one 'Risk:' claim (where the change and the ticket's intent diverge, or an acceptance "
        "criterion is unmet) and one 'Next:' claim (what would settle it)"
    ))
    lines.append("")

    lines += ["## State", ""]
    lines.append(
        f"- [observation] Branch {branch}: {mh.get('ahead_of_merge_base', '?')} commit(s) of work, "
        f"{mh.get('behind_baseline', '?')} behind baseline, merged back: {mh.get('merged_back', '?')} (merge_history)."
    )
    lines.extend(check_lines)
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
