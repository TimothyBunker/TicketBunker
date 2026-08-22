# TicketBunker — agent handoff brief

Start with this file, then read `DESIGN.md` (the design authority) and
`README.md` (the user-facing contract). Work on branch
`claude/jira-git-context-agent-artex0` in `/home/tim/dev/TicketBunker`.

## Product in one paragraph

TicketBunker is a Git-owned context layer between agile trackers and source
work. Its default companion repository has protected configuration and shared
ticket data on separate branches; each source repository commits only a small
locator and ignores its local companion checkouts. Tickets preserve normalized
tracker facts, provider extensions, activity, local branch relationships,
deterministic Git observations, composed agent context, and gated agent reports.
The ticket viewer presents tracker ground truth separately from Agent work. The
stdlib-only Python package exposes the same commands to the CLI, localhost UI,
and programmatic integrations.

## Trust rules

- **observation** is a mechanical pipeline fact.
- **real** is an agent-located claim with a path/line anchor verified at the
  publish gate.
- **speculation** is interpretation ranked grounded, adjacent, or ungrounded.
- Tags upgrade only through verification; finding nothing is reportable.
- Typed intake is the only write route. Git execution is ledgered by the
  wrapper as an execution side effect. Report lint and anchor verification
  cannot be bypassed.
- Ours, pipeline, and agent data stay separate at rest and meet only during
  composition.

## Current product state

Everything below is covered by the test suite and deterministic product
exercise unless a live boundary is explicitly called out.

### Companion database and configuration

- `tb init` connects to an existing Git repository; it never creates a hosted
  repository. It publishes `ticketbunker/config` and `ticketbunker/data`, then
  writes `.ticketbunker.json`. A second init is refused.
- `tb setup` is the repeatable collaborator path. Companion refresh failures
  are typed, actionable rejections rather than raw Git tracebacks.
- Remote Git and branch rules remain the sole authorization mechanism.
- Configuration is project composition, not mutable inheritance. New projects
  pin the then-current global field sets. Project edits do not rewrite global
  defaults or existing tickets.
- GitHub, GitLab, Jira, and custom integrations use the same editable
  TicketBunker-field ↔ provider-field matrix. Credentials live only in the
  configured local environment file, which is ignored.
- `tb config` opens the remote-backed editor. Its Repository page explains the
  three real steps: create an empty companion repo, connect it, then commit the
  source locator.
- `tb doctor [--json] [--offline]` checks Git identity, locator/ignore state,
  companion checkouts and remote heads, resolved configuration, ticket index,
  credential presence, hooks, and worktree cleanliness. Every failure names a
  next action. It writes only a local diagnostic ledger below `.git`.

### Tickets, indexes, and integrations

- Canonical tickets are `projects/<PROJECT>/tickets/<TICKET-ID>` on the data
  branch. `projects/<PROJECT>/tickets/index.json` contains bounded summary
  references, source URLs, digests, and counts—not report bodies.
- A fresh setup sparsely checks out indexes only. Tickets, Projects, search,
  facets, and pagination work from those references. Opening or mutating one
  ticket hydrates only that ticket. Index-less older data branches fall back to
  a full checkout.
- `ticket.upsert` and `ticket.sync` are shared command handlers. Provider sync
  preserves TicketBunker-owned branch assignments and local extensions.
- Packaged `tb github ISSUE` and `tb github --project NUMBER
  --project-owner OWNER` normalize GitHub payloads through the configured field
  matrix and shared batch command. An identical pull creates no ledger event or
  companion commit. `--fixture FILE` drives the same path offline.
- Provider-neutral activity mirrors comments, history, worklogs, logs, notes,
  and extension-defined kinds. It is read-only in TicketBunker because the
  configured tracker remains that activity's ground truth.

### Ticket-to-branch workflow

- `tb branch TICKET --name SHORT [--base BRANCH]` and the viewer's small Work
  plus dispatch the same `workflow.start` command.
- The command rejects a dirty source worktree, fetches origin, switches to the
  selected base, fast-forwards only, creates `<user>/<ticket>-<short-name>`, and
  switches to it. There is no reset, force, autostash, or hidden merge.
- An assignment/session is published only after Git succeeds. Existing
  branches use strict `workflow.attach`; a name without the bounded ticket key
  is rejected with a rename instruction.
- The localhost Work POST is explicit and CSRF-token protected. Viewer pages
  remain bound to `127.0.0.1`, escape ticket/agent content, disable caching,
  framing, referrer propagation, and MIME sniffing, and never expose ticket,
  comment, report, or lifecycle mutation controls.

### Viewer and agent reports

