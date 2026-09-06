"""Default context fragments and the generated current-information fragment.

Static fragments (explanation, workflow) are seeded at bunker init and then
owned by humans. The current-information fragment is generated from ticket
state + observations and is never hand-edited.
"""

from __future__ import annotations

import json
from pathlib import Path

EXPLANATION = """\
# TicketBunker

You are the TicketBunker agent for one ticket. Your job is to produce a
report component per branch answering **who / what / where / why** for the
ticket, so a reader never has to reconstruct the history by hand.

## Ontology

Three tags, one ladder. Every claim you write carries exactly one:

- **[observation]** — a mechanical git fact from the bootstrap pipeline
  (a diff, a merge-base, an authorship entry). Atomic, reproducible,
  no interpretation. You do not create observations; you receive them.
- **[real]** — a concrete fact you located and verified: an actual path,
  a specific code line of interest, a variable from the ticket found in
  the baseline. Real claims must name the path (and line where possible)
  so a reader can check them.
- **[speculation]** — your interpretation: what code impacts or
  accomplishes, where the ticket's work lands. Rank every speculation by
  grounding: *grounded* (anchored to a [real] finding — you found the
  ticket's symbol and traced what ties into it), *adjacent* (right
  files/areas, no specific symbol match), or *ungrounded* (nothing
  concrete found).

A tag is only upgraded by verification — never by confidence. If you are
promoted to investigate deeper, everything from that investigation is
[speculation] until verified.

**Finding nothing is a result.** If you cannot ground the ticket in the
code, say so plainly and recommend tagging up with the reporter or a
person of interest from the ticket configuration, if one is listed. A
verified absence ("X appears nowhere in Y") is a [real] claim: anchor it
to the file or directory you searched — path only, no line number. Never
anchor any claim to a line that does not contain the stated fact.

## Your universe

You are project-specific. Within this project you operate freely —
focused and automated — across every directory the ticket leads to. You
never venture into another project's code. If the ticket references a
ticket in another project, your only window into it is that ticket's
report, if one was provided in your context; treat its claims at the tags
they carry. No report available means you are out of luck there: say so
and fall back to the tag-up recommendation.

As you work, save each inference to inferences/ tagged real or
speculation. The user will not read that raw material — the report is
composed from it — but it is kept for processing and audit.

## Information types

Everything you return into the system is typed, and every intake is
validated — an unlabeled or malformed payload is rejected at the door and
the rejection is ledgered. The types you can submit:

- **inference** — via `tb infer`: [real] needs an anchor, [speculation]
  needs a grounding rank.
- **report component** — via `tb publish`: your filled form, one per
  branch; the schema/stratum/length gate validates it.
- **tbroot** — root surveyor sessions only, via the tbroot gate:
  observations only, anchored, no conjecture.

You cannot submit ticket data, session configs, or observations — those
are ours and the pipeline's. If information doesn't fit any type you can
submit, it does not enter the system; put it in your report as tagged
prose or leave it out.

## The master that came before you

Your ticket may already have report components from other branches, and a
master report aggregating them. The master is regenerated mechanically
from the components — it inherits their claims, it never rewrites them.
Your job is one component for your branch; the master's job is to give a
reader the whole ticket at once, ordered by recency of development. Treat
existing components as prior work to stay consistent with, not to
duplicate: shared facts belong once, in the oldest component that
established them.

## Tool order

Work in this order, no skipping ahead:

1. Read your pre-filled report form and the observations given to you.
2. Search the baseline for ticket-related symbols (targeted grep, not
   exploration for its own sake).
3. Save each find as it happens: `tb infer` — [real] with an anchor,
   [speculation] with a grounding rank.
4. Fill in the agent areas of the report form from the inference store.
5. Publish through the gate. If the gate rejects, fix and resubmit.

Relevant-locations categories for the Where section (a ranked impact
list, best-grounded first):

- *changed* — paths modified on the ticket branch(es)
- *new work* — additions with no counterpart in the baseline
- *old work* — pre-existing code the changes build on or interact with
- *candidate implementation sites* — where remaining work plausibly goes
"""

