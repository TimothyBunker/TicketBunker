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

TicketBunker is an **intermediate context and workflow layer** with its own
domain model. Trackers, Git repositories, agent harnesses, and persistence
engines meet only behind its typed API:

```
user → desktop app → TicketBunker API → domain services → adapters
                                                    ├── Git repositories
                                                    ├── agile providers
                                                    ├── coworker harness
                                                    ├── local database
                                                    └── optional remote service
```

It receives ticket context, resolves related branches, mechanically extracts
Git facts, lets an agent enrich them with repository search, and reports a
structured summary back to the requesting user.

**Deployment principle:** TicketBunker must be **exceedingly simple and
diagnosable**, self-hostable inside a team's security boundary, and adoptable
without modifying a source repository. A local-only Bunker works with no
service. Collaboration uses a compatible TicketBunker service selected and
controlled by the team; TicketBunker the product does not require ownership of
that host or its database.

**Ownership principle:** **Pi does not own this system.** Pi is a replaceable
agent adapter. TicketBunker owns ticket state, configuration, observations,
reports, ledgers, and the publish gates. Replacing Pi cannot discard or redefine
those artifacts.

**Backend principle: the backend is TicketBunker's API and domain model.** Every
user-facing client calls the same action registry. Every device has one SQLite
database per Bunker. A Bunker may be local-only, or its local database may act
as a replica of team state reached through an authenticated remote TicketBunker
service. Desktop clients never connect directly to a remote database, and the
physical remote engine is not part of the client contract.
The versioned request/response boundary is specified in
`docs/remote-service-contract.md`.

**Git principle: Git is an attached source and workflow tool, not a control
plane.** Git provides commits, branches, diffs, authorship, and source access.
It does not provide TicketBunker membership, authentication, database discovery,
configuration authority, or ticket synchronization. Cloning a repository never
joins a Bunker. Explicit exports may use Git or portable files, but export is not
live storage.

## 3. Architecture

### 3.1 Ticket bunkers, sessions, and storage

#### Domain boundaries

- A **Bunker** is the configuration, membership, security, and synchronization
  boundary a user creates or joins.
- A **Project** is a recursive work scope inside one Bunker. A Project may have
  a parent and any number of child Projects. It is not synonymous with a Git
  repository or a tracker project.
- A **Repository binding** attaches a canonical Git repository identity to one
  Project. It is the Project's shared allowlist: a Project may permit zero or
  more repositories, and the shared binding never contains a device filesystem
  path.
- A **local repository attachment** maps one shared binding to one checkout path
  on one device. That path never synchronizes.
- A **ticket** belongs to one Project and may relate to work across any
  repositories explicitly attached within the permitted Bunker scope.
- A ticket may have zero branches before work starts and multiple branches over
  its lifetime. Branch order remains recency-first unless explicit priority is
  assigned; the master report follows the same order (§3.4).

The resulting hierarchy is:

```
Bunker
├── configuration and field registry
├── members and capabilities
└── Projects
    ├── Project
    │   ├── child Projects
    │   ├── repository bindings
    │   └── tickets
    └── Project
```

#### Local database

Every Bunker has exactly one device-local SQLite database. Multiple Bunkers on
the same device remain isolated in separate files; a small machine-local
registry lists them for the app. The local database stores the Bunker record,
Project tree, repository bindings, device paths, configuration snapshots,
tickets, reports, sync cursor, and local jobs. It preserves the provenance
strata described in §4 rather than flattening them into presentation documents.
Every bounded database operation commits or rolls back and closes its SQLite
connection before returning; callers never retain a file handle, which keeps
database replacement, backup, and cleanup semantics consistent on Windows and
Unix hosts.

The default machine-owned layout is outside source repositories:

```
<TicketBunker data home>/
├── bunkers/registry.sqlite3
└── bunkers/<LOCAL-BUNKER-ID>/bunker.sqlite3
```

No `.ticketbunker.json`, `.tb/`, hook, credential, or database file is written
to an attached source repository merely because it was selected. Repository
hooks used for opt-in Git event observation must remain local Git metadata and
must not become discovery or authorization state.

#### Optional remote relationship

A local-only Bunker is authoritative on its device. A shared Bunker adds one
explicit relationship:

```
local Bunker database ↔ authenticated TicketBunker service ↔ host-owned database
```

The local connection record contains only the service origin, remote Bunker ID,
replica identity, configuration revision, sync cursor, state, and a credential
reference. It never contains raw credentials or a database connection string.
The reference is resolved by the machine-local credential store
(`ticketbunker/credentials.py`), a mode-0600 JSON file in the application data
home that hands the HTTP adapter an ephemeral bearer; an operating-system vault
backend can replace that file without changing the reference or any Bunker
record. A host may use SQLite, PostgreSQL, or another transactional engine;
clients cannot observe or depend on that choice.

**Membership has two roles.** A Bunker **owner** holds every capability:
`bunker:share`, `members:invite`, `members:manage`, and `config:write` in
addition to reading and writing tickets. A **member** reads and writes tickets
and reads configuration. Creating a local Bunker makes the device user its
owner, publishing it keeps that ownership, and an invitation carries the role
it grants. Only owners see or use service settings, invitations, and shared
configuration publication. The role and capability vocabulary lives in
`ticketbunker/remote.py`; every compatible service enforces it on its own side,
and a cached capability only decides what the editor offers.

**Replicas pull.** A shared Bunker syncs when it is opened (at most once a
minute) and on **Sync now**: the app fetches the service's authorized snapshot
and reconciles the replica by identity, upserting Projects, repository
bindings, configuration documents, tickets, reports, and members, removing
bindings the service no longer lists, and recording the new cursor, revision,
and sync time. Device paths and the credential reference are untouched. The
Bunker page states "Shared on host · role · synced N minutes ago".

**Sharing is the owner's inverse of joining.** A local Bunker is published to a
chosen service with **Share Bunker**: the app signs in (with the one-time owner
setup token a fresh reference service prints, or a credential this computer
already holds for that server), sends the complete local publication (Projects,
repository allowlists without device paths, configuration documents, tickets,
and reports), and turns the local database into the owner's replica of the
published copy. Ownership, revision, and sync cursor come back from the
service. From then on every shared mutation is proposed locally, sent to the
service as typed, idempotent, revision-checked changes, and written locally
only after the service accepts it. `propose_*` validates without writing and
`apply_*_change` refuses a shared write that carries no acceptance, so a
replica cannot self-elevate even if its cached capabilities say it may.

Joining is server-first and authorization-first:

1. The user pastes a short-lived invitation link from a Bunker owner.
2. The app displays the exact service origin and remote Bunker identity.
3. The service authenticates the user and authorizes membership. A service
   that advertises `invitation-credentials` may create the member and their
   credential at redemption from the machine-local profile; the issued
   credential goes straight into the credential store and only its reference
   reaches the Bunker database. A service that requires prior sign-in says so.
4. Only after enrollment succeeds does TicketBunker create the local database.
5. The first authenticated snapshot supplies the Bunker configuration, Project
   hierarchy, repository identities, tickets, reports, capabilities, revision,
   and sync cursor.
6. The user maps permitted repository identities to local checkout paths.

The optional remote relationship is configured only at Bunker scope. Projects
cannot connect their own databases or services. A Project configurator may
publish repository binding rows; every device then validates the selected
checkout's credential-free `origin` identity against those rows and completes
`git fetch --prune origin` before saving its local path. A failed fetch leaves
the local mapping unchanged. The same checkout may serve multiple Project
scopes in a monorepo because permission is evaluated per Project.

A ticket belongs to one Project rather than one repository. Each branch report
records the repository binding it observed, so one ticket can retain distinct
work across several permitted repositories without branch-name collisions.

Possessing source code or an invitation is not proof of membership. Invitation
bearer material is bounded, short-lived, and never returned in application
state. Cloning or forking a repository therefore has no TicketBunker side
effect.

#### Legacy companion import

The former companion-Git backend is a migration source, not a second live
authority. Import reads its locator, configuration, tickets, and reports into a
new local Bunker while leaving the source checkout and companion data unchanged.
The import records source path, digest, counts, and time. Retirement or deletion
of legacy data is a separate, explicit operation after the user verifies the new
Bunker. Git bundles or JSON/markdown packages remain valid explicit export and
air-gap transport formats (§3.17).

The harness session stays local-first and separately owned. The Bunker stores
TicketBunker facts, observations, ledgers, reports, and bounded coworker state;
Pi continues to own its native session implementation. Reports remain readable
without waking an agent.

