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
remote git repository and is transferred regularly through git**. By default
this is a dedicated companion repository; the source repository commits only
the locator that names it. TicketBunker
functionally mirrors git rather than standing up a whole new service:
clone/pull/push are the sync protocol, history is the audit trail, remotes are
the access model, and the companion data branch's **`.gitignore`** controls
what enters shared history (harness session files and other local-only material
stay local). Anywhere git already works — including air-gapped enclaves that move
git bundles across boundaries — the TicketBunker backend already works. A
thin API service can be layered on top later if a team wants one; it is an
optimization, never the foundation.

## 3. Architecture

### 3.1 Ticket bunkers, sessions, and storage

**Cardinality model:**

- Each **branch** has a **user** (the owner/author of that work).
- A ticket may exist before work is claimed and therefore may have **no
  branch yet**. Once work begins it may have **multiple branches** over its
  life. Branches within a
  ticket are **ordered by recency of development**, or by **priority when one
  is assigned**; this ordering is also the section order of the master report
  (§3.4).
- A ticket may span **multiple directories**.
- Each ticket has a **unique ID**, and that ID names its **ticket bunker**.
  Uniqueness is a hard requirement — the bunker directory *is* the ticket's
  identity in the system, so collisions are unacceptable.

**Default on-disk layout — source locator plus companion checkouts:**

```
source-repo/
├── .ticketbunker.json         # committed project + companion locator
└── .tb/                       # ignored local companion checkouts/cache
    ├── config/                # ticketbunker/config branch checkout
    └── data/                  # ticketbunker/data branch checkout
        └── projects/<PROJECT>/tickets/<TICKET-ID>/
            ├── tb.md          # composed session context (§3.9)
            ├── context/       # provenance-separated input fragments
            ├── sessions/      # per-branch session configuration
            ├── observations/  # atomic deterministic git facts
            ├── inferences/    # tagged agent working inferences
            ├── ledger.jsonl   # typed intake and execution history
            └── reports/
                ├── master.md
                └── branches/<branch_slug>.md
```

The canonical companion has two independent histories. `ticketbunker/config`
holds global field sets and each project's composition/provider mappings;
`ticketbunker/data` holds ticket bunkers. The default branch names are
configurable in the committed locator. Legacy embedded bunkers named
`<ticket_id>_bunker` remain readable and have an explicit, backed-up migration
path, but they are not the default for new projects.

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
   work, not noise from an aging fork point. The repository-owned `.tb/`
   database is pathspec-excluded from code diffs and patches so reports never
   recursively describe TicketBunker's own generated artifacts.
4. **Merge history.** When the branch last merged main (or was rebased), how far
   its code-affecting history is ahead/behind, and whether its non-merge code
   commits have been merged back. Database-only `.tb/` commits are excluded so
   observing and committing a bunker cannot change its own development state.
5. **Work separation.** Partition commits that touch project paths outside
   `.tb/` into *author work* vs *incidental merges/main churn*, so database-only
   commits do not become code authorship while the report still reflects what
   the person actually did.
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
   with a **digest**: each child's Reader brief when present, followed by its
   State section, all selected verbatim. Legacy children without a brief fall
   back to State. A reader gets the whole ticket at a glance before the full
   components below. Selection is mechanical; the digest never rewrites a
   claim.
3. **The report cannot live inside the session.** The session file is an
   append-only conversation record; the report is a living document with its own
   update lifecycle (children rewritten as branches move, master regenerated on
   every child change) and its own audience (remote, authorized users). So
   reports sit beside the session in the bunker, and only `reports/` syncs
   remotely.
4. **Components live together under the ticket, keyed by source branch.** A
   successful publish places the gated component on the companion data branch
   immediately. Every checkout can therefore see the complete child set after
   `tb setup`; `tb checkout <TICKET>` refreshes the companion checkout and
   recompiles the master. Legacy embedded mode retains its branch-collection
   behavior for backward compatibility.

**Report component schema** — each new child leads with a compact Reader brief,
then carries the complete who / what / where / why / state evidence trail. Each
section has a defined **determinant**: the data source that decides its content.
The brief is optional only when admitting a legacy component published before
this schema; every newly scaffolded session must fill it.