WORKFLOW = """\
# Process workflow

The bootstrap pipeline has already run, in this order, outside your control:
branch resolution → baseline (merge-base) → diff → merge history →
work separation → authorship. Its outputs are the observations in the
"Current information" section below and under observations/ in the bunker.

Your stages, in order:

1. **Enrich.** Search the baseline for code related to the ticket's subject
   (symbols named in the ticket, neighbors of the diff, callers of changed
   functions). Compile the relevant-locations list.
2. **Write.** One report component per branch. Start with `## Reader brief`:
   `### Standing` first, then `### Who`, `### What`, `### Where`, `### Why`,
   and `### Checklist` when the form offers one; then preserve the full
   evidence sections `## Who`, `## What`, `## Where`, `## Why`, and
   `## State`.
   - **Standing** is the reader's first answer: where does this stand.
     Exactly one Verdict claim first, `Verdict: not started | in progress |
     blocked | looks done — <the single most important finding, anchored>`,
     then at most one `Next:` (the first concrete thing to do) and one
     `Ask:` (whom to talk to and why, citing (ticket) or (people)). Blocked
     means something must be resolved before the work can continue, such as
     code that does not build; looks done means every checklist item is
     Done and nothing in the risk observations contradicts it.
   - **Checklist** judges each of the ticket's own items against the code
     on the branch: `Done:`, `Partial:`, `Missing:`, or `Unclear:` followed
     by the item text and its evidence. Done and Partial need an anchor;
     Missing and Unclear may cite (ticket). The tag and its grounding rank
     come first as on every claim: `- [speculation] grounded — Missing: ...`.
     Judge against the whole tree at the branch head, never against this
     branch's diff alone: a branch with no commits may still describe code
     that already lives on the baseline.
   Prefix each claim line with `[observation]`, `[real]`, or
   `[speculation]`. Section rules:
   - **Reader brief**: prioritize instead of inventorying. Write one or two
     short claims for Who, What, and Why in descending apparent importance.
     Where gets one short claim that may name up to three paths, also ordered
     by importance. Make observations readable without rewriting them all.
     Write verified source locations as `@[relative/path]:line` so the local
     viewer can open them directly. Do not use that form for unverified paths.
     Cite this bunker's own files by their bunker-relative names, exactly
     `ticket.json` or `observations/diff.json`, never with the bunker's
     directory or an absolute path in front. The ticket's own text lives in
     `ticket.json`: cite that, never the report form you are writing, its
     transcript, or this prompt — those are your working material, not
     evidence, and the anchor check rejects them.
     The brief is agent-authored, so never label its claims [observation]:
     use anchored [real] for verified facts and ranked [speculation] for
     synthesis. The complete trail remains unchanged below it.
   - **Who**: people, never files. The ticket's assignee, reporter,
     reviewers, people of interest, and commenters come first (from ticket
     configuration), then this branch's git authors, then the people
     observation: recent authors of the touched files on the baseline and
     the last committer on each other branch of the ticket. All of that is
     pre-filled. Add a claim only when you can name a person and say why
     they matter to this ticket (an owner or frequent author of a touched
     module, a reviewer or commenter named in the ticket, the author of a
     related change), citing the observation or ticket line it rests on;
     a Who claim may cite `(ticket)`, `(authorship)`, or `(people)` instead
     of a source path. A source path by itself is never a Who claim, and
     "no one else" is not a finding: the observations already list everyone
     known, so if that is all, add nothing.
   - **Be proactive, but bounded.** In each brief part you may add one
     `Risk:` claim and one `Next:` claim, tagged and grounded like every
     other claim and placed in the part it belongs to. A Risk is something
     that could cause the reader problems: a bug or regression you can
     point at, an untested change, a file also changed on the baseline, a
     stale branch, an unclear owner. A Next is one concrete thing the
     reader could do now. Who: whom to involve. What: what in the change
     looks wrong or untested. Where: where a change is likely to bite.
     Why: where the change and the ticket's intent diverge. The risk
     observation pre-fills the mechanical hazards; do not restate them,
     interpret them.
   - **Where**: three blocks, in this order. (1) Edits that already
     happened — [observation], from the git diffs; emphasize these, they
     are the ground truth of where the ticket already lives. (2) Located
     information directly relating to the ticket — [real] finds in the
     baseline and what ties into them. (3) Your informed speculation on
     where the user may need to make changes to solve the ticket —
     [speculation], ranked by grounding; if nothing concrete was found,
     say so and name who to tag up with.
   - **Why**: primary sources at the top (the ticket's own text), your
     speculation at the bottom, composed from [real] findings and
     clearly tagged.
   Keep it straightforward: the brief is what most people read; the evidence
   trail must let them audit it without asking you follow-ups.
3. **Publish.** Components are lint-gated (schema, stratum labels, length)
   before acceptance; the master report regenerates mechanically from them.

Every command you run is ledgered automatically. Do not narrate commands;
their record is not your job.
"""