**Ticket context enters the session either:**

- **manually** (a user pastes / uploads the ticket context — the known drawback,
  see §7), or
- **on request**, after TicketBunker detects a new branch matching the ticket
  key. Git lifecycle hooks mark the ticket as changed without calling a model;
  the user presses Synthesize in the ticket state hub when a fresh report is
  useful.

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
  to the pi session to populate it** with the relevant context. A Git lifecycle
  hook records that the branch changed, but never invokes the session. When the
  user requests synthesis, the session starts pre-loaded from current backend
  and Git state rather than reconstructing context by hand.
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
   work, not noise from an aging fork point. TicketBunker data lives outside
   attached repositories, so it cannot enter these diffs. During explicit
   legacy import, old `.tb/` paths are still pathspec-excluded so reports never
   recursively describe generated artifacts.
4. **Merge history.** When the branch last merged main (or was rebased), how far
   its code-affecting history is ahead/behind, and whether its non-merge code
   commits have been merged back. Legacy database-only `.tb/` commits are
   excluded so old generated history cannot change the observed development
   state.
5. **Work separation.** Partition commits that touch source paths into *author
   work* vs *incidental merges/main churn*. TicketBunker-owned paths, including
   the legacy `.tb/` layout, never become code authorship.
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
3. **The report cannot live inside the generation session.** The generation
   session file is an append-only provenance record; the report is a living
   document with its own update lifecycle (children rewritten as branches move,
   master regenerated on every child change) and its own audience (remote, authorized users). So
   reports are durable records in the Bunker database and only accepted report
   state syncs through the remote service. The local ticket coworker described
   in §3.5 has a separate private session in TicketBunker's machine-owned data
   home. That conversation can propose report changes but is never itself
   shared report truth.
4. **Components live together under the ticket, keyed by source branch.** A
   successful publish writes the gated component and the regenerated master
   into the Bunker database as `report` records (scope `branch:<name>` with
   its repository binding, and `combined`). On a shared Bunker those two rows
   are sent to the service as `report` changes first and written locally only
   after acceptance; any member holds `tickets:write`, so a report published
   on one computer is shared truth for every replica that syncs. Legacy
   companion data may be imported but is never a simultaneous writer.

**The pipeline runs in a machine-local runtime directory.** For a Bunker
ticket, `ticketbunker/bunkerruntime.py` projects the database row into
`<data home>/bunkers/<bunker>/runtime/<TICKET>_bunker/`, a directory shaped
exactly like a legacy ticket bunker, so the observation stages, context
composition, harness session, lint and anchor gates, and master builder run
unchanged. The projection is rewritten only when the ticket itself changes, so
a rerun keeps its input fingerprint. A runtime that has no working copy of a
report the Bunker has accepted (a replica, an imported copy, a machine that
never ran synthesis for the ticket) is seeded with the accepted text when it
is materialized, so the truth commands and the coworker read the same reports
the page shows; an existing working copy is never overwritten, because the
pipeline regenerates it and publication hands it back. The coworker session
reads a Bunker ticket through this same runtime when the page passes the
Bunker id. Observations, session forms, transcripts,
inferences, and the ledger stay in that directory as this machine's evidence;
the accepted reports are handed to the Bunker database by `publish_reports`
and the database is the only shared truth. Nothing is written into the source
repository, and a Bunker ticket needs no `.tb` layout or companion repository.

**Report component schema** — each new child leads with a compact Reader brief,
then carries the complete who / what / where / why / state evidence trail. Each
section has a defined **determinant**: the data source that decides its content.
The brief is optional only when admitting a legacy component published before
this schema; every newly scaffolded session must fill it.

| Section | Determined by | Content | Stratum |
|---|---|---|---|
| Reader brief | **The agent, bounded by the evidence below.** Ticket fields, git observations, and verified finds determine what may be emphasized | `### Who`, `### What`, `### Where`, `### Why`: one or two short claims for Who, What, and Why in descending apparent importance; one short Where claim may name up to three paths in importance order. It makes the evidence readable; it does not summarize every observation. | agent-authored [real] / ranked [speculation]; never [observation] |
| Who | **People, never files.** Assignee, reporter, reviewers, people of interest, and commenters from the ticket; the branch's git authors (authorship); recent authors of the touched files on the baseline and the last committer on each other branch of the ticket (people observation) | Direct ticket people first, then branch authors, then the people around the code, all pre-filled. An agent claim names a person and why they matter, citing `(ticket)`, `(authorship)`, or `(people)`; a source path alone is never a Who claim and "no one else" is not a finding | config + observation |
| What | **Git diffs and actual code** — not just which region changed but the changed code itself. The diff stage captures patch text as an observation so What can quote and describe real changes | Summarized diff: files, hunks, additions/deletions, work vs. merge churn, with the actual changes characterized | observation |
| Where | **Other ticket work, git diffs, ticket information, and a little agent speculation** — in that order of authority | Three blocks, in epistemic order. **(1) Edits that already happened** — from the git diffs and prior components of this ticket, with emphasis: the ground truth of where the ticket already lives. **(2) Located information directly relating to the ticket** — symbols/paths from the ticket found in the baseline and what ties into them. **(3) Informed speculation** on where the user may need to make changes to solve the ticket, ranked by grounding; if nothing concrete, "tag up with reporter/person of interest" | observation → real → speculation |
| Why | **The ticket itself, primary sources** — then the agent | **Primary sources first**: the ticket text and other primary material at the top. At the bottom, the agent's speculation — a composition of real findings and speculation, clearly tagged | ticket + real + speculation |
| State | **Merge history + authorship observations** — then the agent | Branch freshness vs main, merged-back or not, apparent progress | observation + speculation |

**Proactive, but bounded.** A report should tell the reader what could bite
them and what to do next, inside the same four questions rather than in a
new section. Each Reader brief part may carry, on top of its ordinary claims,
one `Risk:` claim (something that could cause the reader problems: a bug or
regression the agent can point at, an untested change, a file also changed on
the baseline, a stale branch, an unclear owner) and one `Next:` claim (one
concrete thing to do now), each tagged and grounded like every other claim.
Who: whom to involve. What: what in the change looks wrong or untested.
Where: where a change is likely to bite. Why: where the change and the
ticket's intent diverge. The lint counts them separately from the ordinary
claims and refuses a second of either. The `risk` stage pre-fills the
mechanical hazards as `[observation]` lines (files changed on both the branch
and the baseline since the merge-base, TODO/FIXME-style markers added by the
patch, source changed with no test touched), so the agent interprets hazards
instead of inventing them. The viewer renders the aspect as a second chip,
Risk or Next, beside the trust chip.

### 3.5 Report-first UX

**Users consume the report first.** A user explicitly chooses when synthesis is
worth running, but never has to operate the generation harness as a separate
tool:

- The state hub's Synthesize or Update synthesis button creates and drives the
  session headlessly; the bootstrap extensions and command-fed agent do the
  rest. Branch creation and ordinary Git activity only mark synthesis as
  missing or changed.
- A stopped synthesis names what stopped it, in this precedence: the run's
  own summary plus the first blocking `[error]` finding, then a provider or
  harness message such as a usage limit, never a `[warn]` line, which does
  not block a publish. The worker log keeps the whole run in order.
- When a run passed every gate but the Bunker (typically its service) did not
  accept the report, the verified child stays in the runtime directory with no
  recorded publication; the next Synthesize shares that report without
  calling the provider again, as long as it was verified at the current
  commit.
- Synthesis failure is a user-request state, not merely a historical worker
  record. Only a run marked by the explicit Synthesize/Update/Retry command may
  render **Synthesis stopped** or a doctor warning. Failures persisted by older
  Git-triggered versions remain local audit history but render as the ordinary
  **Not synthesized** state until the user chooses Synthesize.
- Generation is local until one component passes intake. Agent-side `tb infer`
  calls remain typed and ledgered but do not each enqueue a remote change; the
  worker owns one final transactional publication after the ordinary report
  gate succeeds. A transient remote/DNS problem therefore cannot turn every
  inference into a separate failed sync.
- The pre-agent deterministic refresh takes its baseline from the repository
  binding's configured base branch, never from a hard-coded `main`. It may read the
  already-fetched `origin/<base>` ref when a collaborator has no local base
  branch; this is observation only and never checks out, pulls, or mutates the
  source worktree. If refresh cannot start, the worker preserves the concrete
  failed state and next Git action in the Synthesis strip and writes the exact
  diagnostic to View log. The model is not called.
