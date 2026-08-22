# TicketBunker — agent handoff brief

You are picking up live testing and iteration of TicketBunker. Read this,
then DESIGN.md (the authority on every design decision), then README.md
(user-facing flow). Design questions that look open are usually settled in
DESIGN.md; your job is contact with reality, not redesign.

## What this project is

An intermediate context layer between agile trackers (Jira etc.) and git.
Per-ticket "bunkers" hold: a deterministic git observation pipeline's output
(atomic facts), a composed context document (tb.md) fed to an agent, and the
agent's report — a pre-filled form answering who/what/where/why, with every
claim tagged [observation] / [real] / [speculation]. Reports are the only
artifact users read and the only thing that crosses project boundaries. By
default the canonical database lives in a companion Git repository; the source
commits `.ticketbunker.json` and ignores its `.tb/config` and `.tb/data`
checkouts. The CLI is `tb` (stdlib-only Python package `ticketbunker/`).

Trust model, non-negotiable:
- **observation** = pipeline-produced mechanical git fact
- **real** = agent-located, checkable — must carry a path/line anchor, and
  the anchor is *verified at the publish gate* (see below)
- **speculation** = interpretation, ranked grounded/adjacent/ungrounded
- Tags upgrade only by verification. Finding nothing is a reportable result.
- Everything is enforced structurally: ledger entries are side effects of
  execution wrappers (never agent volition), all information enters via
  typed intake handlers (`ticketbunker/handlers/`) that validate and ledger
  rejections, lint + anchor-verification gates run before publish.

## Current state (all verified, all pushed)

- **Companion storage is the default**: one-time `tb init` publishes separate
  `ticketbunker/config` and `ticketbunker/data` branches before writing the
  source locator; later clones use repeatable `tb setup`. Remote Git and branch
  permissions are the only authority. The user creates the companion repository
  anywhere and supplies its URL; init verifies that it exists before any local
  project state is written and never calls a hosting API to create it. `tb migrate` backs up an embedded `.tb`
  below `.git`, publishes all ticket material, and only then removes legacy
  tracked paths. Successful companion data mutations reconcile and push on
  command completion. Live dogfood verified on `TimothyBunker/coal`: ECS-4
  migrated byte-for-byte to private `TimothyBunker/coal-ticketbunker`; source
  cleanup is `coal:ecs` commit `8a70b18`. A fresh source clone recovered the
  complete ticket/report using only its locator plus `tb setup`, with source,
  config, and data worktrees all clean.
- **Configuration is remote and composable**: `tb config` opens a localhost
  editor for Identity, Repository, Ticket fields, and GitHub/GitLab/Jira
  mappings/tokens. Integration mappings are an add/remove row matrix published
  atomically; GitHub Issues, GitLab Issues, and Jira Cloud are prefilled editable
  templates, and custom integrations use the same mapping plus local-token
  contract. Existing projects pin immutable field-set versions; global
  default edits seed only future projects. Tokens stay local, outside the
  repository. CLI, editor HTTP, and programmatic callers share
  `ticketbunker.commands` handlers and stable rejection codes.

- **Full loop green end-to-end**, stub-harness and live: `tb init` →
  `tb ticket` → `tb branch` → `tb bootstrap` → `tb run` (compose → scaffold
  → harness → verified publish → master regen) → `tb checkout` → `tb view`.
