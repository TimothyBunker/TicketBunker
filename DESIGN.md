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

### 3.1 Session model

- **One session per ticket.** Sessions are keyed and named by ticket ID
  (e.g. `PROJ-1234`).
- Each session stores: session name, the ticket information supplied to it, the
  accumulated observations and inferences, and the report history.
- Ticket information enters the session either:
  - **manually** (a user pastes / uploads the ticket context — the known drawback,
    see §7), or
  - **automatically**, when new branches matching the ticket key appear on the
    remote but are not yet known to the service — the session ingests them and
    refreshes its observations.
- Session state is queryable by **authorized users only** (see §6).

Hosting/harness: **pi.dev** for session management. The specific reason for pi:
its harness supports **automated extension execution at session start**, which
lets us guarantee that a fixed sequence of actions runs *discretely and in order,
irrespective of the agent* (see §3.2). The agent cannot skip, reorder, or
reinterpret the bootstrap.

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

## 9. Open questions

1. pi.dev extension API specifics — confirm the session-start hook can enforce
   strict ordering and that its outputs are injectable as immutable context.
2. Report storage: inside the pi session vs. an external store keyed by ticket ID
   (matters for querying without waking a session).
3. How ticket keys are matched in branch names (exact prefix? `feature/PROJ-123-*`?
   configurable regex per org?).
4. Multi-branch tickets: one merged report or one report per branch?