- Pi and Codex get one bounded report-format repair pass when their first draft
  fails mechanical lint or anchor checks. The repair receives the exact
  findings and edits the same child draft; it cannot edit source or Combined.
  The result still passes through the unchanged typed lint and anchor gates,
  and a second failure stops with the draft/transcript locations and leaves the
  prior shared report untouched. Command harnesses remain deterministic and
  single-pass.
- The report is the sole user-facing artifact — it must be complete enough that
  reading it answers who/what/where/why without a follow-up conversation.
- The generation transcript/session is retained as **provenance**: if a report claim
  needs auditing, it shows exactly which commands produced which observations
  and how each inference was derived. The accepted component is also stamped by
  orchestration with every generator fact it actually knows — harness, provider,
  and model — rather than asking the agent to identify itself. Work authorship
  and report generation are separate facts. That session is an audit log, not a
  chat to resume.
- One local **ticket coworker** provides optional follow-up without becoming a
  source-work agent. There is one underlying Pi session per local user and
  ticket, shared across all ticket branches. The viewer presents it as four
  clear Who, What, Where, and Why conversations; every message carries its
  selected category and Combined-or-branch scope. The coworker receives the
  ticket, the selected synthesis and evidence, exact child branches, and its
  prior ticket conversation. It can explain, advise, accept corrections, and
  prepare a report-update proposal. A message may also carry one bounded,
  structured reference to text the user selected in the current synthesis:
  category, scope, child branch when known, and exact visible excerpt. The quote
  remains visible in local conversation history and is report context, never an
  instruction or independent evidence. It cannot edit source, execute shell or
  Git mutations, or directly edit Combined.
- Raw coworker messages and Pi session files stay private below the Bunker's
  machine-owned runtime directory, keyed by ticket and local user. Pi is launched with
  only `read`, `grep`, `find`, and `ls`. Report-check failures are expressed in
  conversation as a concise rejected/not-saved outcome; exact lint and anchor
  findings stay in the private local coworker diagnostics and never become
  coworker copy. A proposal is inert until the user accepts it. Acceptance
  rechecks the current child digest, report lint, and every real anchor, updates
  only the named child Reader brief subsection, and regenerates Combined
  mechanically. The ordinary typed report publication path remains the only
  way the accepted report becomes shared truth.

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
The database maintains those bounded summaries transactionally beside the
ticket records. They contain human-facing ticket fields, small report/branch
counters, source URLs, and content revisions, never report bodies. A summary is
updated as a typed ticket mutation side effect, not through another write
authority.

**A Bunker page is its Project catalog, never a ticket list.** Selecting a
Bunker shows the recursive Project tree from the local replica. Each row names
the Project, its allowed repositories with this computer's connection state,
and its ticket count including descendants, so the count matches what the
Project's own page will list. The Bunker header carries only Configure
(defaults for new Projects), Connect remote, and Add project. Selecting a
Project applies the same search and facets to that Project and its
descendants, lists any subprojects above the ticket catalog, and owns the
repository allowlist through Configure. Project and ticket rows are
lightweight database references. Opening a result is the deliberate point
where the full ticket snapshot is composed. Local-only, shared, and imported
legacy Bunkers use the same query contract.

Per-ticket pages remain organized around completing one ticket. The singular
page keeps an explicit **Ticket / Agent** contract as two sections of one page
in reading order rather than tabs: the ticket first, then **Agent work** under
its own heading with a rule between them, so nothing is blended and nothing is
hidden behind a control in the corner. Selecting a branch in the rail scrolls
to Agent work with that report selected. The sticky header carries a
persistent **Ticket / Details / Agent work** section control: each button
scrolls to its section, and the one for the section currently under the
reader's eye stays selected, so the reader can jump between the ticket, its
facts, and the agent's report from anywhere on the page without tabs. Ticket
is the Jira-like issue surface. The header names the source project before the
ticket, then presents the mapped status as the sole lifecycle ground truth
beside labels and the source's updated time. Description, optional acceptance
criteria, and optional notes each sit under a small heading and read as
constrained rich text, including indentation-preserving nested ordered and
unordered lists. **Activity is mirrored and read-only**: comments and
provider-neutral `activity` entries (`comment`, `history`, `worklog`, `log`,
`note`, or an integration-defined kind) render as one stream entry each with
the author, the kind when it is not the section's default, a relative time
with the absolute time on hover, the source link when the provider gave one,
and the text as constrained rich text; a history entry without text shows its
field change. The section headings count their entries. Whatever field names
the provider used (`author` or `user`, `text` or `body`, `when` or `created`)
read the same way; the viewer has no authoring control.

One **Details** section follows the reading flow as a grid of labelled facts
rather than a two-column definition list: assignee, reporter, type, priority,
created and updated (calendar date with relative age), labels, source with its
outbound link, attached repositories, and linked boards. An absent value says
what is absent (Unassigned, Not set, None linked) instead of a dash. Transport
IDs, provider flags, and raw source state move into a native Metadata
disclosure; duplicated source titles,
timestamps already promoted into the header/details, mapped status fields, and
project ordinals are omitted. Classification is semantic (project, timestamp,
status, ID, URL), never a GitHub/Jira/GitLab-specific view branch. Nested provider
metadata is flattened into readable field paths instead of exposed as a JSON
blob. Agent owns Agent work and never mixes those fields into its report surface.
Synthesis and Branches form one persistent right rail outside the tab panels,
so the reader keeps the same ticket and Git orientation while moving between
Ticket and Agent. Branches shows one row per branch name that carries the
ticket key: local heads and remote-tracking refs fold together, and the row
says where the branch exists (checked out, local only, on origin and not
checked out, both), how far it is ahead of and behind the Project's base, and
who last committed to it. The read uses only local refs. The rail is rendered
once; the Work control and branch links are never duplicated between views.

Agent work opens on one branch report: the checked-out branch when it has a
report, otherwise the most recent report. The selector at the right of its
heading lists each branch report and, only when more than one exists, **All
branches**, which stacks every branch's Reader brief contributions in
development-recency order with the branch named and a visible rule between
them. Each category immediately follows its synthesis with a native disclosure
for the unchanged evidence; a legacy report without a brief shows its evidence
trail in place and says so. The Branches rail lists branches only, each noting
whether it has a report; selecting one narrows Agent work to that report or, for
a branch without one, shows a direct Synthesize empty state. The mechanically
assembled master remains the Bunker's `combined` record for agents, export, and
audit rather than a reading surface of its own. Reports whose refs are no
longer resolved remain available for audit.
State remains a separate collapsed report trail, because tracker lifecycle and
observed branch state are already visible in the rail. Legacy children derive
compact previews from their evidence without inventing synthesis. Verification
and freshness remain visible. Each branch report carries one provenance line
read from its mechanical stamp: the commit and branch it was verified against,
when, the harness, provider, and model that generated it, and when the Bunker
recorded it. Work authors stay separate from that generator identity. Unknown generator fields
stay absent instead of being guessed. Before the first report, Agent work shows
only its empty state rather than a meaningless Combined selector. Trust tags
render as chips that say what to trust rather than the on-disk word: Observed
for pipeline facts, Verified for anchored [real] claims, and Speculation with
its grounding rank (grounded, adjacent, ungrounded); a legend above the report
explains each on hover, and a `Risk:` or `Next:` prefix becomes a second
chip beside it. The bracket tags and prefixes remain the report's stored form.