| Section | Determined by | Content | Stratum |
|---|---|---|---|
| Reader brief | **The agent, bounded by the evidence below.** Ticket fields, git observations, and verified finds determine what may be emphasized | `### Who`, `### What`, `### Where`, `### Why`: one or two short claims for Who, What, and Why in descending apparent importance; one short Where claim may name up to three paths in importance order. It makes the evidence readable; it does not summarize every observation. | agent-authored [real] / ranked [speculation]; never [observation] |
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
- The harness transcript/session is retained as **provenance**: if a report claim
  needs auditing, it shows exactly which commands produced which observations
  and how each inference was derived. The accepted component is also stamped by
  orchestration with every generator fact it actually knows — harness, provider,
  and model — rather than asking the agent to identify itself. Work authorship
  and report generation are separate facts. The session is an audit log, not a
  chat to resume.
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
(`ticketbunker/viewer.py`, stdlib HTTP on 127.0.0.1). **Tickets is the landing
surface.** It defaults to tickets assigned to the current Git identity, groups
them by declared status, and includes backlog/open/to-do/in-progress/in-review
while done and closed stay out of the way until selected. One persistent,
provider-neutral filter bar searches ticket key, title, description, project,
people, and labels; status, assignee, reporter, label, and project are explicit
facets. Filters and the current page live in the URL. Results are server-side
pages of twenty lightweight ticket references, so the browser receives only the
requested summaries and never composes every ticket/report to build a list.
Each project commits those bounded references at
`projects/<PROJECT>/tickets/index.json`. The index contains human-facing ticket
summary fields, small report/branch counters, the canonical relative reference,
source URL, and a digest of `ticket.json`; it never contains report bodies. It is
generated from typed ticket mutations as a publication side effect, not another
write authority.

**Projects is the companion catalog surface.** It lists projects known to the
companion config/data branches; selecting one applies the same search and
facets within that project. A project row and a ticket result are references to
canonical companion data, not a claim operation and not a copy of bunker
contents into the source repository. Opening a result is the deliberate point
where the full ticket snapshot is composed. Standalone and legacy databases
present their one inferred project through the same query contract.

Per-ticket pages remain organized around completing one ticket. The singular page has
an explicit **Ticket / Agent** contract. Ticket is the default Jira-like issue
surface. The header names the source project before the ticket, then presents
the mapped status as the sole lifecycle ground truth beside labels and the
source's updated time. Description and optional acceptance criteria read as
constrained rich text. **Activity is mirrored and read-only**: legacy comments
plus provider-neutral `activity` entries (`comment`, `history`, `worklog`, `log`,
`note`, or an integration-defined kind) retain author, text, time, and source;
the viewer has no authoring control.

One **Details** section follows the reading flow. Base ticket fields and useful
custom/source fields stay in that list. Transport IDs, provider flags, and raw
source state move into a native Metadata disclosure; duplicated source titles,
timestamps already promoted into the header/details, mapped status fields, and
project ordinals are omitted. Classification is semantic (project, timestamp,
status, ID, URL), never a GitHub/Jira/GitLab-specific view branch. Nested provider
metadata is flattened into readable field paths instead of exposed as a JSON
blob. Agent owns Agent work and never mixes those fields into its report surface;
declared lifecycle and the persistent Branches rail appear there beside the
observed work, not on the ticket's tracker surface.

Agent work defaults to **Master** and provides one native report selector at the
right of its heading: Master followed by each branch report. Master presents each
category's concise Reader brief contributions in development-recency order, with
the branch named and a visible rule between branch contributions; each category
immediately follows its synthesis with a native disclosure for the unchanged
evidence, likewise separated and grouped by child. Selecting a branch narrows all
Who/What/Where/Why synthesis and evidence to that component. Direct links in the
persistent Branches rail activate Agent and select the corresponding report.
State remains a separate collapsed report trail, because tracker lifecycle and
observed branch state are already visible in the rail. Legacy children derive
compact previews from their evidence without inventing synthesis. Verification
and freshness remain visible. Each child trail labels work authors separately
from the harness/provider/model that generated the report; a selected branch also
shows its known generator beside its branch identity. Unknown generator fields
stay absent instead of being guessed. Trust tags render as
chips. It is strictly
read-only — no write
endpoint exists, every page renders from `.tb/` on each request, and pages
poll a database fingerprint so whatever the user is looking at reloads
when the data changes. All content is HTML-escaped before the constrained
report markup is re-introduced; agent text can never inject into the page.
Localhost-only is a decision, not a limitation: there is deliberately no
bind option and no auth surface — remote viewing (phone, another machine)
is SSH port-forwarding's job, which already carries the repo's own access
control. Do not add network exposure to the viewer.