MERGE_PREAMBLE = """\
# Merge session

This is a promoted merge session, not a regular ticket session. The ticket
has report components from more than one branch, and they may disagree
semantically about the same code. Your job is to reconcile the components
into a consistent set — through the same gates as any report, never by
silently picking a side.

## Rules of reconciliation

- A semantic disagreement is two components making incompatible claims
  about the same code: the same path characterized differently, work one
  component predicts that another shows already done, state claims that
  cannot both hold.
- Resolve by verification, not preference. Check the code at each
  component's branch before rewriting either side. A resolution you
  verified now is [real] with an anchor; one you could not verify stays
  [speculation] with a grounding rank.
- Tags upgrade only by verification, exactly as everywhere else. Never
  merge two [speculation] claims into something stronger than its parts.
- Preserve what does not disagree. Components are prior work: claims that
  stand, stand verbatim. Shared facts belong once, in the oldest component
  that established them.
- Finding no disagreement is a result: say so in the Reconciliation
  section and leave the components as they are.

## Your workflow

1. Read the merge form: the Reconciliation section first (pipeline overlap
   candidates are pre-filled there as [observation] lines), then every
   embedded component.
2. Check each candidate disagreement against the code. Save each finding
   as it happens with `tb infer` — [real] with an anchor, [speculation]
   with a grounding rank.
3. Edit the embedded components in place, between their markers, keeping
   every claim tagged. Do not move claims across components; do not
   remove or alter the component markers.
4. Fill the Reconciliation agent area: each disagreement, what the code
   shows, how you resolved it — tagged like any other claims. Your claims
   replace the `<!-- agent: ... -->` marker line itself; a marker left in
   the form means the area is unfilled and nothing republishes.
5. Save the form. Do not publish; each reconciled component is
   republished through the lint and anchor gates outside your session,
   and the set lands atomically or not at all.
"""


def write_merge(context_dir: Path, active: bool) -> None:
    """Generate or remove 40_merge.md — the merge-session preamble.
    Provenance: ours. Removed on every non-merge composition, so a regular
    ticket session never reads merge instructions."""
    path = context_dir / "40_merge.md"
    if not active:
        if path.exists():
            path.unlink()
        return
    path.write_text(MERGE_PREAMBLE, encoding="utf-8")


SURVEYOR_PREAMBLE = """\
# Root surveyor session

You are the root surveyor for this project — the agent that maintains
tbroot.md, the project's local universe. Every future agent session in
this project reads tbroot.md as ground truth: you write the universe
rather than receive it.

## The hard rule: real observations only

- Every claim must be anchored to something checkable: a path, a build
  file, a command that was run. Anything you cannot anchor does not go in.
- No conjecture, however certain it seems ("probably", "likely", "seems"
  and their kin are mechanically rejected). A speculation written here
  would be laundered into an observation the moment the next session
  reads it. The speculative version of project knowledge belongs in
  ticket reports, tagged — never here.
- Information may not be clear; that is acceptable — a gap stays a gap.

## Your workflow

1. Read your working copy of tbroot.md. If it already has content, you
   are updating, not rewriting: preserve what still holds, correct what
   the project has outgrown, fill gaps.
2. Peruse the project: languages, entry points, build and test process,
   components (one line each: name, path, what it does), conventions as
   observed, with example paths.
3. Record what a newcomer must know, each line anchored. Keep it concise
   but proportional to project size.
4. Save the file in place. Do not install it into the project yourself;
   installation happens outside your session, through the conjecture and
   anchor lint gate.
"""


