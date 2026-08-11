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

## 3. Architecture

### 3.1 Ticket bunkers, sessions, and storage

**Cardinality model:**

- Each **branch** has a **user** (the owner/author of that work).
- Each **ticket** gets a **branch**, but a ticket may span **multiple
  directories**.
- Each ticket has a **unique ID**, and that ID names its **ticket bunker**.
  Uniqueness is a hard requirement — the bunker directory *is* the ticket's
  identity in the system, so collisions are unacceptable.

**On-disk layout — one bunker per ticket:**

```
<ticket_id>_bunker/
├── literal_pi_session.json    # the pi session file (pi stores a session as a
│                              #   single self-contained file, all branches of
│                              #   the conversation included)
└── report                     # the deliverable artifact
```

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

**Harness: pi (pi.dev).** Pi is a minimal agent harness with an extension model
(TypeScript extensions, skills, prompt templates) rather than baked-in behavior,
which is exactly what we need: we build the bootstrap ordering ourselves and the
harness enforces it, *irrespective of the agent* (see §3.2). Pi's ordering
semantics are visualized on pi.dev. Two other properties matter:

- Sessions are stored as **single files** → the `literal_pi_session.json` layout
  above is native, not something we bolt on.
- Pi runs **headless** (`pi -p`, RPC over stdio, or SDK embedding) → the agent
  can run with no TUI and no human in the loop, which §3.5 depends on.

### 3.2 Deterministic bootstrap pipeline (the "observation" phase)

On session instantiation, harness extensions execute — in order, outside agent
control:

1. **Branch resolution.** Find branches whose names contain the ticket key
   (convention: branches are labeled by ticket). Record all matches; zero matches
   is itself an observation.
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

Inference outputs are labeled as such. The report format keeps the two strata
visually and structurally separate, so a reader can trust the observations
absolutely and weigh the inferences appropriately.

### 3.4 Report

Delivered back to the requesting user (and optionally attachable to the ticket):

| Section | Content | Stratum |
|---|---|---|
| Who | Authors, committers, reviewers touched the ticket's branches | observation |
| What | Summarized diff: files, hunks, additions/deletions, work vs. merge churn | observation |
| Where | Relevant-locations list (changed / new / old / candidate sites) | observation + inference |
| Why | Goal of the work as inferred from ticket text + code | inference |
| State | Branch freshness vs main, merged-back or not, apparent progress | observation + inference |

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

## 4. Ordering & atomicity principles

- **Observations before inference, always.** Enforced by the harness, not by
  prompting.
- **Observations are atomic**: each one is a single mechanical fact (one diff, one
  merge-base, one authorship entry). They can be cached, replayed, and audited.
- **Inferences are less atomic by nature** — they aggregate observations — but
  each inference should cite the observations it rests on.

## 5. Interfaces

- **Ingest:** ticket context (manual paste or future tracker webhook), remote
  branch events.
- **Query:** by ticket ID — return session info, latest report, raw observations.
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
- **Session/report layout:** `<ticket_id>_bunker/` containing
  `literal_pi_session.json` + `report`; bunker name = unique ticket ID.
- **UX:** report-first, agent-invisible (§3.5).
- **Harness fit:** pi's extension model + single-file sessions + headless modes
  (`pi -p` / RPC / SDK) confirm the architecture; ordering semantics are
  documented/visualized on pi.dev.

## 10. Open questions

1. How ticket keys are matched in branch names (exact prefix? `feature/PROJ-123-*`?
   configurable regex per org?).
2. Multi-directory tickets: how the bunker composes observations across dirs —
   one report with per-dir sections, or per-dir observation sets merged at the
   inference layer?
3. Report sync transport: what "handled remotely" concretely is (shared git repo
   of reports? object store? the tracker itself as attachment?). Needs to work
   inside the classified enclave's constraints.
4. The report format: single human-readable file vs. `report/` dir with
   markdown + machine-readable JSON side by side (§5 currently implies both).