**There is one Python read model.** `ticketbunker/api.py` exposes the public,
read-only `TicketCatalog`, `TicketDatabase`, query/page types, and typed
ticket/branch/report snapshots. `TicketCatalog` reads lightweight summaries
across projects, applies the viewer's provider-neutral filters, and returns a
bounded page without reading report bodies. `TicketDatabase` composes one
opened ticket's fields, deterministic branch observations, report ownership,
verification/freshness, the master digest, and activity straight from layout-
defined bunker paths on every query; it holds no cache or second database.
Both the viewer and future non-CLI integrations consume this boundary. Writes
remain exclusively in the typed, validated, ledgered handlers (§3.12–3.13).
Ticket intake accepts optional `acceptance_criteria` (a list of non-empty
strings) and `metadata` (an extensible mapping) so tracker-shaped information
can travel with the repository without turning Jira or another importer into
the authority.

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
accepted component is stamped (`tb:verified branch/commit/ts` plus known
`harness/provider/model`, mechanical, never agent-authored) with the ref it was
proven against and its generator identity; the master's
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
   description, people, acceptance criteria, carried-over comment sources, and
   the source/extension metadata mapping. Markdown by default, with metadata as
   deterministic JSON inside the fragment. This is *information we add* — kept
   as its own fragment, never blended into generated content.
5. **Observations** — the latest bootstrap output, as its own fragment.

Fragments 1–2 and 4 are *ours*; 3 is agent-written but gated (§3.10); 5 is
pipeline output. The composition manifest records each fragment's provenance
(§4) — the agent reads one document, the storage stays partitioned. A merge
session (§3.13) composes one fragment more — the merge preamble
(`40_merge.md`, ours), session-scoped and removed from every non-merge
composition.

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

**The surveyor runs like any other session** (`tb survey REPO --run`): the
same harness path, its own stage contract (`survey_session`), and a
project-level session home at `.tb/root/` — bunker-shaped (ledger,
sessions/, context/, tb.md) so the machinery applies unchanged, above all
ticket bunkers, never colliding with one (bunker directories end in
`_bunker`). The runner seeds the session's working copy from the existing
tbroot.md — updating, not rewriting — or from the template, composes the
surveyor preamble (`00_surveyor.md`, ours: the write-the-universe rules),
and the agent edits the working copy in place. Installation happens outside
the session through the typed tbroot intake: the conjecture/anchor lint
gates the text, acceptance and rejection are ledgered in
`.tb/root/ledger.jsonl`, and the installed tbroot.md mirrors into every
bunker's composition at the next recompose (§3.9). A session that leaves a
fresh template untouched fails; an update session that changes nothing is
recorded (`survey_no_update`). Refresh-triggered surveys on the main branch
remain future work under the re-run cost policy.

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

**Tracker integration ends at a provider-neutral JSON boundary.** `tb ticket
--from-json FILE` (or `-` for stdin/copy-paste/pipes) accepts one complete
ticket object, preserves typed extension metadata and tracker comments, and
passes it through the ordinary ticket handler. The ticket ID may come from the
object, so a translator can pipe directly into `tb ticket --from-json -`.
Provider adapters live outside the installed core — the GitHub Projects example
under `examples/` reads GitHub and emits this object — keeping the core
stdlib-only and preventing any tracker from becoming the authority. JSON input
is a complete source, not an implicit override layer: combining it with field
flags or the editor-template path is rejected.

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