**File references act on click, and selection never acts on its own.** Any
reference a report or ticket carries in the forms the anchor checker accepts
(`path:line` or `path` in a code span, `@path:line`, `@path`, or the older
`@[path]:line`) renders as one reference control. Clicking it shows a small
action toolbar beside it: **Open in editor** at that line, **Copy path** as an
absolute path, and **Send to chat**, which quotes the reference into the
coworker. A line number is optional and defaults to the first line. The same
toolbar appears beside selected report text with **Send to chat** and
**Copy**; selecting text no longer opens the chat by itself, and the toolbar
never steals the selection, so copying a phrase stays a plain copy. Ticket,
Project, and Agent reading remains strictly read-only.
Each Who, What, Where, and Why synthesis has a small, accessible chat-bubble
action, and the same ticket coworker is available from one bottom-right bubble.
Selecting synthesis text or evidence inside a category's full trail reveals a
contextual bubble only after a deliberate drag, long touch selection, double
click, or keyboard selection. Ordinary taps, clicks, and text-field caret
movement clear the contextual action instead of reviving an older selection.
Activating the bubble attaches only that excerpt, its category, scope, and
branch to the next message. The quoted attachment sits above the composer with
an explicit Clear quote action, never an unexplained close glyph over the input.
This is a local
conversation and proposal surface, not a second report writer. Its expanded
surface is a right-side drawer with a collapse control, not a close action;
topic, scope, draft, attached reference, and expanded state survive navigation
between the ticket and its included source editor. The other exception is an
explicit local **Work** control: a small plus beside Branches opens a form for a
short branch name and a real base-branch choice. Its CSRF-protected POST
dispatches the same `workflow.start` action as automation clients; it is not a ticket,
comment, report, or lifecycle editor. Every ticket page renders immediately
from the local Bunker database and local or already-fetched Git refs. After the
response is sent, one coalesced background command syncs through the configured
remote service when permitted, fetches source remote-tracking refs without
touching the source worktree, and never queues synthesis. A compact header
cluster replaces the full-width status strip. Healthy remote sync and Git refs
are absent; loading, cached, and stopped states appear as small stateful
dots beside the title. Synthesis remains visible as its own dot plus `Updated
<age>` when known, or its concise actionable state when no successful update
exists. Hover, keyboard focus, and click reveal each full label, detail, and
action, so touch and keyboard users do not depend on hover. The synthesis
button remains the only application route that starts the model. While running, the
state stays visibly busy. On failure, Retry synthesis and a diagnostic
disclosure sit together; the disclosure names and can copy the exact local log
path without leaking raw output into the app. Every stopped state preserves the prior
report and names the next action. `tb view` normally opens the installed
TicketBunker desktop application and returns the terminal immediately. The app
loads its bundled interface and owns one private typed core worker for its
lifetime; native use does not start a localhost server. If the desktop app is
not installed, the command serves the same compiled interface as a detached
loopback browser fallback. `--browser` explicitly selects that fallback,
`--foreground` retains its attached diagnostic mode,
and `--no-open` starts only its local service. Per-page presence and close
signals keep the browser fallback alive across navigation and stop it shortly
after the final tab closes. Presence tolerates browser timer throttling: a
hidden or covered tab may heartbeat only once a minute, so a session lives
three minutes without one, a tab re-announces itself the moment it is visible
or focused again, and the worker prints why it stopped. If the service is gone
anyway, the page shows one banner naming the fix instead of silent request
failures.

The viewer checks shared truth at a human-scale cadence and pauses Git-backed
polling while a text entry has focus or the page is hidden. Direct user actions
still update their state immediately. Typing and selection therefore never
compete with repeated repository scans.

The local health response identifies the TicketBunker version. After an update,
`tb view` never reuses an older detached worker for the same project; it opens
the current viewer on a nearby loopback port and tells the user the older tab
can be closed.

Verified source locations may use `@[relative/path]:line` in report or coworker
copy. Existing report synthesis may also carry a verified inline-code
`relative/path:line` anchor. The viewer renders both forms as the same explicit
local-open control in Combined, child synthesis, full trails, and chat. Its
path text remains selectable: dragging across it suppresses file opening and
feeds the ordinary synthesis-selection coworker action, while a click on the
path or activation of its adjacent Open button opens it. The
default target is TicketBunker's included source editor, rendered by the same
application and requiring no system editor or terminal launcher. Its
`editor.read` command resolves symlinks, rejects anything outside the current
checkout or absent on the current branch, and admits only bounded UTF-8 text.
Every rendered source reference ends with a small copy control. Its
`editor.resolve` command applies the same checkout boundary without reading or
opening the file, then copies the absolute local path and line to the clipboard.
`editor.save` requires the digest returned by that read, refuses to overwrite a
file changed since it opened, and atomically replaces only that file. It does
not stage, format with an external process, or run Git. The page preserves the
file's LF/CRLF convention, supports configurable spaces/tabs, tab width and
line wrapping, and can opt into deterministic trailing-whitespace and final-
newline cleanup. Unsaved drafts stay in the browser when a save is rejected.
The fixed toolbar names the originating ticket as its back destination. When
the editor belongs to a ticket, selecting bounded source text reveals an Ask
about selection control; the same ticket coworker receives the guarded relative
path, line range, selected text, and whether that text is an unsaved draft.

An external editor remains an explicit Local settings option. In that mode the
same `editor.open` guard launches `TB_EDITOR`, the saved command,
`VISUAL`/`EDITOR`, Git's `core.editor`, or automatic `vim` then `nano`, without
a shell. On Windows it also recognizes Git's bundled Vim. If that optional
editor is absent, cannot start, or requires a terminal launcher that is not
available, the same source-link action falls back to TicketBunker's included
editor. Opening or saving is always a user action and grants the coworker no
source-writing capability.
Neither `tb view` nor `tb open` publishes or mutates shared report truth; both
are machine-local actions even in a shared Bunker. A new authorized replica
creates its machine-local session directory before writing a form, so missing
local artifacts are an empty state rather than a filesystem traceback.
Pages poll that local state and reload only when ticket content or refs change.
All content is HTML-escaped before the constrained
report markup is re-introduced; agent text can never inject into the page.
Localhost-only is a decision, not a limitation: there is deliberately no
bind option and no auth surface — remote viewing (phone, another machine)
is SSH port-forwarding's job, which already carries the repo's own access
control. The Work endpoint accepts a per-server unguessable transport token
embedded only in same-origin pages and remains bound to localhost. Do not add
network exposure to the viewer. Viewer responses disable caching, framing,
referrer propagation, and MIME sniffing.