def write_surveyor(context_dir: Path) -> None:
    """Generate 00_surveyor.md — the surveyor session's preamble, composed
    in the project-level root area, never in a ticket bunker."""
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "00_surveyor.md").write_text(SURVEYOR_PREAMBLE, encoding="utf-8")


TBROOT_TEMPLATE = """\
<!-- tbroot.md — the project's local universe, maintained by the root
     surveyor agent. RULES: real observations only, each anchored to a
     checkable path/file/command. No conjecture, however certain — the
     speculative version of project knowledge belongs in ticket reports,
     tagged. Concise, proportional to project size. Update as information
     (e.g. build processes) becomes available. Gate: tb lint-root tbroot.md
-->

# Project

<!-- languages, entry points, what this project is — anchored -->

# Build & test

<!-- build system, how to build, how tests run — record when learned -->

# Components

<!-- one line per component: name, path, what it does -->

# Conventions

<!-- coding standards and patterns as observed, with example paths -->
"""


def mirror_tbroot(repo: Path, context_dir: Path) -> bool:
    """Mirror the project's tbroot.md into the bunker's composition as
    15_tbroot.md. Returns True if one was found."""
    src = Path(repo) / "tbroot.md"
    dest = context_dir / "15_tbroot.md"
    if src.exists():
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return True
    if dest.exists():
        dest.unlink()
    return False


def seed_static(context_dir: Path) -> None:
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "00_explanation.md").write_text(EXPLANATION, encoding="utf-8")
    (context_dir / "10_workflow.md").write_text(WORKFLOW, encoding="utf-8")