**The root is git-relative; the durable storage is companion-relative.** `tb
init` runs inside a source git repository and fails outside one. The user first
creates a Git repository on any host and supplies its clone URL. TicketBunker
has no repository-creation path or hosting API: it verifies that the supplied
remote exists and is accessible before creating `.tb/` or changing the source,
then publishes only its config/data branches. An empty remote is valid. After
both branches publish it writes `.ticketbunker.json` and adds `/.tb/` to the
source ignore file. The ignored `.tb/config` and `.tb/data`
directories are ordinary checkouts of the locator's canonical branches.
Ticket IDs resolve under
`.tb/data/projects/<PROJECT>/tickets/`. A second `tb init` is refused; other
clones run `tb setup`, which reads the committed locator and fast-forwards both
local checkouts. A fresh setup checks out configuration normally but uses a
sparse data checkout containing only project ticket indexes. Listing, search,
facets, and pagination therefore work without copying every bunker. Opening or
mutating one indexed ticket expands only that ticket's canonical path in the
sparse checkout. Data branches made before indexes remain compatible: setup
detects the missing index and falls back to a full checkout rather than hiding
old tickets. Embedded same-repository and standalone `--root` operation
remain compatibility modes, not defaults.

**Initialization authority comes from Git, not TicketBunker identity.** The
initializer is the user whose credentials successfully publish the initial
config and data branches. TicketBunker records that Git identity but does not
grant it authority. Repository/branch rules decide who may update
`ticketbunker/config`; ordinary Git permission decides who may update data.
The companion can be the source remote, but a dedicated repository is the
default because it cleanly separates source history, ticket data, and config
administration and can serve several projects.

**The bunker merges the way Git merges.** Concurrent data-branch commits come
together through ordinary git, with the merge behavior the data shapes demand,
declared in the companion data branch's `.gitattributes`:
append-only logs (`ledger.jsonl`, `inferences.jsonl`) merge by **union** —
every side's entries survive, order within the merge is immaterial for
append-only records; the **master is never merged**, it is a generated
artifact — `merge=ours` keeps a placeholder and the post-merge hook
regenerates it from the actual children. The generated ticket index likewise
uses `merge=ours` to avoid line-oriented JSON conflicts; after a rebase the
publisher recomputes each locally changed ticket reference against the selected
remote index, so concurrent additions remain discoverable. `tb hooks --install` writes
`post-commit`/`post-merge` hooks that run `tb refresh` — resolve the
current branch's ticket, re-run the observation pipeline, recompose,
regenerate the master — never blocking git (a branch without a ticket, or
any failure, is a silent no-op). This is the first slice of the §3.1
trigger model: the viewer polls the database, the hooks keep the database
current, so what a user is looking at tracks the repo as they work.
Ticket.json edits from two branches still conflict like source code — by
design, they are declared data. **Semantic conflicts get a merge session
in the same trust frame** (`tb merge`): merging is part of solving a
ticket, so two branches' report components disagreeing about the same
code is a legitimate agent task — a promoted session, never a silent
auto-resolution. The candidate signal is computed mechanically — repo
paths that more than one component's claims anchor to — and pre-filled as
`[observation]` lines in a merge form that embeds every component
verbatim between markers; a merge preamble fragment (`40_merge.md`, ours,
removed from every non-merge composition) carries the rules: resolve by
verification, not preference; tags upgrade only by verification; finding
no disagreement is a result. The session runs on the ordinary harness
path under its own stage contract (`merge_session`), edits the embedded
components in place, and fills a Reconciliation section held to the same
stratum and anchor rules as any report. Republication happens outside the
session, through the same lint and anchor gates as any publish — each
component re-verified and re-stamped against its own branch refs — and
the reconciled set lands atomically or not at all; the ledger records
`merge_reconciled` (or the rejection), and the master regenerates.

### 3.14 Naming conventions and branch creation

**Tickets and branches follow an allowed pattern.**

- **Ticket IDs** match a configured pattern — by default `PROJECT-NUMBER`,
  e.g. `TB-1267` is TicketBunker's ticket 1267. Enforced at ticket intake:
  an ID that doesn't match the pattern is rejected.
- **Branches** follow `username/TICKET-name` — e.g. user Tim working
  TB-1267 gets `Tim/TB-1267-fourier-alg-impl`. The ticket ID embedded in
  the branch name is what makes branch resolution (§3.2) deterministic
  instead of fuzzy.