**The viewer is an error boundary, not a traceback surface.** Expected Git,
remote, and workflow rejections render three concise facts: what stopped, what
state was preserved, and the next command or action. Raw Git stderr and Python
details never enter HTML or JSON responses. Unexpected failures use the same
shape with an opaque diagnostic ID; full detail is appended only to the local
Bunker diagnostics directory. A browser
disconnect during a response is normal transport state and produces no error.
Problem pages do not poll or auto-retry, and TicketBunker never performs a
recovery merge, switch, reset, stash, pull, or rebase on the user's behalf.

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
own files for observation anchors. A bunker citation is normalized to the
bunker's layout first: anything up to and including the bunker's directory
name, or an absolute prefix, is stripped, so `runtime/ECS-4_bunker/ticket.json`
and `ticket.json` resolve the same way. The workflow preamble tells the agent
to use the plain form. A cited line number must fall inside the
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
| **Staleness** — child's recorded commit SHA vs branch head; master's recorded child digests vs actual children | yes | on branch push events and on read | mark stale prominently; offer Update synthesis |
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
pipeline **pre-fills** it — Who from ticket config, authorship, and the
people stage (recent authors of the touched files, committers on the
ticket's other branches), What from the diffs and the risk stage, Where from
the diffs and baseline overlap, State from merge history, all as
`[observation]` lines — and the
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

**The surveyor runs like any other session**: the same harness path, its own
stage contract (`survey_session`), and a Project-scoped session namespace in
the Bunker's machine-owned runtime directory. The runner seeds the session's
working copy from the existing
tbroot.md — updating, not rewriting — or from the template, composes the
surveyor preamble (`00_surveyor.md`, ours: the write-the-universe rules),
and the agent edits the working copy in place. Installation happens outside
the session through the typed tbroot intake: the conjecture/anchor lint gates
the text, acceptance and rejection are ledgered in the local Bunker database,
and the installed Project root context is composed into every affected ticket
at the next recompose (§3.9). A session that leaves a
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

**Tracker integration ends at a provider-neutral ticket command.** `tb ticket
--from-json FILE` (or `-` for stdin/copy-paste/pipes) accepts one complete
ticket object, preserves typed extension metadata and tracker comments, and
passes it through the ordinary ticket handler. The ticket ID may come from the
object, so a translator can pipe directly into `tb ticket --from-json -`.
For recurring imports, `ticket.upsert` and atomic-batch `ticket.sync` are the
same programmatic gate used by installed adapters. Incoming fields refresh the
source snapshot while absent local extensions survive; TicketBunker's branch
key, explicit branch assignments, repository binding, and mechanical timestamps
remain locally owned. A semantically identical snapshot performs no write,
adds no ticket-ledger event, and advances no local or remote revision.

**Linking boards is one flow, and the token is the only thing typed.** A
tracker is connected once at Bunker level (or in Settings) by pasting a
personal access token; the app verifies it against the provider, keeps it in
the machine-local credential file, and records the linked account in the
Bunker's shared `connections` document when the member may write
configuration. A Project's **Board** view then lists every board that token
can see (GitHub Project boards for the account and its organisations, and
repositories whose issues can be mirrored) and the user picks one. A Project
mirrors one or more boards: each link appends to the Project's shared,
revisioned `tracker-sources` document (per board: a stable id, kind, owner,
number or repository, title, link, whether to carry comments), unlinking
removes one entry and keeps the tickets it already produced, and the document
travels in the join snapshot so a new member's Project already knows its
boards. Sync reads every linked board and keeps one copy of an issue that
sits on two of them, by its tracker identity. Linking first scans the board and builds the Project's model from what
it actually carries: Status options become the status enum in TicketBunker's
vocabulary, every other single-select field becomes an enum with the board's
options, and iteration, number, date, and text fields are added with mappings
into the synced item. Existing fields are updated in place and never removed.
The model is published before the board is recorded, fields before the mapping
that references them, and on a shared Bunker each step is a service-accepted
change. The same view shows what will sync, with the field matrix and ticket
fields behind advanced disclosures rather than as separate surfaces. **Sync
tickets** reads that source with the machine-local token, normalizes every
item through the Project's field matrix, and upserts tickets into the Bunker
database. A ticket
id is a deliberate `KEY-n` prefix in the issue title when present, otherwise
the Project key plus the issue number, so ordinary boards need no naming
convention. Every synced ticket carries its tracker identity
(`metadata.source_identity`), which keeps the id stable across syncs. When a
board spans repositories and two items claim one id, deliberate title keys win,
the other item receives the next free number once, and its identity keeps that
number afterwards. On a shared Bunker the created and updated tickets are sent
to the service as `ticket` changes (members hold `tickets:write`) before they
are written locally; unchanged tickets produce no change at all.

Tracker tokens are connected once per computer in Settings → Trackers and
stored in the machine-local credential file; the app shows only whether a
token exists. Nothing in a Bunker or its service ever carries a tracker token.

Adapters remain thin stdlib transports under `ticketbunker/integrations/`; they
fetch and normalize but cannot write around the typed sync path. `tb github` is the
first complete adapter: it reads one Issue or every Issue item in a GitHub
Project, mirrors comments and Project fields, applies the project's published
GitHub field matrix, then submits one typed batch. `--fixture` drives that exact
normalization and command path without network access. The example under
`examples/` remains a JSON-only copy/paste wrapper. This packaging does not make
GitHub authoritative over TicketBunker or introduce a GitHub dependency, and
there is no tracker write-back. JSON creation remains a complete source, not an
implicit override layer: combining it with field flags or the editor-template
path is rejected.

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

Machine-local operating-system behavior crosses one additional narrow boundary:
`platform_services.py`. It detects the host per operation and supplies
application-data locations, release/runtime keys, executable suffixes, command
parsing, editor candidates, terminal launchers, and background-process flags.
That parser also tokenizes configured command-harness templates, so native
Windows paths retain their backslashes and quoted spaces without creating a
second harness contract. Piped, headless helpers receive the Windows
`CREATE_NO_WINDOW` process flag through this boundary; an agent, synthesis, or
readiness check may never allocate a terminal merely because its parent is the
graphical application.
Domain services never branch on Linux, Windows, macOS, WSL, drive letters, or
desktop-session details. WSL runs the ordinary Linux implementation; if a
native shell communicates with a core in another environment, that connection
is a launcher/transport concern rather than a new Bunker or repository type.

**The Bunker is the instance boundary.** Its durable data is addressed through
domain identifiers and typed services, not through client-visible filesystem
paths. `bunkerdb.py` owns the SQLite schema and transactions; `application.py`
owns the shared action registry; `remote.py` owns the versioned service
protocol and the role/capability vocabulary. `layout.py` remains the sole
authority for legacy import paths and runtime artifacts that still need a
filesystem representation. The reference service under `ticketbunker/service/`
is the only other SQL boundary in the package: it stores host-owned data that
no client ever sees, behind the same contract any other host implements.

**The root is Bunker-relative, not Git-relative.** Creating a Bunker requires a
name and a first Project, but no repository. Attaching a repository is a later,
explicit action against a Project's configured repository allowlist. It verifies
the local checkout's canonical remote identity, fetches `origin`, and stores the
device path only in that Bunker's local database. It writes no locator, database,
ignore entry, or membership hint into the repository. The same repository may
be attached to appropriate Projects in the same or different Bunkers without
implying that any Bunker owns it.

**Initialization authority comes from the selected storage authority.** A
local-only Bunker is created by the local user and remains local. A shared
Bunker is created by, or joined through, an authenticated TicketBunker service.
The service decides membership and capabilities before a local replica exists.
Git credentials and repository branch rules govern source access only; they
never grant Bunker administration or ticket access.

The companion-Git implementation remains behind an explicit, read-only import
adapter. Its `tb init`, locator, sparse checkout, and data-branch behavior are
compatibility tools for existing installations, not discovery or persistence
contracts for new Bunkers. Import never makes the legacy store and SQLite
replica concurrent writers.

**The Bunker reconciles typed records, not Git files.** Local mutations use
SQLite transactions and append an ordered change record. A remote service
accepts idempotency keys and expected revisions, authorizes each operation, and
returns either the accepted revision or a typed conflict. Append-only ledger
facts are unioned by identity; generated masters are always rebuilt from
accepted children; mutable ticket and configuration records use explicit
revision checks. There is no direct database connection and no file-level
merge driver.

Opt-in Git hooks may record branch creation, checkout, commit, merge, rebase,
and rewrite events into the local Bunker job queue. They live only in local Git
administrative metadata, chain pre-existing hooks, do no network or agent work,
and return immediately. Git activity therefore cannot spend model calls or be
failed by a provider. The app also works without hooks by refreshing attached
repositories on explicit user actions.

The viewer compares the current branch against the last accepted report and
durable input fingerprint. It shows the last synthesis time and, when the
verified commit is known, the number of commits since. Only an explicit
Synthesize, Update synthesis, or Retry synthesis command queues the detached
worker. The worker first runs the deterministic observation refresh, then the
ordinary harness-neutral agent pipeline and lint/anchor publish gate, then
publishes through the typed report service. Existing successful components
retain their harness on later requests; otherwise the local selected Pi or
Codex profile is used. A queued or claimed request is represented by a durable
running state so the state hub cannot offer a second request while work is in
flight. The queued HEAD is checked after the agent returns; a form for a
superseded commit stays an unpublished local draft. Provider failure preserves
the prior report, records the failure in the Bunker ledger, and exposes the
bounded local log through View log. Pre-existing executable Git hooks are
preserved and chained, and uninstalling TicketBunker's hooks restores them.
Concurrent report edits may still conflict semantically even when their records
do not. **Semantic conflicts get a merge session
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
performs an ordinary, guarded Git workflow**. It refuses a dirty worktree,
fetches `origin` when present, switches to the chosen local/tracking base,
fast-forwards it only, then creates and switches to the convention-named branch.
Only after Git succeeds does it create the typed session config and publish the
branch assignment. Nobody hand-types the convention, so nobody gets it wrong —
the pattern is enforced by construction at the same place the branch is born.
Failure messages name the state that stopped Git and what the user must do next;
no reset, forced checkout, auto-stash, or non-fast-forward merge occurs.

Opening a ticket first renders Branches from local and already-fetched refs plus
explicit assignments. The viewer then runs `git fetch --prune origin` in the
background and recomposes the same union. Fetch updates refs only, so it is
allowed while the source worktree is dirty and never becomes an implicit pull,
rebase, switch, merge, stash, or reset. If the network is unavailable, the
ticket remains open with local and cached remote refs and an explicit retry
action; the failed fetch remains ledgered. A branch without computed ahead,
behind, or merge state is labeled **Needs Git state** and says to open that
branch to generate its comparison. This names the missing program output
instead of an ambiguous human/agent observation state.

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
construction. An existing branch may be attached only when its name already
contains the bounded ticket key; a misnamed branch is rejected with a rename
instruction instead of silently weakening resolution. `workflow.attach`
records an accepted branch on the ticket itself (`assigned_branches` in
ticket.json — ours provenance, travels with the repo, re-entering through the
ticket intake gate). Branch resolution (§3.2) unions both sources and labels each
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

Canonical configuration lives in the Bunker database and changes only through
typed, revision-checked actions. In a shared Bunker the remote service
authorizes configuration changes and distributes immutable revision snapshots;
local replicas do not elevate their own capabilities. The configuration editor
has five direct surfaces: Identity, Local settings, Storage, Ticket fields, and
Integrations. Local settings never synchronize. The included source editor is the default;
editor mode, indentation style, tab width, line wrapping, save cleanup, and an
optional external command live only in `~/.tbconfig`. Both the browser and `tb
config --editor` dispatch `local.editor.update`. The CLI, editor HTTP
transport, and programmatic callers dispatch the same command envelopes;
transports do not reimplement mutations.

Storage connection is explicit and does not infer authority from source code.
The Storage page shows the exact local Bunker database and, when configured,
the service origin, remote Bunker identity, replica state, revision, and sync
cursor. It never exposes a database URL or raw credential. Repository bindings
are managed separately as Project resources. Diagnostics validate local schema,
remote authentication/sync readiness, configuration revision, attached
repository availability, integration credentials, agent readiness, and pending
jobs. Every failure includes a concrete next action; structured output exposes
the same result to automation, and offline mode skips only remote access.
Diagnostics write only machine-local records and never mutate shared truth.

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
integration's complete map in one revision-checked mutation, so partial UI
publication cannot leave the matrix half-applied. The legacy one-pair command
remains available to CLI/API callers for compatibility.

Tokens are local-only secrets in a mode-0600 credential file outside the
repository. Built-in environment names remain `GITHUB_ACCESS_TOKEN`,
`GITLAB_ACCESS_TOKEN`, and `JIRA_API_TOKEN`; custom entries deterministically
use `TICKETBUNKER_<INTEGRATION>_TOKEN`. Canonical configuration stores labels,
templates, and field mappings, never token values. Installed transports read an
environment value first, then the configured local credential file; neither
path returns secret values in configuration state or command output.

### 3.16 The project universe boundary

**A ticket belongs to exactly one Project in one Bunker.** A Project can contain
child Projects and can attach multiple repositories, so the work scope is not
forced to match one repository or one tracker board. By default the agent may
follow the ticket through repository bindings attached to that Project. Access
to descendants or ancestors must be explicitly included by Bunker policy; tree
membership alone is not an implicit code-reading grant.

**An agent and its session are scoped by authorized repository bindings.** An
agent cannot venture into an unattached checkout or another Bunker's code. If a
ticket trail leaves the authorized scope, the only automatic input is another
authorized report reference. Otherwise the agent says what boundary stopped it
and recommends a person to contact rather than guessing.

**Cross-Project tickets link by stable identity.** When work genuinely spans
Projects, tickets carry references to their counterparts. An intra-Bunker
reference may resolve through the Project tree; an inter-Bunker reference
resolves only when the current user is independently authorized. A reference is
never a license to read another Project's code.

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

### 3.18 Distribution and local agent readiness

The ordinary end-user artifact is a self-contained native desktop bundle: the
Tauri app, a frozen `tb` sidecar, the official standalone pi executable,
TicketBunker's thin pi adapter, the upstream license, and a per-file checksum
manifest. Linux produces Debian and AppImage packages and Windows an NSIS
installer as the first tested desktop targets. The macOS app/DMG build remains
compile- and CI-covered but experimental until it is exercised on real
hardware. x64 and arm64 remain release architectures. The app passes its
private resource directory to the core as
`TB_BUNDLE_ROOT`, so GUI launch never depends on a shell's PATH and runtime
verification uses the same manifest as the CLI.

Installation is an operating-system workflow, not a command-line workflow.
Windows installs a current-user NSIS package with a Start-menu launcher; Linux
installs a desktop entry whose `Terminal` value is false. Opening that launcher
without arguments lands on Bunkers and selects repositories through the native
folder picker. The AppImage is a portable Linux alternative, while CLI archives
remain automation and server artifacts rather than onboarding.

Tagged Windows releases are an Authenticode boundary. The release workflow
must refuse to publish when its certificate secrets are absent, sign and
timestamp both the frozen core and installer, then verify every emitted
signature before upload. Manual workflow builds may remain unsigned for test
purposes but cannot become a tagged product release. Certificate material
exists only in the ephemeral release runner and the repository's secret store.

TicketBunker pins one reviewed pi version. Release construction downloads the
corresponding official platform archive and refuses to extract it unless its
recorded SHA-256 digest matches; runtime discovery verifies every extracted Pi
file against the bundle manifest before invoking it. The declared executable
path is relative to each upstream archive and is allowed to differ by platform;
staging, managed installation, and runtime discovery all consume that same
path instead of assuming the archive has a wrapper directory. The native build
also stages `tb` under Tauri's target-triple sidecar name and refuses to proceed when
the frozen core or declared Pi executable is missing. Each native installer is
copied into the flat release-asset directory with an adjacent SHA-256 file, so
publishing does not depend on a build runner's nested Tauri directories.
Updating the Pi pin is an explicit, tested TicketBunker release change, never
an ambient npm update. Cross-platform release tooling resolves build commands to
their concrete executable before launching them without a shell; this includes
Windows command shims such as `npm.cmd`.

Within one running TicketBunker process, Git commands are serialized per
checkout. Multi-command worktree mutations such as branch creation hold that
repository transaction across preflight, fetch, switch, and publication setup,
so a ticket-open background ref refresh finishes before the mutation begins.

Linux x64 publishes both Debian and AppImage installers. Linux arm64 publishes
the native Debian installer; its AppImage is withheld until Tauri's upstream
arm64 `linuxdeploy` path can be made reproducible. A failed optional packaging
format must not discard an already verified native installer for that platform.

A portable CLI-first archive with the same frozen core and verified Pi runtime
remains available for servers, air-gapped transfer, and environments that do
not install desktop applications. It is a distribution alternative, not a
different data or command model.

The **stdlib-only core stays independently installable** as a wheel, pipx
application, or Python zipapp. It carries the adapter, pin manifest, and
license, but not the large pi binary. On its first ordinary `tb init`, it
downloads the pinned platform archive, verifies the recorded upstream digest,
safely extracts the complete distribution into TicketBunker's machine-local
data directory, writes a per-file manifest, and records the resolved `pi_bin`
in `~/.tbconfig`. A team may instead provide an approved runtime with the
explicit `TB_AGENT_PI_BIN` override, choose `--core-only`, or construct its own
bundle inside an air-gapped boundary. The core data model and every publish
gate have no binary-runtime dependency.

Agent authentication is machine-local. On `tb init`, TicketBunker discovers
the bundled or managed runtime before configured and PATH fallbacks, proves the
runtime can start, and uses pi's credential-safe `auth check --no-refresh` for
the selected provider/model. TicketBunker reads pi's non-secret default
provider and model identifiers but never reads, copies, emits, or stores
credential values. If selection or authentication is absent in an interactive
terminal, init opens pi once for `/login` and `/model`, then checks again and
saves only `pi_bin`, `harness`, `provider`, and `model` in `~/.tbconfig`.
Non-interactive setup fails actionably rather than installing apparently-working
automation. `--core-only` is the explicit local opt-out: repository hooks still
record ticket changes, while the state hub explains that synthesis is
unavailable until a later ordinary init enables the agent.

Provider/model selection is also a machine-local control surface, never shared
ticket configuration. `tb init` accepts optional `--provider` and `--model`
values and otherwise adopts Pi's authenticated defaults. Later, `tb agent
model` opens Pi's picker, while the same optional flags provide a scriptable
route; `tb agent login` and `tb agent status` complete that small lifecycle.
All routes call the same credential-safe validation and only replace the saved
selection after it passes. Successful init always names `tb agent model` as the
way to change the selection.

Git hooks pin the exact installed `tb` launcher rather than a Python module or
shell-specific PATH. Detached workers invoke that same launcher and the
resolved pi executable, so a packaged installation keeps working outside the
terminal environment that ran init. Git events remain non-blocking: readiness
is established during init/doctor, while any later provider outage is recorded
by the existing background worker without failing Git.

### 3.19 Standalone desktop application

The ordinary interactive surface is a distinct TicketBunker desktop
application. Its bundled UI is also the explicit browser fallback; the native
host is a distribution, security, and lifecycle boundary, not a second backend.
The CLI remains fully supported for scripts, hooks, remote
terminals, and direct commands.

The shared interface may render in the bundled system webview or, through the
explicit browser fallback, in the system-selected standards-compliant browser.
Rendering technology does not decide execution authority. Native app actions
cross bounded, request-ID-correlated JSONL envelopes into one app-owned Python
worker and the same command registry; browser
fallback actions cross the bounded localhost transport. Git, file, database,
and agent work always runs in the selected local core environment. The Python
backend changes hosts by selecting `platform_services.py`, not by changing
domain handlers. Linux and Windows are the initial supported desktop
environments; WSL is simply one Linux execution location and is never inferred
from a repository path.

The application has three layers:

1. A small Tauri shell owns windows, application startup and shutdown, and the
   privileged Bunker launcher.
2. The frozen Python `tb` executable remains the application core. It owns Git,
   tracker adapters, typed commands, the read model, agent orchestration, and
   all durable TicketBunker state.
3. One React/TypeScript interface is compiled into `ticketbunker/app_assets`.
   Tauri loads that bundle directly; the stdlib browser fallback serves the
   exact same files at an ephemeral loopback origin. Both transports reach the
   same Python command handlers and cannot drift into separate behavior.

The packaged desktop executable and `tb` core are siblings. The shell starts
one `tb _app-worker` when the first native action is requested, retains its
private stdin/stdout pipes for the application lifetime, and stops it with the
launcher. Production Windows builds use the graphical subsystem, and both the
core worker and direct Pi setup process are explicitly launched without a
console window. The worker accepts only an explicit desktop action allowlist, bounds
every JSON request, redirects accidental handler output away from the protocol,
and returns stable command codes without exposing tracebacks or payloads.
Unexpected failures receive an opaque ID and a credential-safe diagnostic in
the native application data directory. Broken transport is never automatically
replayed because a mutation may already have completed.

`tb view` discovers the sibling or PATH desktop binary and passes only a
canonical repository and guarded in-app route as structured arguments, without
a shell or port. `TB_DESKTOP_APP` and `TB_DESKTOP_CORE` are developer or
managed-install overrides, never required user setup. One Tauri window renders
Bunkers, Projects, tickets, configuration, Agent work, coworker chat, and the
included editor. It may invoke only the bounded `desktop_command` bridge, the
directory picker, clipboard, and guarded HTTPS login opener. Rust forwards
domain actions unchanged to `_app-worker`; it never reads files, runs Git,
queries SQLite, or publishes reports itself. Remote HTML is never loaded into
the native context.

The browser fallback is a transport, not a second application. `app_server.py`
serves the compiled bundle, injects one launch CSRF token, and exposes only the
same desktop action allowlist at `/api/action`. It binds to loopback, bounds
requests and responses, keeps page-presence heartbeats, and exits after the last
tab closes. It contains no domain handlers. The legacy Python HTML renderer is
retained only for compatibility tests and read-only integrations; ordinary
desktop and browser use share the compiled application.

First run is application-facing and starts with the storage boundary:

- establish a machine-local TicketBunker profile (display name and email),
  defaulting to Git identity when available;
- select or defer an agent provider/model, with login handled in the app and
  secrets retained by the provider/runtime rather than TicketBunker;
- choose **Create Bunker** for a local Bunker and first Project, or **Join
  Bunker** with an invitation from a known service;
- for Join, inspect the service origin and Bunker identity locally, then require
  explicit authentication and enrollment before creating a replica;
- after the Bunker exists, add recursive Projects and explicitly attach local
  Git checkouts to an allowed repository row in the appropriate Project.
  Repository identity is derived from the checkout only after the user chooses
  Connect.

Agent setup is an app-native conversation with Pi, not an embedded terminal.
The privileged Bunkers window starts the verified Pi runtime in RPC mode with
sessions, tools, context files, skills, prompt templates, themes, and ambient
extension discovery disabled. On Windows that child is headless and cannot
open or flash a console window. It loads only TicketBunker's pinned
`tb_auth.ts` bridge. That bridge enumerates Pi's provider/model catalog and
calls Pi's own `ModelRuntime.login`; Pi remains the sole credential-store
writer and the sole implementation of provider OAuth and API-key flows.

Provider notifications and prompts cross a short-lived JSONL pipe into app
forms. Secret input travels from the local webview through Rust directly to
Pi's stdin; it never enters the Python command dispatcher, `~/.tbconfig`, a
TicketBunker log, or shared project data. Login URLs are displayed before a
user action and a label-guarded Rust command opens only valid HTTPS URLs in the
system browser. Only the bundled local window may listen for these setup
events. Browser mode cannot perform provider login and states that directly.

The bridge is packaged with the frozen core and materialized into a stable,
digest-addressed machine-local asset path before the one-file core exits. Its
small compatibility seam to Pi's pinned `ModelRegistry` is integration-tested
against that exact Pi version, and release construction must complete a live
catalog exchange through the staged Pi executable before producing an
installer. The seam must be reviewed with every pin update.
Native artifact publication filters by the current package version so a stale
local Tauri build can never be mistaken for output from the active release.
Closing setup kills the owned Pi child. Merely opening or cancelling setup does
not enable an agent or record a first-run choice. **Use without agent** records
the explicit local opt-out; **Use this model** saves only a successfully
authenticated provider/model selection.

Opening the app without a target creates one bundled **Bunkers** window. It
reads a machine-local profile, registry, and Bunker summaries through typed
`application.*` actions; Rust does not invent another config or data store. The
profile may default from Git's global name and email for convenience, but that
identity grants no Bunker capability and editing it never changes global Git
configuration.

Settings → System exposes the installed version and update state. The native
app checks at most once per day after its local content is visible; an explicit
Check action is always available. The core accepts only GitHub's bounded typed
release response, validates the exact official TicketBunker release and
download URL prefixes, and chooses an installer matching the platform boundary.
The privileged webview never loads remote release HTML and TicketBunker never
executes a downloaded file. **Download update** opens the validated installer
URL in the operating system browser, leaving installation and signature checks
to the OS. Browser fallback checks only on request and uses the same typed core
action.

**Create Bunker** asks only for a Bunker name and first Project key/name. It
creates one local SQLite database without touching a repository. **Join
Bunker** asks only for an invitation link; it never shows raw authorization or
credential fields. The service response supplies membership, capabilities,
configuration, Project tree, repository allowlists, ticket snapshot, reports,
revision, and sync cursor before the app installs the replica, so a new member
lands with every Project already configured.

**Share Bunker** appears only on a local Bunker's page. It asks for the server
address and, on a fresh reference service, the one-time setup token printed by
`tb serve`; a computer that already holds a credential for that server needs
only the address. Publication makes the current user the owner. Once shared,
**Members** replaces it: everyone sees the member list with roles; owners also
create invitation links (one use, seven days by default), copy them, see
pending invitations, and revoke them. There is no hand-edited service record.

A selected Bunker lists its Projects rather than tickets; the ticket catalog
appears only inside a selected Project, beneath that Project's subprojects.
Within a Bunker, **Add project** places a new Project under the selected parent.
The selected Project's **Configure → Repositories** table is the only repository
surface. A configurator publishes canonical allowed remotes and base branches;
each user maps a row to a checkout with **Connect**. The picker normalizes the
working-tree root, validates its `origin` against that row, fetches remote refs,
and saves only the device path. Service connectivity lives only at Bunker scope
through Share Bunker and Members; nothing accepts a database connection string.
A separate Migration section lists legacy companion-backed projects and offers
**Import copy**. That operation is read-only with respect to both source and
companion repositories and records its import digest.

A Project page names its board beneath the title and offers **Sync tickets**
when one is linked, or **Connect a board**, which opens the Project's Board
view. That view asks for a token only when this computer has none, otherwise
it lists the boards the token can see; picking one links it, and the field
matrix and ticket fields sit beneath as advanced disclosures. A Bunker page
offers **Trackers** (connect GitHub, GitLab, or Jira once for the Bunker) and
**Defaults** (the field and matrix documents new Projects copy). Settings →
Trackers reaches the same token step for this computer. GitHub is the tracker
this version can list and sync; the others accept a token so that step is
already familiar.

**Configure** therefore has two Project tabs, Board and Repositories, and two
Bunker tabs, Trackers and Defaults. Ticket fields, integration matrices, the
Bunker's connections, and the Project's tracker source are whole revisioned
documents: a publish uses the revision loaded by the editor and replaces the
complete draft atomically.
GitHub, GitLab, and Jira are prefilled templates; custom integrations use the
same matrix. Project creation copies and pins the current Bunker field and
integration documents, so later default changes affect only later Projects.
Local Bunkers publish on the device. On a shared Bunker the editor is writable
only for a member whose live capability includes `config:write`, and each
publish (fields, integrations, repository allowlists, new Projects with their
pinned documents) is sent to the service first and written locally only after
the service accepts it; members without that capability see a read-only editor
that names the owner as the next action.

On Linux, the shell respects an explicitly selected GSettings backend. When no
backend was selected and the advertised session D-Bus socket is absent, as in
some minimal desktop sessions, it uses GTK's in-memory settings backend before creating any
window. The native folder picker therefore remains responsive and does not
flood a launching terminal with unrelated `dconf` warnings.

The bundled local window is the sole recipient of native directory-dialog,
clipboard, local-command, and agent-setup permissions. Its Tauri commands check
the caller's window label. No remote origin is admitted to that ACL boundary;
Tauri 2.11.5 or newer is required.

There is no mandatory TicketBunker-hosted account or database implied by the
desktop application. Identity and membership are local or scoped to the remote
service selected by the Bunker. TicketBunker may provide adapters and setup
guidance, but teams own their service, database, credentials, access rules, and
remote deployment.
Browser mode remains an explicit accessibility, diagnostics, server, and
air-gapped fallback rather than a lesser command path.

### 3.20 Reference service

`tb serve` is TicketBunker's own answer to "where does shared data live" for a
team without operators. It is one stdlib-only process that owns one SQLite
file and implements the complete `/api/v1` contract in
`docs/remote-service-contract.md`. Nobody installs or administers a database
engine; backups are a copy of that file. The data store is
`ticketbunker/service/storage.py`, the transport is
`ticketbunker/service/server.py`, and neither is reachable through any client
path except the versioned HTTP contract.

The tiers are chosen by what a team already has:

- **Solo.** A local Bunker and no service.
- **Small team, zero operations.** `tb serve` on any machine the team controls
  or on a platform that hands out HTTPS and a persistent disk. The service binds
  to localhost by default; a deployment passes `--host 0.0.0.0` behind its TLS
  proxy or `--certfile`/`--keyfile` to terminate TLS itself. Clients still
  refuse plain HTTP anywhere except localhost.
- **Larger organisation.** The same contract on the team's own infrastructure,
  engine, and identity provider; the desktop application does not change.

First start prints a one-time **owner setup token** and the address to paste
into Share Bunker. Claiming the token creates the first user and their
credential; `--reset-setup-token` prints a new one for an operator with disk
access. Publishing a Bunker makes the publisher its owner. Invitations are
issued only by members holding `members:invite`, are single use, expire, can
be revoked, and carry the role they grant. Redeeming one on this service
creates the member and issues their credential (`invitation-credentials`), so
a small team needs no separate accounts system; a larger deployment may
require sign-in before redemption instead. Bearer and invitation tokens are
stored only as SHA-256 digests. Snapshots and changes are scoped to active
members, and a non-member learns nothing about whether a Bunker exists.

Typed changes are applied all or nothing with per-record revision checks and
idempotency keys: a replayed key returns the earlier result, a stale expected
revision returns `409` with the stable code the client renders as a next
action, and a duplicate project key is reported as such. Repository allowlists
use the content-derived revision that both client and service compute from the
same shared fields, so neither side keeps a separate counter. Join requests,
membership management, deploy recipes, and ticket/report synchronisation are
the next slices on this boundary.

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

The TicketBunker backend is **the typed domain service** (§2). Desktop, optional
CLI automation, integrations, Pi, and remote transport are clients of the same
action registry and cannot invent parallel behavior:

- **Ingest:** manual ticket context and read-only tracker snapshots through the
  same provider-neutral upsert/batch action; explicit Git observation events.
- **Ledger:** immutable stage and command facts appended transactionally by the
  executing service (§3.8).
- **Publish:** gated child report records and mechanically regenerated masters.
- **Sync:** revisioned, idempotent change exchange through a compatible remote
  TicketBunker service. Clients never connect to its physical database.
- **Query:** bounded Bunker, Project, ticket, report, observation, and ledger
  read models from the local database.
- **Import/export:** explicit, portable JSON/markdown packages and a read-only
  legacy companion adapter. Transfer is never confused with live membership.

## 6. Authorization

- A local-only Bunker is accessible to its device user. A remote Bunker service
  authenticates users, enrolls replicas, and authorizes every query/mutation by
  Bunker capability before returning shared data.
- Roles are owner and member. Owners alone hold `bunker:share`,
  `members:invite`, `members:manage`, and `config:write`; the service enforces
  those capabilities on every request, and the application only mirrors them
  when deciding what to offer.
- Source Git access and Bunker membership are independent checks. Cloning a
  repository, knowing its remote URL, or possessing a stale invitation grants
  no ticket, report, configuration, or membership access.
- Invitations are short-lived enrollment capabilities. Bearer material is
  consumed only by the join request, never persisted in application state, and
  never substitutes for the service's user authentication.
- Teams should grant report access no more broadly than the source material
  allows. A service may additionally require that repository access be proven,
  but Git permission alone can never grant Bunker access.
- Open question: whether to support a *redacted report tier* for the
  "data-consumer" persona (someone allowed to know ticket state but not code
  content). This is exactly the cross-boundary use case, and also the riskiest —
  a summary of classified code may itself be classified. Default answer for v1:
  **no redacted tier; access to a report requires access to the repo.**

## 7. Known drawbacks & mitigations

| Drawback | Mitigation |
|---|---|
| Tracker APIs and field shapes differ | Normalize at a provider-neutral typed batch boundary; publish editable field-path matrices, retain provider metadata, and keep offline/manual JSON intake available. GitHub Issues/Projects is the first packaged transport. |
| Branch-per-ticket naming is a convention, not guaranteed | Fuzzy matching + explicit manual branch association as fallback; report "no branches found" honestly rather than guessing. |
| Reports go stale as branches move | Git events mark staleness without spending a model call; reports show their timestamp and commits since, and the state hub offers Update synthesis. |
| Teams need different database scale and hosting | Keep the client contract at the versioned service API; local SQLite, hosted PostgreSQL, or another transactional engine can change without changing Bunker identity or exports. |
| Existing companion-Git data must survive the cutover | Provide an explicit read-only import with digest and counts; never operate the old and new stores as concurrent authorities. |

## 8. Non-goals (v1)

- Not a Jira replacement or plugin; it's a sidecar.
- No write-back to Jira, GitHub Projects, or GitLab trackers in v1.
- No cross-repo inference beyond the repos explicitly attached to a session.

## 9. Resolved decisions

- **Domain boundary:** Bunker owns configuration, membership, authorization,
  synchronization, recursive Projects, tickets, and reports. A Project is a
  work scope, not a repository. Repositories are explicitly attached resources.
- **Storage:** every Bunker has one local SQLite database. Collaboration is an
  optional relationship to an authenticated TicketBunker service backed by a
  team-owned database. Clients never receive a database connection string.
- **Git relationship:** Git supplies source history and guarded workflow only.
  It supplies no membership, configuration authority, Bunker discovery, or
  live database transport. Cloning a repository never joins a Bunker.
- **Join:** the remote service authenticates and authorizes before a local
  replica is created. Repository paths are mapped after enrollment and stay
  device-local.
- **Migration:** companion-Git installations enter only through an explicit,
  read-only import. JSON/markdown and Git bundles remain explicit export/import
  media, not live storage.
- **Report storage:** the accepted report is a durable Bunker artifact; the pi session stays
  local (remote session storage is an optional optimization if cheap). A report
  never requires waking a session to read.
- **Session/report layout:** local database records preserve ticket, context,
  observation, ledger, report-child, and report-master identities without
  exposing storage paths as an API. The harness persists its own session data
  in its own storage; the Bunker stores only what TicketBunker owns.
- **Session context:** delivered as `tb.md`, composed deterministically from
  separate fragments (explanation / workflow / current information) so context
  is modular and a user can see exactly what a session is fed (§3.9).
- **Report structure:** the report is agent-authored and lives *outside* the
  session file. One component per branch, owned by the session; a master is
  fully regenerated (never patched) whenever a child changes, recording child
  digests for staleness detection. Only accepted report records sync remotely.
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
3. ~~Report sync transport~~ — resolved: a local Bunker commits the accepted
   report transactionally, then a shared Bunker exchanges revisioned changes
   through its configured TicketBunker service (§2, §5). Explicit exports and
   bundles carry data across air gaps without becoming live storage.
4. ~~File format of components~~ — resolved by the format doctrine: JSON for
   certain machine export/import, markdown for framework-agnostic agent- and
   human-facing content; reports are markdown, their machine interchange (when
   needed) is a JSON export, not a second source of truth (§3.17).
5. Concurrency on the master: if two users' sessions publish child updates for
   different branches at nearly the same time, who regenerates the master?
   Simplest answer: regeneration is idempotent from the child set, so
   last-writer-wins is safe *as long as* the service checks the expected child
   revision and regenerates from the accepted child set. This is a remote API
   invariant, not a client-side merge responsibility.
