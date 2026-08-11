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
person of interest from the ticket configuration, if one is listed.

As you work, save each inference to inferences/ tagged real or
speculation. The user will not read that raw material — the report is
composed from it — but it is kept for processing and audit.

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
2. **Write.** One report component per branch with sections:
   `## Who`, `## What`, `## Where`, `## Why`, `## State`.
   Prefix each claim line with `[observation]`, `[real]`, or
   `[speculation]`. Section rules:
   - **Who**: the ticket's assignee, reporter, and people of interest
     first (from ticket configuration), then git authors.
   - **Where**: a ranked impact list — grounded speculation first,
     adjacent next, ungrounded last; if nothing concrete was found, say
     so and name who to tag up with.
   - **Why**: primary sources at the top (the ticket's own text), your
     speculation at the bottom, composed from [real] findings and
     clearly tagged.
   Keep it straightforward: the report is read by people who cannot ask
   you follow-ups.
3. **Publish.** Components are lint-gated (schema, stratum labels, length)
   before acceptance; the master report regenerates mechanically from them.

Every command you run is ledgered automatically. Do not narrate commands;
their record is not your job.
"""


def seed_static(context_dir: Path) -> None:
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "00_explanation.md").write_text(EXPLANATION, encoding="utf-8")
    (context_dir / "10_workflow.md").write_text(WORKFLOW, encoding="utf-8")


def write_current(context_dir: Path, ticket: dict, observations: dict | None) -> Path:
    """Generate 20_current.md from ticket state + latest observations."""
    lines = ["# Current information", "", "## Ticket", ""]
    for key in ("id", "key", "title", "priority", "description"):
        if ticket.get(key):
            lines.append(f"- **{key}**: {ticket[key]}")
    lines.append("")
    lines += ["## People", ""]
    lines.append(f"- **assignee**: {ticket.get('assignee') or 'not configured'}")
    lines.append(f"- **reporter**: {ticket.get('reporter') or 'not configured'}")
    poi = ticket.get("people_of_interest") or []
    lines.append(f"- **people of interest**: {', '.join(poi) if poi else 'none configured'}")
    lines.append("")

    if observations:
        lines += ["## Observations (bootstrap)", ""]
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
        lines += ["## Observations", "", "Bootstrap has not run yet."]

    path = context_dir / "20_current.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
    for stage in ("diff", "merge_history", "work_separation", "authorship"):
        combined[stage] = stage_files.get(stage, {}).get("per_branch", {})
    return combined
