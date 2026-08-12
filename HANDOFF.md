# TicketBunker — agent handoff brief

You are picking up live testing and iteration of TicketBunker. Read this,
then DESIGN.md (the authority on every design decision), then README.md
(user-facing flow). The design has zero open questions; your job is contact
with reality, not redesign.

## What this project is

An intermediate context layer between agile trackers (Jira etc.) and git.
Per-ticket "bunkers" hold: a deterministic git observation pipeline's output
(atomic facts), a composed context document (tb.md) fed to an agent, and the
agent's report — a pre-filled form answering who/what/where/why, with every
claim tagged [observation] / [real] / [speculation]. Reports are the only
artifact users read, the only thing that crosses project boundaries, and
the bunker database lives in `.tb/` at the git root, committed with the
repo. The CLI is `tb` (stdlib-only Python package `ticketbunker/`).

Trust model, non-negotiable:
- **observation** = pipeline-produced mechanical git fact
- **real** = agent-located, checkable (must carry a path/line anchor)
- **speculation** = interpretation, ranked grounded/adjacent/ungrounded
- Tags upgrade only by verification. Finding nothing is a reportable result
  ("tag up with the reporter").
- Everything is enforced structurally where possible: ledger entries are
  side effects of execution wrappers (never agent volition), all
  information enters via typed intake handlers that validate and ledger
  rejections, lint gates check report form before publish.

## Current state (verified)

- Full loop green with a stub harness: `tb init` → `tb ticket` → `tb branch`
  → `tb bootstrap` → `tb run` (compose → scaffold → harness → gated publish
  → master regen) → commit `.tb/` with the repo.
- pi profile (pi.dev, `@earendil-works/pi-coding-agent`): verified to the
  provider-login wall; extension `harness/pi/tb.ts` confirmed loading and
  writing ledger entries from inside pi.
- codex profile: first live run on this repo executed but the model filled
  nothing (read-only sandbox). Fixes landed: `--sandbox workspace-write`,
  tb run now refuses to publish forms with remaining `<!-- agent: -->`
  areas, harness transcript saved to `sessions/<slug>.transcript.txt`.
- The dogfood ticket is **TB-1** in this repo's `.tb/`, branch
  `claude/jira-git-context-agent-artex0`, baseline `main` (anchored at the
  root commit, so the diff is the whole project).

## Your mission

Drive live agent runs to a *good* report, fixing what reality exposes.

1. **Re-run the live test** until an agent-filled report publishes:
   ```sh
   tb run TB-1 --branch claude/jira-git-context-agent-artex0 --repo . --harness codex
   # pi alternative (needs `pi /login` done): --harness pi --provider anthropic --model "claude-*"
   # escape hatch for odd CLI flags:
   #   --harness command --cmd '<agent-cli> {prompt}'   (placeholders: {form} {tb_md} {repo} {prompt})
   ```
   On failure: read `sessions/<slug>.transcript.txt` in the bunker, diagnose,
   fix the adapter/prompt/fragments, re-run.

2. **Grade every published report** — this is the actual test:
   - Every `[real]` claim: does the anchor exist? (spot-check with grep)
   - Every `[speculation]`: is the grounding rank honest, or inflated?
   - Where section: blocks in order (past edits → real finds → ranked
     speculation)? Are the finds genuinely relevant?
   - Why: readable by someone who never saw this session?
   - If the model mislabels or rambles: tune `ticketbunker/builders/fragments.py`
     (the agent-facing instructions) or the lint in
     `ticketbunker/processors/reportlint.py` — from evidence, not taste.

3. **Convert every raw traceback into a helpful error.** The standard: a
   first-time user's mistake should print what happened and what to type
   next, never a stack trace. Several were already converted (missing
   baseline, unresolvable bunker); expect more.

4. **Known queued work, in rough priority order:**
   - Composed-size check: warn (in `tb compose`/`tb run`) when tb.md exceeds
     a sane token budget — a 40k-token preamble fails silently.
   - Publish-time cross-check: every [real] claim in a report should trace
     to an inference-store entry (`inferences/inferences.jsonl`) and its
     anchors should exist in the repo. Closes the "formatted like truth vs
     checked like truth" gap. Design allows it; not yet built.
   - Root surveyor runner: `tb survey` seeds/lints tbroot.md but nothing
     *runs* a surveyor agent session yet (same harness machinery as tb run,
     different preamble — it writes the universe rather than receives it,
     gated by `lint_root`).
   - Git-hook triggers (post-push → bootstrap refresh) — design §3.1/§3.8.

## Rules (violating these is worse than any bug)

- **Never weaken the trust architecture** to make a test pass: no untyped
  intake paths, no unledgered execution wrappers, no publish routes around
  the gate, no conjecture permitted into tbroot.md.
- **Data provenance separation** (DESIGN.md §4): ours / pipeline / agent
  data never intermingle at rest. Mixing happens only at composition.
- **Stdlib-only core.** No pip dependencies in `ticketbunker/`. The pi
  extension (TypeScript) is the only non-Python code, and it stays thin.
- **Update DESIGN.md when behavior changes meaningfully** — it is the
  single source of truth and currently fully consistent with the code.
- Work on branch `claude/jira-git-context-agent-artex0`; commit in focused
  units with descriptive messages; push after each verified fix.
- Test in a scratch directory, not by littering this repo; the repo's own
  `.tb/` is the *dogfood* database — treat its TB-1 bunker as real data.
- After changing the package: reinstall before testing the installed CLI
  (`pip install --force-reinstall .`) or test via `PYTHONPATH=. python3 -m
  ticketbunker.cli`.

## Where things live

| Thing | Path |
|---|---|
| Design authority | `DESIGN.md` (§3.x architecture, §4 principles) |
| Bunker layout | `ticketbunker/layout.py` (only place paths are defined) |
| Type registry / validation | `ticketbunker/types/` |
| Intake (only write paths) | `ticketbunker/handlers/` |
| Agent-facing instructions | `ticketbunker/builders/fragments.py` |
| Lint gates | `ticketbunker/processors/reportlint.py` |
| Observation pipeline | `ticketbunker/processors/stages.py` |
| Harness adapters | `ticketbunker/adapters/harness.py`, `harness/pi/tb.ts` |
| Stub agent (credential-free E2E) | `tools/stub_agent.py` |
| Single-file build | `python3 tools/build_tb.py` → `dist/tb` |

## Definition of done for this phase

A codex- or pi-driven `tb run` on TB-1 publishes a report where the human
grader (Tim) agrees: the [real] anchors check out, the speculation ranks are
honest, and a teammate could answer who/what/where/why from the report alone.
When that holds, the next phase is pointing it at a real work repo.