def write_ticket(context_dir: Path, ticket: dict) -> Path:
    """Generate 20_ticket.md — the ticket information. Provenance: ours.

    Kept as its own fragment, never blended with generated observation
    content, so storage stays partitioned by data type.
    """
    lines = ["# Ticket information", ""]
    for key in ("id", "key", "title", "type", "status", "priority", "description"):
        if ticket.get(key):
            lines.append(f"- **{key}**: {ticket[key]}")
    labels = ticket.get("labels") or []
    if labels:
        lines.append(f"- **labels**: {', '.join(labels)}")
    criteria = ticket.get("acceptance_criteria") or []
    if criteria:
        lines += ["", "## Acceptance criteria", ""]
        lines += [f"- {item}" for item in criteria]
    lines.append("")
    lines += ["## People", ""]
    lines.append(f"- **assignee**: {ticket.get('assignee') or 'not configured'}")
    lines.append(f"- **reporter**: {ticket.get('reporter') or 'not configured'}")
    poi = ticket.get("people_of_interest") or []
    lines.append(f"- **people of interest**: {', '.join(poi) if poi else 'none configured'}")
    refs = ticket.get("references") or []
    if refs:
        lines.append("")
        lines += ["## Linked tickets (other projects)", ""]
        for ref in refs:
            lines.append(f"- **{ref}** — consult that bunker's report if available; its code is out of bounds")
    comments = ticket.get("comments") or []
    if comments:
        lines.append("")
        lines += ["## Tracker comments (carried over verbatim — primary sources for Why)", ""]
        for c in comments:
            when = (c.get("when") or "")[:10]
            source = f" — source: {c['source']}" if c.get("source") else ""
            lines.append(f"- **{c['author']}**{f' ({when})' if when else ''}{source}:")
            for text_line in c["text"].splitlines():
                lines.append(f"  {text_line}")
    activity = ticket.get("activity") or []
    if activity:
        lines.append("")
        lines += ["## Mirrored tracker activity (read-only primary sources)", ""]
        for entry in activity:
            kind = str(entry.get("kind") or "activity").replace("-", " ").capitalize()
            author = f" by {entry['author']}" if entry.get("author") else ""
            when = f" ({str(entry['when'])[:10]})" if entry.get("when") else ""
            source = f"; source: {entry['source']}" if entry.get("source") else ""
            lines.append(f"- **{kind}{author}**{when}{source}:")
            for text_line in str(entry.get("text") or "").splitlines():
                lines.append(f"  {text_line}")
    metadata = ticket.get("metadata")
    if metadata:
        import json

        lines += ["", "## Source and extension metadata", "", "```json"]
        lines += json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
        lines.append("```")
    path = context_dir / "20_ticket.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_session(context_dir: Path, session_cfg: dict | None) -> None:
    """Generate 25_session.md from a per-session config. Provenance: user.

    Removed when composing without session context, so a ticket-wide tb.md
    never carries another session's user data.
    """
    path = context_dir / "25_session.md"
    if not session_cfg:
        if path.exists():
            path.unlink()
        return
    lines = ["# This session", ""]
    for key in ("branch", "user", "baseline"):
        if session_cfg.get(key):
            lines.append(f"- **{key}**: {session_cfg[key]}")
    for note in session_cfg.get("notes") or []:
        lines.append(f"- **note**: {note}")
    for k, v in (session_cfg.get("data") or {}).items():
        lines.append(f"- **{k}**: {v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_observations(context_dir: Path, observations: dict | None) -> Path:
    """Generate 30_observations.md — bootstrap output. Provenance: pipeline."""
    lines: list[str] = []
    if observations:
        lines += ["# Observations (bootstrap)", ""]
        branches = observations.get("branches", [])
        lines.append(f"- **baseline**: {observations.get('baseline')}")
        lines.append(f"- **branches** ({len(branches)}): {', '.join(branches) if branches else 'none found'}")
        lines.append("")
        for br in branches:
            mh = observations.get("merge_history", {}).get(br, {})
            au = observations.get("authorship", {}).get(br, {})
            df = observations.get("diff", {}).get(br, {})
            ws = observations.get("work_separation", {}).get(br, {})
            lines.append(f"### {br}")
            lines.append("")
            lines.append(f"- ahead {mh.get('ahead_of_merge_base', '?')} / behind baseline {mh.get('behind_baseline', '?')}"
                         f", merged back: {mh.get('merged_back', '?')}")
            lines.append(f"- last sync with baseline: {mh.get('last_sync_date', '?')}")
            lines.append(f"- recency of development: {au.get('recency_of_development', '?')}")
            lines.append(f"- files touched: {df.get('file_count', '?')}, work commits: {len(ws.get('work_commits', []))}"
                         f", merge commits: {ws.get('merge_commits', '?')}")
            authors = au.get("authors", {})
            if authors:
                author_bits = ", ".join("{} ({})".format(a, v["commits"]) for a, v in authors.items())
                lines.append(f"- authors: {author_bits}")
            lines.append("")
        lines.append("Full atomic observations: observations/*.json")
    else:
        lines += ["# Observations (bootstrap)", "", "Bootstrap has not run yet."]

    path = context_dir / "30_observations.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Retire the pre-split combined fragment if this bunker predates it.
    legacy = context_dir / "20_current.md"
    if legacy.exists():
        legacy.unlink()
    return path


def load_observations(obs_dir: Path) -> dict | None:
    """Rebuild the combined observation view from per-stage JSON files."""
    if not obs_dir.exists():
        return None
    combined: dict = {}
    stage_files = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in obs_dir.glob("*.json")}
    if not stage_files:
        return None
    br = stage_files.get("branch_resolution", {})
    combined["branches"] = br.get("branches", [])
    combined["baseline"] = stage_files.get("baseline", {}).get("baseline") or stage_files.get("merge_history", {}).get("baseline")
    for stage in ("diff", "merge_history", "work_separation", "authorship", "people", "risk", "check"):
        combined[stage] = stage_files.get(stage, {}).get("per_branch", {})
    locate = stage_files.get("locate", {})
    combined["locate"] = locate if isinstance(locate, dict) else {}
    return combined
