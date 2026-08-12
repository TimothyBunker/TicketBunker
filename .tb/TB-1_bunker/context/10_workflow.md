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
   Keep it straightforward: the report is read by people who cannot ask
   you follow-ups.
3. **Publish.** Components are lint-gated (schema, stratum labels, length)
   before acceptance; the master report regenerates mechanically from them.

Every command you run is ledgered automatically. Do not narrate commands;
their record is not your job.
