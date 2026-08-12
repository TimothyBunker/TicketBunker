# TicketBunker — Design

## 1. Problem

Agile tickets are the interface between people, but git is where the truth lives.
The two drift apart because:

- Workers don't (or can't) log progress, branch names, or merge state on the ticket.
- Teams communicate *through* tickets to people who can't read the code — either
  because code is lost on them, or because they're not permitted to see it (e.g.
  someone reporting an anomaly in data their code produced downstream).
- On classified programs, the unclass ticket is often all the context that crosses
  the boundary, and it's frequently not enough — even beyond what the boundary
  itself strips out.

The result: whoever picks up the ticket next spends hours tracking down who
touched what, on which branch, against which baseline, and why.

## 2. Concept

An **intermediate agent layer** that sits between the tracker and the repo:

```
┌─────────────┐     ticket context      ┌──────────────────┐    git ops     ┌─────────┐
│ Jira / agile │ ──────────────────────▶ │   TicketBunker   │ ─────────────▶ │  repos  │
│   tracker    │ ◀────────────────────── │  (session layer) │ ◀───────────── │  (git)  │
└─────────────┘   who/what/where/why    └──────────────────┘   diffs, logs  └─────────┘
```

It receives ticket context, resolves related branches, mechanically extracts the
git facts, lets an agent enrich them with repo search, and reports a structured
summary back to the requesting user.

**Deployment principle:** TicketBunker is a **layer on top of an existing git
project** — any company, any security level. That constrains everything: the
core processes must be **exceedingly simple and diagnosable**, self-hostable
inside whatever boundary the repo already lives in, and adoptable without
changing the project itself. Diagnosability is not a feature bolted on later;
it is the design driver for §3.7–3.8.

**Ownership principle:** **Pi does not own this system.** TicketBunker has its
own backend; pi is a *client harness* that talks to it. Sessions run in pi
because its extension model fits our stage discipline (§3.7), but the source
of truth — ticket registry, bunker index, report storage, execution ledger —
lives in the TicketBunker backend. If pi were replaced tomorrow, the bunkers,
reports, and ledgers would be untouched.

**Backend principle: the backend is a git repo.** The team's database — every
bunker, with the information another user would need: tickets and their
corresponding reports, configs, even session data where cheap — **lives in a
remote git repository and is transferred regularly through git**. TicketBunker
functionally mirrors git rather than standing up a whole new service:
clone/pull/push are the sync protocol, history is the audit trail, remotes are
the access model, and a customizable **`.tbignore`** controls what enters the
shared history (harness session files and other local-only material stay
local). Anywhere git already works — including air-gapped enclaves that move
git bundles across boundaries — the TicketBunker backend already works. A
thin API service can be layered on top later if a team wants one; it is an
optimization, never the foundation.

## 3. Architecture

### 3.1 Ticket bunkers, sessions, and storage

**Cardinality model:**

- Each **branch** has a **user** (the owner/author of that work).
- Each **logged ticket requires at least one branch** — no branch, nothing to
  bunker — and may have **multiple branches** over its life. Branches within a
  ticket are **ordered by recency of development**, or by **priority when one
  is assigned**; this ordering is also the section order of the master report
  (§3.4).
- A ticket may span **multiple directories**.
- Each ticket has a **unique ID**, and that ID names its **ticket bunker**.
  Uniqueness is a hard requirement — the bunker directory *is* the ticket's
  identity in the system, so collisions are unacceptable.

**On-disk layout — one bunker per ticket:**

```
<ticket_id>_bunker/
├── tb.md                      # composed session-context document (§3.9) —
│                              #   exactly what gets prepended to a session
├── context/                   # the separate fragments tb.md is composed from
│   ├── 00_explanation.md      #   [ours] purpose, rules, ontology, how to
│   │                          #     write reports, tool order, and the
│   │                          #     purpose of the master (§3.9)
│   ├── 10_workflow.md         #   [ours] process workflow / stage expectations
│   ├── 15_tbroot.md           #   [agent, gated] the project's tbroot.md,
│   │                          #     mirrored in at bootstrap (§3.10)
│   ├── 20_ticket.md           #   [ours] the ticket information — people,
│   │                          #     priority, description (XML optional for
│   │                          #     easy injection)
│   └── 30_observations.md     #   [pipeline] latest bootstrap observations
├── sessions/                  # per-session configs, one per branch session:
│   └── <branch_slug>.json     #   user data injected into setup, for the
│                              #   agent and the backend (§3.11)
├── observations/              # bootstrap stage outputs — atomic, JSON
├── inferences/                # agent's saved working inferences, tagged
│                              #   real vs speculation — agent-facing, not
│                              #   user-facing (§3.3)
├── ledger.jsonl               # execution ledger (mirrored to the backend)
└── reports/                   # the deliverable artifacts — OUTSIDE the session
    ├── master                 # aggregate view; auto-regenerated when any
    │                          #   child component changes
    └── branches/
        └── <branch_slug>      # one report component per branch, owned by
                               #   the session (see §3.4)
```

Note what is *not* here: the session file. **The harness persists its own
session data** in its own storage (pi keeps its single-file sessions wherever
pi keeps them). The bunker holds what TicketBunker owns — context, facts,
ledger, reports — and nothing the harness owns.

**Local vs. remote:**

- The **pi session is local-first**: it comes into existence when someone makes a
  new branch for the ticket, on that user's machine. It is the working substrate,
  not the product.