- **First live codex report published and human-graded good** (ticket TB-1
  in this repo's `.tb/` — the dogfood database; treat it as real data).
  pi profile is verified only to the provider-login wall.
- **Ticket surface mirrors Jira**: types (bug/task/story), statuses
  (backlog/todo/in-progress/in-review/done/closed), labels, priorities,
  mechanical created/updated stamps ('updated' moves only on meaningful
  edits). `tb ticket` with no flags opens $EDITOR on a template; `--from
  FILE` and flags for scripting; `tb set` moves fields after creation —
  same gate, never a hand edit. `tb comment` carries tracker comments over
  verbatim with original authors; `--bulk` pastes a whole thread
  (`--- author [when] [source]` separators).
- **Branch↔ticket association**: the branch-match key defaults to the
  ticket id, matching is *bounded* (TB-1 never claims TB-11's branches;
  `stages.relate_branches` is the single rule), and `tb branch <T>
  --assign BRANCH` adopts a branch whose name carries no id
  (`assigned_branches` in ticket.json, ours provenance).
- **Anchor verification at the gate** (`processors/anchorcheck.py`): every
  [real] claim's anchors must resolve — repo at the branch, at the exact
  merge-base its observations came from, or bunker files — lines checked
  against file length. Failures reject + ledger. Accepted components get a
  mechanical `tb:verified branch/commit/ts` stamp; the master's manifest
  and digest point at that ref; `tb show`/viewer badge components whose
  branch developed past verification.
- **Master anti-bloat**: master.md leads with a Digest (each child's State
  section, selected verbatim, never rewritten) before full components.
- **Read path**: `tb list` (status groups; `--sort key|priority|created|
  updated`, `--reverse`, `--fetch` includes remote-only tickets),
  `tb show` (fields, comments, development panel, report state, ledger
  tail), `tb view` (localhost web viewer, auto-reloads via database
  fingerprint polling). Per-ticket pages split ticket-owned and agent-owned
  content into Ticket/Agent tabs. Ticket is the Jira-like default: rich-text
  description, acceptance criteria, and mirrored read-only activity in the main
  column. Project, mapped status, labels, and source-updated time lead the page;
  human-relevant fields follow in Details while transport IDs/provider flags sit
  in a Metadata disclosure and redundant source fields stay hidden. These roles
  are provider-neutral. Agent work defaults to a branch-separated Master
  synthesis; a native selector and its Branches rail switch to any child
  component while its full evidence stays collapsed by category.
  **Viewer is localhost-only BY DESIGN — no bind option, no
  auth surface, remote viewing is SSH port-forwarding's job. Do not add
  network exposure.**
- **Git-native merge story**: generated data-branch `.gitattributes` — ledgers and
  inference logs `merge=union`, master `merge=ours` + regenerated;
  `tb hooks --install` adds post-commit/post-merge hooks running
  `tb refresh` (resolve current branch's ticket → re-bootstrap → recompose
  → regen master; never blocks git). In companion mode `tb checkout` refreshes
  the canonical child set and regenerates; embedded legacy mode retains the
  original branch-collection path.
- **Agent merge session** (`tb merge`, DESIGN.md §3.13): promoted session
  reconciling report components that may disagree about the same code.
  Mechanical anchor-overlap candidates pre-filled as [observation] lines;
  components embedded verbatim in a merge form between markers; merge
  preamble fragment (`40_merge.md`, removed on non-merge composes); own
  stage contract (`merge_session`) on the same harness path; republication
  through the same lint + anchor gates, all-or-nothing (pre-checked before
  any write), `merge_reconciled`/`merge_rejected` ledgered, master regen.
  Stub-verified end-to-end incl. adversarial cases (fabricated [real]
  anchor and broken markers both rejected atomically). **Live-graded good**
  (codex gpt-5.6-sol, scratch repo with two mutually exclusive limiter
  branches): named the real disagreement with branch-qualified anchors
  that all resolve, cleared the two false overlap candidates by verifying
  the shared files identical, upgraded verified claims speculation→real
  (the only allowed direction), and declined to pick a winner — selection
  stays human. First attempt failed the unfilled check by inserting above
  the `<!-- agent: -->` marker instead of replacing it; the failure path
  behaved exactly as designed (atomic, ledgered, form preserved) and the
  prompts now state that claims replace the marker line.
- **Root surveyor runner** (`tb survey REPO --run`, DESIGN.md §3.10):
  session home at `.tb/root/` (bunker-shaped, above all tickets), working
  copy seeded from existing tbroot.md or the template, surveyor preamble
  fragment (`00_surveyor.md`), own stage contract (`survey_session`),
  installation only through the new typed `intake_tbroot` handler
  (conjecture + anchor gate, ledgered). Untouched fresh template fails;
  unchanged existing tbroot is `survey_no_update`. Stub-verified incl.
  conjecture rejection. **Live-graded good** (codex gpt-5.6-sol, this
  repo): zero conjecture, every spot-checked anchor true — including the
  exact dist/tb zipapp byte size and the 7-test unittest run it performed
  to earn its Build & test claims — installed as tbroot.md and committed.
  Refresh-triggered surveys stay future work under the re-run cost policy
  (mission item 4).

## Your mission, in priority order

1. **pi harness live verification** past the login wall (`pi /login`
   first; extension `harness/pi/tb.ts` is confirmed loading). Grade the
   report like the codex one was: spot-check every [real] anchor, audit
   speculation ranks, read Where/Why cold. (`tb merge` and `tb survey
   --run` are already live-graded good on the codex harness; codex runs
   need node 24 — `nvm use 24` first.)
2. **Composed-size check**: warn in `tb compose`/`tb run` when tb.md
   exceeds a sane token budget — a 40k-token preamble fails silently.
3. **Ticket archival**: closed tickets should age out of the default
   `tb list` view (Jira-style), without deleting bunker data.
4. **Re-run policy**: hooks refresh observations, but agent re-runs are
   manual. Decide + build the trigger policy for re-running `tb run` when
   a report is badged "predates latest development" (cost-aware — an
   agent session per commit is too much; per push or on demand may be
   right). Talk to Tim before building this one.

The first real work repo is `/mnt/c/Users/Tim/Desktop/voxel-engine-starter`
(`ECS`, source remote `TimothyBunker/coal`); its companion is
`TimothyBunker/coal-ticketbunker`.

## Rules (violating these is worse than any bug)

- **Never weaken the trust architecture** to make a test pass: no untyped
  intake paths, no unledgered execution wrappers, no publish routes around
  the gates (lint *and* anchor verification), no conjecture into tbroot.md.
- **Data provenance separation** (DESIGN.md §4): ours / pipeline / agent
  data never intermingle at rest. Mixing happens only at composition.
- **Stdlib-only core.** No pip dependencies in `ticketbunker/`. The pi
  extension (TypeScript) is the only non-Python code, and it stays thin.
- **Update DESIGN.md in the same commit** as any meaningful behavior
  change — it is the single source of truth, currently fully consistent.
- **All bunker paths come from `ticketbunker/layout.py`** — never
  hand-build one.
- **User-facing errors say what happened and what to type next.** A raw
  traceback reaching a user is a bug even when the complaint is legitimate.
- Work on branch `claude/jira-git-context-agent-artex0`; focused commits,
  descriptive messages, push after each verified fix.
- Test in a scratch dir (e.g. /tmp), driving the CLI via
  `PYTHONPATH=. python3 -m ticketbunker.cli ...`; use
  `tools/stub_agent.py` as the credential-free harness
  (`--harness command --cmd "python3 tools/stub_agent.py {form}"`). Note
  the stub discovers a real file to anchor to — even the stub must pass
  the anchor gate.
- This repo's own `.tb/` (ticket TB-1) is the dogfood database — real
  data, not a scratch pad.
- After changing the package: `pip install --force-reinstall .` before
  testing the installed `tb`, or test via the PYTHONPATH form.
- Tim's working style: no ceremony. Direct conversational design —
  explore the code, propose a compact concrete design with a
  recommendation, let him react. Don't run staged brainstorm/plan
  workflows.

## Where things live

| Thing | Path |
|---|---|
| Design authority | `DESIGN.md` (§3.x architecture, §4 principles) |
| Bunker layout + gitignore/gitattributes templates | `ticketbunker/layout.py` |
| Type registry / validation | `ticketbunker/types/` |
| Intake (only write paths) | `ticketbunker/handlers/` |
| Agent-facing instructions | `ticketbunker/builders/fragments.py` |
| Ticket editor template + thread parser | `ticketbunker/builders/ticketform.py` |
| Lint gates | `ticketbunker/processors/reportlint.py` |
| Anchor verification + verified stamps | `ticketbunker/processors/anchorcheck.py` |
| Merge session (preamble, form build/split, runner) | `builders/fragments.py`, `builders/scaffold.py`, `cli.py` cmd_merge |
| Surveyor session (preamble, runner, tbroot intake) | `builders/fragments.py`, `cli.py` cmd_survey, `handlers/` intake_tbroot |
| Observation pipeline + branch relation rule | `ticketbunker/processors/stages.py` |
| Master regen + digest | `ticketbunker/builders/master.py` |
| Local web viewer | `ticketbunker/viewer.py` |
| Shared mutation commands | `ticketbunker/commands.py`, `ticketcommands.py`, `projectconfig.py` |
| Companion storage + migration | `ticketbunker/project.py` |
| Local configuration editor | `ticketbunker/config_viewer.py` |
| Harness adapters | `ticketbunker/adapters/harness.py`, `harness/pi/tb.ts` |
| Stub agent (credential-free E2E) | `tools/stub_agent.py` |
| Single-file build | `python3 tools/build_tb.py` → `dist/tb` |

## Definition of done for this phase

The merge session and surveyor runner exist and pass the same standard the
codex report did: gates enforced structurally, outputs human-graded honest.
Then TicketBunker gets pointed at a real work repo with a real tracker
ticket, and the report answers who/what/where/why for someone who wasn't
there.