- Tickets lands on the current Git user's active tickets. Search and
  status/assignee/reporter/label/project filters persist in the URL; done and
  closed remain available without cluttering the default page. Projects uses
  the same reference query.
- A ticket page has explicit Ticket and Agent tabs. Ticket is a compact
  Jira-shaped read surface: project, title, mapped status, labels, source update
  time, rich description/acceptance criteria, activity, useful details, and a
  Metadata disclosure for transport fields.
- Agent shows Lifecycle, Branches, and Agent work. Master separates each branch
  visibly; Who/What/Where/Why synthesis appears before each collapsible full
  evidence trail. Branch selection narrows the view. Before the first report,
  there is a direct empty state rather than a meaningless Master selector.
- Harness/provider/model identity is displayed separately from work authors.
  Unknown generator fields are omitted, never guessed.
- Report publication still passes lint plus anchor verification and stamps the
  verified branch/commit. Reports that predate newer branch development are
  visibly badged.

### Deterministic product exercise

Run:

```sh
python3 tools/mock_product_loop.py
python3 tools/mock_product_loop.py --json
```

The script creates disposable local source and companion remotes, never touches
the real voxel repository, and exercises 15 GitHub-shaped tickets with two Git
users. It proves first sync, revision-level no-op sync, a three-ticket update,
active/faceted catalog queries, zero-ticket sparse setup, one-ticket
fetch-on-open, separate convention-named branches, remote reconciliation, two
clean doctor reports, and the Work UI. Measurements and explicit live-boundary
caveats are in `docs/mock-product-loop.md`; the scenario is also a unit test.

Browser QA was performed in headless Google Chrome at desktop and mobile sizes.
The Work panel, base selector, readiness state, responsive stacking, and
JavaScript deep link were inspected. This does not replace the HTTP/CSRF tests.

## Existing agent pipeline

The established agent loop remains green: ticket → branch → bootstrap → compose
→ scaffold → harness → lint/anchor-gated publish → master regeneration.
`tb merge` reconciles components through the same gates. `tb survey REPO --run`
publishes a gated repository survey. Git hooks refresh deterministic
observations/master state without launching paid agent sessions.

Codex has been live-verified. The pi adapter loads, but its provider login is an
external prerequisite; do not describe a Codex-backed pi profile as pi-provider
verification. The deterministic 15-ticket product exercise intentionally does
not run an agent report.

## Remaining product work

1. Live-test GitHub sync with a disposable Project and real local credentials,
   including hosted branch rules and API pagination/rate-limit failure copy.
2. Verify the pi harness past its provider login and grade every `[real]`
   anchor, speculation rank, and cold-read Where/Why result.
3. Warn when composed `tb.md` exceeds a documented token budget.
4. Add age-based archival policy for closed tickets without deleting data.
5. Decide the cost-aware policy for rerunning stale agent reports. Discuss it
   with Tim before implementing; per-commit agent sessions are not acceptable.

## Non-negotiable implementation rules

- Stdlib-only core; no package dependency additions.
- Update `DESIGN.md` in the same commit as meaningful behavior changes.
- Resolve every bunker path through `ticketbunker/layout.py`.
- Never weaken typed intake, structural ledgering, report lint, anchor checking,
  or provenance separation to make a test pass.
- User failures must say what happened and what to do next; a raw traceback is
  a bug.
- Treat this repository's own `.tb/` and the real voxel repo at
  `/mnt/c/Users/Tim/Desktop/voxel-engine-starter` as real data. Use disposable
  directories for experiments unless Tim explicitly requests a live action.
- Use focused commits and push this branch after each verified behavior slice.
- After package changes, run `pip install --force-reinstall .` before testing the
  installed `tb`, or explicitly use `PYTHONPATH=.` for source-tree tests.

## Key paths

| Concern | Path |
|---|---|
| Design authority | `DESIGN.md` |
| Layout | `ticketbunker/layout.py` |
| Shared commands | `ticketbunker/commands.py`, `ticketcommands.py` |
| Companion storage | `ticketbunker/project.py` |
| Committed references | `ticketbunker/ticketindex.py` |
| GitHub transport | `ticketbunker/integrations/github.py` |
| Branch workflow | `ticketbunker/workflow.py` |
| Diagnostics | `ticketbunker/doctor.py` |
| Read model | `ticketbunker/api.py` |
| Ticket viewer | `ticketbunker/viewer.py` |
| Config editor | `ticketbunker/config_viewer.py` |
| Typed intake | `ticketbunker/handlers/` |
| Report gates | `ticketbunker/processors/reportlint.py`, `anchorcheck.py` |
| Harnesses | `ticketbunker/adapters/harness.py`, `harness/pi/tb.ts` |
| Product exercise | `tools/mock_product_loop.py` |