**The workflow:** configure identity once → select or locally claim a global
ticket → input just the short branch name → the system **fills in the rest and
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
agent as primary source material for Why. Creation has four ways in, one
gate: provider-neutral `--from-json FILE|-` for API/copy-paste adapters, field
flags for scripting, `--from FILE` for a saved human template, and — the default
when no fields are given — `$EDITOR` on a pre-filled template, like `git commit`;
a draft that fails validation is saved and the error says how to resume it.
After creation, fields move only through `tb set`
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

### 3.15 Project configuration by composition

Canonical configuration lives on the companion config branch and changes only
through typed, revision-checked commands. `tb config` first refreshes that
branch, then opens a localhost editor with four direct surfaces: Identity,
Repository, Ticket fields, and Integrations. The CLI, editor HTTP transport,
and programmatic callers dispatch the same command envelopes; transports do
not reimplement mutations.

Configuration is composition, not runtime inheritance. Versioned global field
sets form the default composition copied and pinned when a project is created.
A project composes those exact versions with project fields, field overrides,
and removals. Later changes to global defaults affect newly created projects,
not existing project compositions. Tickets hold values and the universal tags
field; they do not define schemas.

Integrations are named, provider-agnostic configuration entries. Each maps
external payload paths into the same resolved project fields, so provider-only
information can be retained without coupling the core ticket type to one
service. GitHub Issues, GitLab Issues, and Jira Cloud are editable starter
templates, not a closed provider enum; a user can add or remove a custom
integration through the same typed command boundary. The editor renders each
integration as a matrix of TicketBunker-field ↔ external-field input rows.
Adding and removing rows is a local draft; **Publish mappings** replaces that
integration's complete map in one revision-checked config commit, so partial UI
publication cannot leave the matrix half-applied. The legacy one-pair command
remains available to CLI/API callers for compatibility.

Tokens are local-only secrets in a mode-0600 credential file outside the
repository. Built-in environment names remain `GITHUB_ACCESS_TOKEN`,
`GITLAB_ACCESS_TOKEN`, and `JIRA_API_TOKEN`; custom entries deterministically
use `TICKETBUNKER_<INTEGRATION>_TOKEN`. Canonical configuration stores labels,
templates, and field mappings, never token values.

### 3.16 The project universe boundary

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

### 3.17 Format doctrine: JSON and markdown

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
- **Sync:** companion mutations commit, reconcile, and push the data branch as
  part of command completion. Append-only ledgers use union merge and generated
  masters use the ours driver before regeneration. `tb sync` remains the
  explicit transport for standalone/legacy stores.
- **Query:** by ticket ID — clone/pull, then read the bunker by its fixed
  layout. Latest report, raw observations, ledger — all addressable by path.
- **Emit:** structured report (markdown + machine-readable JSON alongside).

## 6. Authorization

- Session data is served only to authorized users. Authorization is the
  companion remote's Git access model; teams should grant report access no
  more broadly than the source material allows.
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
- No write-back to Jira, GitHub Projects, or GitLab trackers in v1.
- No cross-repo inference beyond the repos explicitly attached to a session.

## 9. Resolved decisions

- **Report storage:** the report is the remote artifact; the pi session stays
  local (remote session storage is an optional optimization if cheap). A report
  never requires waking a session to read.
- **Session/report layout:** `projects/<PROJECT>/tickets/<TICKET-ID>/` containing `tb.md` +
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
- **Cardinality:** a ticket may wait unclaimed with no branch and may later have many;
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
   (§3.16).
3. ~~Report sync transport~~ — resolved: the backend is a companion git repo;
   command completion reconciles and pushes its data branch, while `tb sync`
   remains for legacy/standalone stores (§2, §5). Works anywhere git works,
   including bundle transfer across air gaps.
4. ~~File format of components~~ — resolved by the format doctrine: JSON for
   certain machine export/import, markdown for framework-agnostic agent- and
   human-facing content; reports are markdown, their machine interchange (when
   needed) is a JSON export, not a second source of truth (§3.17).
5. Concurrency on the master: if two users' sessions publish child updates for
   different branches at nearly the same time, who regenerates the master?
   Simplest answer: regeneration is idempotent from the child set, so
   last-writer-wins is safe *as long as* the regenerator always pulls the
   current children first — worth stating as an invariant when the sync
   transport is chosen.