- The **report is the remotely-handled artifact** — it's what gets synced,
  stored, and served to other users.
- If session storage turns out to be cheap (pi sessions are single JSON files,
  so plausibly yes), sessions can be stored remotely too — useful for
  resuming/refreshing a bunker from a different machine. But this is an
  optimization, not a requirement: the report must stand alone.

**Ticket context enters the session either:**

- **manually** (a user pastes / uploads the ticket context — the known drawback,
  see §7), or
- **automatically**, when new branches matching the ticket key appear on the
  remote but are not yet known to the service — the session ingests them and
  refreshes its observations.

Report state is queryable by **authorized users only** (see §6).

**Harness: pi (pi.dev).** Pi's role is deliberately narrow — it is *just the
harness that owns the session*. Concretely, three jobs:

1. **Communicating information to the agent** — injecting ticket context,
   bootstrap observations, and backend updates into the session.
2. **Managing sessions** — creation, persistence (single-file sessions make the
   `literal_pi_session.json` layout native), branching, resumption.
3. **Letting the agent explore the repo** — for what a git diff alone can't
   surface: context needed for a new ticket, explaining a commit, code the
   changes interact with.

Everything else — pipeline logic, stage contracts, ledger, reports, state —
belongs to the TicketBunker backend (§2). The two meet in both directions:

- **Backend → session:** the backend manages the stateful information — ticket
  registry, branch state, prior observations, report status — and **passes it
  to the pi session to populate it** with the relevant context. This happens at
  instantiation (the session starts pre-loaded from backend state, not empty)
  and on events afterward: e.g. a git command (via a hook) triggers the
  backend, which updates the session with the new state — a fresh push on a
  ticket branch flows into the bunker without anyone re-entering it.
- **Session → backend:** the executing hooks write ledger entries and publish
  reports through the API (§3.8).

**Harness portability:** we implement the integration as pi extensions, but
structured so that most of the added functionality and pipeline works with
other harnesses where possible. The stage contracts, bootstrap pipeline,
lint gates, master regeneration, and backend API are all harness-neutral —
plain processes speaking to an API. The pi extensions are a **thin adapter**:
they wire stages into pi's session lifecycle and command execution, and that
adapter layer is the only thing another harness would need reimplemented.
Pi remains the reference harness because its extension model and ordering
semantics fit the stage discipline best (§3.2, §3.7), and it runs **headless**
(`pi -p`, RPC over stdio, SDK embedding) — no TUI, no human in the loop, which
§3.5 depends on.

### 3.2 Deterministic bootstrap pipeline (the "observation" phase)

On session instantiation, harness extensions execute — in order, outside agent
control:

1. **Branch resolution.** Find branches whose names carry the ticket key
   (default: the ticket ID itself; convention: branches are labeled by ticket),
   unioned with branches explicitly assigned to the ticket (§3.14). Key
   matching is bounded — the key must sit against non-alphanumeric characters
   or the name's edge, so TB-1 never claims TB-11's branches. Each match
   records its rule — bounded key match or explicit assignment. Zero matches
   is itself an observation, and an assigned branch absent from the repo is
   recorded as missing, not silently dropped.
2. **Baseline identification.** Determine the reference branch (default: `main`,
   configurable per repo) and compute `merge-base` for each ticket branch.
3. **Diff extraction.** `git diff <merge-base> <branch>` per branch — the actual
   work, not noise from an aging fork point.
4. **Merge history.** When the branch last merged main (or was rebased), how far
   behind it is, and whether it has been merged back.
5. **Work separation.** Partition commits into *author work* vs *incidental
   merges/main churn*, so the report reflects what the person actually did.
6. **Authorship map.** `git log` / blame over the touched paths: who committed,
   when, in what order.

Every output of this phase is an **atomic observation**: a mechanical git fact,
reproducible from the same repo state, with no interpretation attached.

### 3.3 Agent enrichment (the "inference" phase)

The agent starts from **minimal prior context**: the ticket's input information, a
**baseline ontology** (the fixed vocabulary of §4 — observation vs. inference,
the who/what/where/why report schema, the relevant-locations categories), and
nothing else. All further context is **fed to it through commands** — the
bootstrap pipeline's outputs plus a constrained command set it can invoke itself.
The agent does not free-form its way around the repo; it composes commands.

After bootstrap, the agent runs with the observations as ground truth. It may:

- **grep / search the base branch** for code related to the ticket's subject
  matter (symbols named in the ticket, files adjacent to the diff, callers of
  changed functions).
- Compile a **relevant-locations list** for the reader:
  - *changed* — paths modified on the ticket branch(es),
  - *new work* — additions with no counterpart in the baseline,
  - *old work* — pre-existing code the changes build on or interact with,
  - *candidate implementation sites* — where remaining/future work plausibly goes.
- Synthesize the **who / what / where / why** narrative.

**The inference stratum splits in two.** Not all agent output carries the same
epistemic weight, and the report must say which is which:

- **Real** — a concrete fact the agent *located and verified*: an actual path,
  a specific code line of interest, a variable named in the ticket found in
  the baseline. Real findings are checkable by the reader (the thing is there
  or it isn't) and by the lint tooling (dead-reference check).
- **Speculation** — the agent's interpretation: what that code impacts, what
  it accomplishes, where the ticket's work plausibly lands. Speculation is
  explicitly tagged and always **ranked by grounding**:
  1. *grounded* — anchored to a real finding (e.g. the ticket names a variable,
     the agent found it, and traced what ties into it);
  2. *adjacent* — anchored to the neighborhood (right files/areas, no specific
     symbol match);
  3. *ungrounded* — nothing concrete found.

**Finding nothing is a first-class result.** If the agent can't ground the
ticket in the code, the report must say so and recommend the human move: tag
up with the **reporter** or a configured **person of interest**, when one is
available. An honest "talk to X" beats a confident guess. A verified absence
is itself a real finding, anchored to the searched file or directory — path
only, never a fabricated line number.

**Investigation promotion.** The agent may be promoted to investigate further
(deeper tracing beyond the standard enrichment); everything submitted from
such investigation carries the speculation tag — promotion raises effort,
never epistemic rank. Only verification can turn speculation into real.

**The inference store.** As the agent works it saves its inferences, tagged
real vs. speculation, under `inferences/` in the bunker. This is working
data: the user doesn't need the raw material, but it is kept so the agent can
process its final output from it (and so an audit can trace a report claim
back through the store to the ledger). The report is composed *from* the
store; the store is not the report.

### 3.4 Report system

**The report is a writing of the agent itself** — it is the inference layer's
output, authored over the bootstrap observations. That has three structural
consequences:

1. **One report component per branch.** Each session owns exactly one report
   component for each branch it covers (`reports/branches/<branch_slug>`). The
   session is the sole writer of its components; nothing else touches them.
2. **A master report aggregates the children.** `reports/master` is
   automatically updated whenever any child component is modified. Regeneration
   is **full, not incremental** — the master is always rebuilt from the current
   set of children, so it can never drift from them — and it records the digest
   of each child it was built from, so master-vs-child staleness is mechanically
   detectable. Master assembly is mechanical (ordering, deduplicating, and
   stitching child sections); child sections appear in the ticket's branch
   order — recency of development, or priority when assigned (§3.1). Agent
   writing happens in the children, not the master, so the master inherits —
   never re-invents — the children's claims. Against bloat, the master leads
   with a **digest**: each child's State section, selected verbatim — a reader
   gets the whole ticket at a glance before the full components below.
   Selection is mechanical; the digest never rewrites a claim.
3. **The report cannot live inside the session.** The session file is an
   append-only conversation record; the report is a living document with its own
   update lifecycle (children rewritten as branches move, master regenerated on
   every child change) and its own audience (remote, authorized users). So
   reports sit beside the session in the bunker, and only `reports/` syncs
   remotely.
4. **Components live on their branches; checkout collects them.** Because
   `.tb/` is committed with the repo, each branch's report component is
   committed *on that branch* — no single checkout sees them all. `tb
   checkout <TICKET>` is the collection step: fetch, read each related
   branch's component out of git (`git show <branch>:.tb/...`), and re-admit
   every one through the report intake gate (transport is mechanical, but
   admission is still typed and linted — a component that fails today's gate
   is reported and skipped, never smuggled in), then recompile the master.

**Report component schema** — each child (and the master, per-branch-sectioned)
delivers who / what / where / why. Each section has a defined **determinant**:
the data source that decides its content.

| Section | Determined by | Content | Stratum |
|---|---|---|---|
| Who | **Users.** Assignee, reporter, people of interest from ticket config; the session's user from session config. Manually configured for now; tracker API compatibility after manual is solid. Git authors supplement | Direct ticket people first, then git authors from the branches | config + observation |
| What | **Git diffs and actual code** — not just which region changed but the changed code itself. The diff stage captures patch text as an observation so What can quote and describe real changes | Summarized diff: files, hunks, additions/deletions, work vs. merge churn, with the actual changes characterized | observation |
| Where | **Other ticket work, git diffs, ticket information, and a little agent speculation** — in that order of authority | Three blocks, in epistemic order. **(1) Edits that already happened** — from the git diffs and prior components of this ticket, with emphasis: the ground truth of where the ticket already lives. **(2) Located information directly relating to the ticket** — symbols/paths from the ticket found in the baseline and what ties into them. **(3) Informed speculation** on where the user may need to make changes to solve the ticket, ranked by grounding; if nothing concrete, "tag up with reporter/person of interest" | observation → real → speculation |
| Why | **The ticket itself, primary sources** — then the agent | **Primary sources first**: the ticket text and other primary material at the top. At the bottom, the agent's speculation — a composition of real findings and speculation, clearly tagged | ticket + real + speculation |
| State | **Merge history + authorship observations** — then the agent | Branch freshness vs main, merged-back or not, apparent progress | observation + speculation |

### 3.5 Report-first UX

**Users consume the report, not the session.** Ideally a user never interacts
with the agent at all:

- The session is created and driven headlessly (branch creation triggers it; the
  bootstrap extensions and command-fed agent do the rest).
- The report is the sole user-facing artifact — it must be complete enough that
  reading it answers who/what/where/why without a follow-up conversation.
- The pi session file is retained as **provenance**: if a report claim needs
  auditing, the session shows exactly which commands produced which observations
  and how each inference was derived. It is an audit log, not a chat to resume.
- If interactive follow-up is ever needed, that's a signal the report schema is
  missing a section — fix the schema, don't normalize talking to the agent.

**The ticket surface mirrors a tracker page, read-only and honest about
provenance.** `tb list` shows every ticket grouped by declared status and
ordered by priority then recency; `tb show <TICKET>` is the Jira-page
equivalent — fields, carried-over comments, a development panel rendered
from the observations, report state, and the ledger tail as the activity
log. Declared workflow state (status, ours) is always shown *beside*
observed git state (merged-back, recency — the pipeline's), never blended.
`tb view` serves the same data as a minimal local viewer
(`ticketbunker/viewer.py`, stdlib HTTP on 127.0.0.1): a ticket list and
per-ticket pages with the master digest inline, full components collapsed,
and trust tags rendered as chips. It is strictly read-only — no write
endpoint exists, every page renders from `.tb/` on each request, and pages
poll a database fingerprint so whatever the user is looking at reloads
when the data changes. All content is HTML-escaped before the constrained
report markup is re-introduced; agent text can never inject into the page.

### 3.6 Report analysis toolset

Because reports are agent-written and long-lived, they will decay without
mechanical pressure: sections bloat, children duplicate each other, the master
drifts from reality. We therefore need a **procedure/toolset for analyzing
existing reports and keeping them as straightforward as possible** — a report
linter + auditor, mechanical wherever possible, in the same spirit as the
bootstrap pipeline.

**Anchor verification closes the formatted-vs-checked gap.** At every
publish (and at checkout import), each [real] claim's anchors are resolved
against the source repo — the branch itself, then the exact baseline state
(merge-base) the observations were computed from — and against the bunker's
own files for observation anchors. A cited line number must fall inside the
file. A claim none of whose anchors resolve is rejected at the gate, and an
accepted component is stamped (`tb:verified branch/commit/ts`, mechanical,
never agent-authored) with the ref it was proven against; the master's
manifest and digest carry that ref, and `tb show`/the viewer badge any
component whose branch has developed past its verification instant. This
proves existence, not meaning — a resolvable anchor can still be cited
dishonestly; that residue is what report grading is for.

v1 checks, in rough priority order:

| Check | Mechanical? | When | On failure |
|---|---|---|---|
| **Schema** — required who/what/where/why/state sections present; every claim carries a stratum label; every inference cites ≥1 observation | yes | gate: before a child is accepted into `reports/branches/` | block the write; agent must rewrite |
| **Staleness** — child's recorded commit SHA vs branch head; master's recorded child digests vs actual children | yes | on branch push events and on read | mark stale prominently; trigger refresh |
| **Dead references** — paths/symbols named in the report that no longer exist on the branch or baseline | yes | on master regeneration | strike through + warn |
| **Duplication** — same observation restated across children (e.g. shared merge-base facts); child content re-narrated in the master | yes (digest/similarity) | on master regeneration | hoist shared facts to the master; children keep references |
| **Length budget** — per-section size ceilings; a report that outgrows them is summarizing badly | yes | gate, same as schema | block; agent must compress |
| **Contradiction** — master claims vs child claims, or two children disagreeing about shared state | agent-assisted | on master regeneration | flag for the owning session's next refresh |

The gate checks (schema, length) run *before* a child is published, so a
malformed report never reaches remote storage. The regeneration checks
(dead refs, duplication, contradiction) run every time the master rebuilds,
which is every time a child changes — so the whole tree is re-audited at the
same cadence it mutates. Only the contradiction check needs an agent; everything
else is deterministic, which keeps the audit itself trustworthy.

### 3.7 Stage contracts

Every stage or process the agent goes through — bootstrap steps, enrichment
steps, report writing, lint gates, master regeneration — is defined by an
explicit, modular **stage contract**:

| Field | Meaning |
|---|---|
| **Purpose** | why this stage exists — the question it answers |
| **Input** | exactly what it consumes (prior stage outputs, ticket context, repo state) |
| **Process** | the commands/operations it performs |
| **Desired outcome** | what a successful run produces, in checkable terms |

All four fields are explicit and written down per stage; no stage's behavior is
implied by prompting alone. This is a large part of why pi extensions fit: each
stage maps to one extension (or one command a stage exposes), so the contract
boundary and the code boundary are the same boundary. Stages are modular —
addable, removable, and reorderable at the contract level — and §3.2's pipeline
is simply the ordered list of bootstrap stage contracts.

### 3.8 Execution ledger & stage markers

The diagnosability requirement, concretely: **every executed command is logged,
and every stage is bracketed by markers** — and neither is the agent's job.

- **Every command is logged**: the automated bootstrap commands, every command
  the agent invokes during enrichment, the lint gates, master regeneration —
  all of it. A ledger entry records: stage, command, arguments, initiator
  (harness-automatic vs. agent-invoked), repo state (commit SHAs), timestamp,
  exit status, and an output digest.
- **Every stage emits markers**: a stage-start and stage-end marker referencing
  its contract (§3.7), so the ledger reads as bracketed, ordered stages rather
  than a flat command stream. A failed or skipped stage is visible as an
  unclosed or absent bracket — diagnosis is reading the ledger, not reproducing
  the run.
- **Instrumentation is structural, not behavioral.** The agent does not call
  the logging flags and cannot be trusted to — markers and ledger entries are
  emitted **automatically by each function/hook**: the pi extension wrappers
  that execute commands do the logging as a side effect of execution. An
  unlogged command is impossible by construction, not discouraged by prompt.
- **The ledger lives in the TicketBunker backend**, written through its API —
  not in the pi session. The session file remains useful color (the agent's
  reasoning), but the ledger is the authoritative record of what actually ran.
  This also means audit does not depend on collecting session files off
  individual machines.

### 3.9 Session context composition (tb.md)

The bootstrap context handed to a session is a **composite markdown document,
`tb.md`**, assembled from separate parts:

1. **Explanation** — the traditional harness preamble: the agent's purpose,
   its rules, the ontology (§3.3), how it is to write reports, the order to
   use tools in, and **the purpose of the master report that came before
   it** — what the aggregate is for and how this session's component feeds
   it. (This doc is deliberately placed before any master content the
   session might see.)
2. **Process workflow** — the stage discipline: what stages will run/have run,
   what the agent is expected to produce, in what order.
3. **The local universe** — the project's tbroot.md, mirrored in (§3.10).
4. **Ticket information** — essentially all of the ticket: title, priority,
   description, people. Markdown by default; can be carried as XML where
   that makes injection easier. This is *information we add* — kept as its
   own fragment, never blended into generated content.
5. **Observations** — the latest bootstrap output, as its own fragment.

Fragments 1–2 and 4 are *ours*; 3 is agent-written but gated (§3.10); 5 is
pipeline output. The composition manifest records each fragment's provenance
(§4) — the agent reads one document, the storage stays partitioned.

**The report is a form.** A report component doesn't start empty: the
pipeline **pre-fills** it — Who from ticket config and authorship, What from
the diffs, State from merge history, all as `[observation]` lines — and the
agent **fills in the agent-specific areas**: the `[real]` finds, the ranked
`[speculation]`, the Why synthesis. The form's structure is ours; the
observations are the pipeline's; only the fill is the agent's. This keeps the
provenance separation intact inside the report itself, and it shrinks the
surface where an agent can wander: filling a labeled form beats authoring a
document from scratch.

**Each part is a separate file** under `context/`, and the composed `tb.md`
sits at the **root of the output dir**, so a user can open one file and see
exactly what is being prepended or appended to a session — no hidden prompt.
Composition rules:

- **Deterministic and mechanical:** fragments are concatenated in filename
  order (numeric prefixes); each fragment is bracketed in `tb.md` by markers
  carrying its filename and content digest, so the composed document is
  auditable back to its parts.
- **Modular:** adding, removing, or modifying context = editing fragment files
  and recomposing. Static fragments (explanation, workflow) are edited by
  humans; generated fragments (current information) are rewritten by the
  pipeline and never hand-edited.
- The harness adapter's only context job is "prepend `tb.md` to the session" —
  which keeps context assembly harness-neutral and out of the agent's control.

### 3.10 The root surveyor and tbroot.md

Ticket agents know their ticket; something has to teach them the **project**.
That is the job of a **special agent that lives at the root folder level** —
per project, above all ticket bunkers.

**tbroot.md** sits in the project of interest and is one of the preamble
files composed into every spawned agent's context (§3.9) — the part of the
composition that explains their local universe. The surveyor:

- looks for an existing `tbroot.md` first; if present, it updates rather than
  rewrites;
- peruses the project and records what a newcomer must know: "mainly C++,
  built with Maven, N components — here's a one-liner on each; adapters live
  here, builders here"; architecture, layout, coding standards *as observed*;
- keeps it **concise but proportional to project size** — not a huge doc.
  Concise reasoning about grounded features of the codebase is fine as long
  as it doesn't compromise capacity;
- **updates it as more information becomes available** — e.g. when a build
  process is learned, it gets recorded. tbroot.md is living, not
  write-once.

**The hard rule: real observations only.** Information may not be clear, and
that is acceptable — a gap stays a gap. Agent conjecture is not allowed in
this file *even when it is seemingly certain conjecture*. tbroot.md is
consumed as ground truth by every future agent in the project; a speculation
written here would be laundered into an observation the moment the next
session reads it. Anything the surveyor cannot anchor to something checkable
(a path, a build file, a command that was run) does not go in. The
speculative version of project knowledge belongs in ticket-level reports,
tagged, not here.

Mechanical enforcement (same spirit as the report gate): tbroot.md is linted
for anchors — claims must reference checkable things — and for conjecture
language ("probably", "seems", "likely", ...), which is rejected. The lint
cannot prove a claim true, but it can refuse the *form* that conjecture
takes.

### 3.11 Per-session configuration

Each ticket has a **config for each specific session** — one per branch
session, under `sessions/<branch_slug>.json`. Its job is **injecting user
data into setup**, for two consumers:

- **the agent**: the config becomes a fragment in that session's tb.md
  composition (provenance: user), so session-specific facts — the branch's
  user, a baseline override, extra people, free-form notes — reach the agent
  through the same audited composition path as everything else;
- **our backend**: the same config is what the backend stores/receives for
  that session, keeping session-scoped user data addressable per session
  rather than smeared across the ticket.

Ticket-wide data stays in the ticket configuration; session config carries
only what is specific to that session. Both are *information we add* (§4) —
they never mix with observations or agent output at rest.

**The master (global) report has no config, by design.** Everything the
master needs it can grab readily from its children — their configs, their
components, their digests. Giving the master its own config would create a
second source of truth to drift; deriving from children keeps regeneration a
pure function of the child set.

### 3.12 Typed information intake

Agents (and users) don't hand the system free-form data: **every piece of
information returned into the system carries a specific type**, from a fixed
registry — ticket, session config, inference, report component, tbroot,
observation — and the agent is taught the types and how to use each one (the
type list and its commands are part of the preamble).

**Nothing enters unlabeled, nothing enters unvalidated.** Every intake path
runs through a handler that (a) requires a declared type and (b) validates
the payload against that type's rules before anything is written — locally
today, via our backend as the authority when it exists. A payload that fails
validation is rejected at the door and the rejection is ledgered; there is no
side channel that writes without the type check. This is the same
by-construction philosophy as the ledger: validation is structural, not a
convention the agent is asked to follow.

### 3.13 Code architecture

The repo separates cleanly by role, **per information type**:

- **types/** — the registry: each information type's definition and
  validation rules. The single authority on what may enter.
- **handlers/** — intake: receive a typed payload, validate it against the
  registry, write it to its storage location, ledger the acceptance or
  rejection. One intake path per type; no writes from anywhere else.
- **builders/** — construct artifacts from stored information: tb.md
  composition, the pre-filled report form, the master report, fragments.
- **processors/** — transform and derive: the bootstrap stages (git facts →
  observations), the lint gates.
- **adapters/** — boundaries with the outside: git today; the harness (pi)
  and the backend API next. Nothing outside adapters/ touches an external
  system.

**The bunker is the instance directory** — easily navigable the way `.git`
is: a fixed, well-known layout (context/, sessions/, observations/,
inferences/, reports/, ledger.jsonl, tb.md) defined in one place
(`layout.py`) and stable across every bunker. Knowing one bunker means
knowing them all; tooling and humans can both address any piece of instance
state by path without asking.

**The root is git-relative.** `tb init` runs inside a git repository — and
fails outside one, exactly like git itself — creating `.tb/` at the repo
root. Ticket bunkers live under `.tb/`, every command resolves bare ticket
IDs by walking up to the nearest `.tb/` (git's own discovery rule), and the
bunker database travels with the repo: committed alongside the code, same
remote, same access boundary — which is precisely the §6 auth model
(report access requires repo access) enforced by location. A nested
`.tb/.gitignore` keeps session-local material (form drafts, harness session
files, patches) out of the shared history. A standalone external database
(tb ticket --root, tb sync) remains supported for teams that want bunkers
in a separate repo.

**The bunker merges the way the repo merges.** Branches carrying `.tb/`
changes come together through ordinary git, with the merge behavior the
data shapes demand, declared in a generated `.tb/.gitattributes`:
append-only logs (`ledger.jsonl`, `inferences.jsonl`) merge by **union** —
every side's entries survive, order within the merge is immaterial for
append-only records; the **master is never merged**, it is a generated
artifact — `merge=ours` keeps a placeholder and the post-merge hook
regenerates it from the actual children. `tb hooks --install` writes
`post-commit`/`post-merge` hooks that run `tb refresh` — resolve the
current branch's ticket, re-run the observation pipeline, recompose,
regenerate the master — never blocking git (a branch without a ticket, or
any failure, is a silent no-op). This is the first slice of the §3.1
trigger model: the viewer polls the database, the hooks keep the database
current, so what a user is looking at tracks the repo as they work.
Ticket.json edits from two branches still conflict like source code — by
design, they are declared data. **A merge session is future work in the
same trust frame**: merging is part of solving a ticket, so semantic
conflicts (two branches' components disagreeing about the same code) are
a legitimate agent task — a promoted session that reconciles components
through the same gates, never a silent auto-resolution.

### 3.14 Naming conventions and branch creation

**Tickets and branches follow an allowed pattern.**

- **Ticket IDs** match a configured pattern — by default `PROJECT-NUMBER`,
  e.g. `TB-1267` is TicketBunker's ticket 1267. Enforced at ticket intake:
  an ID that doesn't match the pattern is rejected.
- **Branches** follow `username/TICKET-name` — e.g. user Tim working
  TB-1267 gets `Tim/TB-1267-fourier-alg-impl`. The ticket ID embedded in
  the branch name is what makes branch resolution (§3.2) deterministic
  instead of fuzzy.

**The workflow:** global config for the user, once → select a ticket →
input just the short branch name → the system **fills in the rest and
creates the branch in a relatively normal git fashion** (a ledgered
`git branch`, nothing exotic). Nobody hand-types the convention, so nobody
gets it wrong — the pattern is enforced by construction at the same place
the branch is born.

**The ticket is the deliberate step, so creating one must not hurt.** A
ticket mirrors the useful core of a tracker issue — id, title, description,
type (bug/task/story), status (todo/in-progress/in-review/done), priority,
labels, assignee/reporter/people-of-interest, linked tickets, plus
mechanical created/updated stamps — without rebuilding the tracker: sprints,
versions, estimates and votes stay the tracker's job. **Comments are carried
over, not authored**: `tb comment` pastes a real tracker comment onto the
ticket verbatim, keeping its original author (and optionally timestamp and
source URL) — ours-provenance imported context that composition feeds to the
agent as primary source material for Why. Creation has three ways in, one
gate: field flags for scripting, `--from FILE` for a saved template, and —
the default when no fields are given — `$EDITOR` on a pre-filled template,
like `git commit`; a draft that fails validation is saved and the error says
how to resume it. After creation, fields move only through `tb set`
(status transitions, priority, labels, …) — the same ticket gate, so there
is never a hand-edited side channel into ticket.json.

**Association is automatic by default, explicit when needed.** A ticket's
branch-match key defaults to the ticket ID itself (`--key` overrides it for
teams whose convention uses a different token), so any branch carrying the
ID associates automatically — and `tb branch`-created branches carry it by
construction. For a branch that predates the convention or won't be
renamed, `tb branch <TICKET> --assign BRANCH` records an explicit
assignment on the ticket itself (`assigned_branches` in ticket.json — ours
provenance, travels with the repo, re-entering through the ticket intake
gate). Branch resolution (§3.2) unions both sources and labels each
branch's match rule; `tb run` on an assigned branch resolves its ticket
through the assignment when the branch name carries no ID.

**User identity:** the one hard requirement is that **each user has an
identifier that means something to the team**. To avoid configuration time
we default it from git's own global config (`user.name` — required by git
anyway, so it always exists), sanitized for branch-name use; at init the
user is offered the chance to change it, and a TicketBunker-level config
overrides git's when set.

**This is not a new constraint — it's configuration of an existing one.**
Large projects already tag tickets and branches like this. Making the
pattern *configurable* (per team/admin) is the agile-program-agnostic move:
whatever convention a Jira/GitLab/tracker shop already uses, admins encode
it once and TicketBunker speaks it, rather than TicketBunker imposing its
own.

### 3.15 The project universe boundary

**A ticket is restricted to the global project** — but more often than not
it impacts multiple parts of that project, and that's fine: within the
project universe the agent operates **focused and automated**, following
the ticket wherever it leads across the project's directories.

**An agent and its session are project-specific.** An agent working one
project cannot venture out into another project's code. If a ticket's
trail leads outside the universe, the agent's only recourse is **another
agent's report** — if one is available for the referenced ticket/project,
it may be consumed (as tagged, provenance-carrying input); if not, the
agent is out of luck, and says so (the tag-up recommendation applies: name
the person, not the guess). Reports are thus the interface *between*
project universes for agents, exactly as they are the interface between
the system and humans — nothing crosses a project boundary except a
report.

**Cross-project tickets link by reference.** When work genuinely spans
projects, the tickets are linked via references — a ticket carries the IDs
of its counterparts in other projects (validated against the ticket
pattern). The reference is a pointer to another bunker's reports, never a
license to read another project's code.

### 3.16 Format doctrine: JSON and markdown

Both formats, with defined jobs — neither is a fallback for the other:

- **JSON is for certainty**: export and import of information within and
  between our own systems — observations, configs, the ledger, the
  inference store, anything the framework must retrieve, validate, or
  transfer mechanically. If a machine consumes it, it's JSON, and it goes
  through typed intake.
- **Markdown is for agnosticism**: everything meant to interface with
  agents and humans in a general, convenient way, and to stay useful
  outside our framework — reports, tb.md, tbroot.md, the fragments. A
  TicketBunker report should be directly readable and relevant to any
  project or use case with no TicketBunker tooling in sight.

The split already matches the layout: everything under `observations/`,
`sessions/`, `inferences/`, `ticket.json`, `ledger.jsonl` is JSON;
everything a person or agent reads whole — `tb.md`, `reports/`,
`tbroot.md`, `context/` — is markdown.

## 4. Ordering & atomicity principles

- **Observations before inference, always.** Enforced by the harness, not by
  prompting.
- **Observations are atomic**: each one is a single mechanical fact (one diff, one
  merge-base, one authorship entry). They can be cached, replayed, and audited.
- **Inferences are less atomic by nature** — they aggregate observations — but
  each inference should cite the observations it rests on.
- **Three tags, one ladder:** `observation` (pipeline-produced mechanical fact)
  → `real` (agent-located, verifiable concrete fact: a path, a line, a symbol)
  → `speculation` (interpretation, ranked by grounding). A claim's tag can only
  be upgraded by verification — never by confidence, repetition, or promotion
  of the agent's effort level.
- **Data provenance separation.** Three data types, by origin:
  1. **information we add** — ticket data, people, configuration, the static
     preamble fragments;
  2. **observations** — pipeline-produced mechanical facts;
  3. **information the agent adds** — inferences, report prose, tbroot
     updates.
  The agent sees all three composed together; **our storage never
  intermingles them**. Each type lives in its own location (`ticket.json` +
  static fragments / `observations/` / `inferences/` + `reports/`), each
  fragment and store entry carries its provenance, and composition happens
  only at read time (tb.md, the report). The backend database keeps the same
  partition. Mixing is a presentation step, never a storage step.

## 5. Interfaces

The TicketBunker backend is **a git repository of bunkers** (§2) — pi
extensions and the CLI are clients of it, never owners of state:

- **Ingest:** ticket context (manual paste or future tracker webhook), remote
  branch events.
- **Ledger:** stage markers and per-command log entries, written automatically
  by the executing hooks (§3.8). JSONL ledgers are merged with git's union
  strategy so concurrent appenders don't conflict.
- **Publish:** child report components (through the typed intake) and
  regenerated masters.
- **Sync:** `tb sync` mirrors git — pull, add what `.tbignore` admits,
  commit, push. The `.tbignore` is customizable per team; the default keeps
  harness session files and regenerable bulk (diff patches) local.
- **Query:** by ticket ID — clone/pull, then read the bunker by its fixed
  layout. Latest report, raw observations, ledger — all addressable by path.
- **Emit:** structured report (markdown + machine-readable JSON alongside).

## 6. Authorization

- Session data is served only to authorized users. Authorization is scoped at
  least per-repo (a user who can't read the repo can't read reports derived from
  it — the report is a derivative of the code).
- Open question: whether to support a *redacted report tier* for the
  "data-consumer" persona (someone allowed to know ticket state but not code
  content). This is exactly the cross-boundary use case, and also the riskiest —
  a summary of classified code may itself be classified. Default answer for v1:
  **no redacted tier; access to a report requires access to the repo.**

## 7. Known drawbacks & mitigations

| Drawback | Mitigation |
|---|---|
| Agile info must be manually re-entered into the session | Accept for v1; later, tracker webhook/API ingest where the boundary allows. Auto-ingest of new remote branches by ticket key already reduces re-entry. |
| Branch-per-ticket naming is a convention, not guaranteed | Fuzzy matching + explicit manual branch association as fallback; report "no branches found" honestly rather than guessing. |
| Reports go stale as branches move | Sessions refresh on branch push events; reports are timestamped against a commit SHA so staleness is visible. |

## 8. Non-goals (v1)

- Not a Jira replacement or plugin; it's a sidecar.
- No write-back to git.
- No cross-repo inference beyond the repos explicitly attached to a session.

## 9. Resolved decisions

- **Report storage:** the report is the remote artifact; the pi session stays
  local (remote session storage is an optional optimization if cheap). A report
  never requires waking a session to read.
- **Session/report layout:** `<ticket_id>_bunker/` containing `tb.md` +
  `context/` + `observations/` + `ledger.jsonl` + `reports/`; bunker name =
  unique ticket ID. The harness persists its own session data in its own
  storage — the bunker stores only what TicketBunker owns.
- **Session context:** delivered as `tb.md`, composed deterministically from
  separate fragments (explanation / workflow / current information) so context
  is modular and a user can see exactly what a session is fed (§3.9).
- **Report structure:** the report is agent-authored and lives *outside* the
  session file. One component per branch, owned by the session; a master is
  fully regenerated (never patched) whenever a child changes, recording child
  digests for staleness detection. Only `reports/` syncs remotely.
- **Cardinality:** a logged ticket requires ≥1 branch and may have many;
  branches (and master sections) are ordered by recency of development, or by
  priority when assigned.
- **Report hygiene:** a mechanical lint gate (schema, stratum labels, length)
  before a child publishes, plus regeneration-time audits (staleness, dead
  references, duplication, contradiction) — §3.6.
- **UX:** report-first, agent-invisible (§3.5).
- **Ontology:** three tags — observation / real / speculation — with
  speculation ranked by grounding (grounded / adjacent / ungrounded); finding
  nothing is a reportable result that recommends tagging up with the reporter
  or a person of interest. Agent working inferences persist in `inferences/`,
  tagged, as agent-facing raw material for the final report.
- **Who:** assignee, reporter, and user-added people of interest are manual
  ticket configuration for now; tracker API compatibility comes after the
  manual flow is solid.
- **Root surveyor:** a per-project agent maintains `tbroot.md` — the
  local-universe preamble composed into every session. Observations only, no
  conjecture however certain; concise but proportional to project size;
  updated as information (e.g. build processes) becomes available (§3.10).
- **Per-session config:** one config per branch session for injecting user
  data into setup (agent + backend). The master report has no config —
  it derives everything from its children (§3.11).
- **Harness fit:** pi's extension model + single-file sessions + headless modes
  (`pi -p` / RPC / SDK) confirm the architecture; ordering semantics are
  documented/visualized on pi.dev.
- **Ownership:** pi does not own the system — it is a client harness of the
  TicketBunker backend API, which holds all durable state (ticket registry,
  reports, execution ledger). Pi is replaceable without data loss.
- **Diagnosability:** every command (automated or agent-invoked) is ledgered
  and every stage bracketed by markers, emitted structurally by the executing
  hooks — never by the agent's own volition (§3.8).
- **Stage contracts:** every stage declares purpose / input / process / desired
  outcome, explicitly and modularly, one pi extension per stage (§3.7).
- **Harness portability:** pi's scope is session ownership only (feed the
  agent, manage sessions, allow repo exploration). All pipeline functionality
  is harness-neutral; pi extensions are a thin adapter layer, the only part
  another harness would need reimplemented.

## 10. Open questions

1. ~~How ticket keys are matched in branch names~~ — resolved: tickets and
   branches follow configurable allowed patterns (`PROJECT-NUMBER`,
   `username/TICKET-name`), and branches are created *by* the system, so the
   convention holds by construction (§3.14).
2. ~~Multi-directory tickets~~ — resolved: a ticket operates freely across
   directories *within* its project universe; projects are the hard boundary,
   crossed only by reports, with cross-project tickets linked by reference
   (§3.15).
3. ~~Report sync transport~~ — resolved: the backend is a git repo; `tb sync`
   moves the bunker database through ordinary git with `.tbignore` filtering
   (§2, §5). Works anywhere git works, including bundle transfer across
   air gaps.
4. ~~File format of components~~ — resolved by the format doctrine: JSON for
   certain machine export/import, markdown for framework-agnostic agent- and
   human-facing content; reports are markdown, their machine interchange (when
   needed) is a JSON export, not a second source of truth (§3.16).
5. Concurrency on the master: if two users' sessions publish child updates for
   different branches at nearly the same time, who regenerates the master?
   Simplest answer: regeneration is idempotent from the child set, so
   last-writer-wins is safe *as long as* the regenerator always pulls the
   current children first — worth stating as an invariant when the sync
   transport is chosen.
