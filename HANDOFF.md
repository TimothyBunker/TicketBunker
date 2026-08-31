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

- `tb init` is the one public connection command. Without a locator it connects
  an owner-created repository, publishes `ticketbunker/config` and
  `ticketbunker/data`, then writes `.ticketbunker.json`; with a committed
  locator it connects or refreshes a collaborator checkout. `tb setup` remains
  only as an alias.
- Every successful `tb init` installs checkout, commit, merge, and rewrite Git
  hooks. They preserve and chain existing hooks, record the newest ticket
  branch/HEAD/event/change time below `.git`, and never call a provider or
  start a worker. The user starts synthesis explicitly from the ticket state
  hub. That request queues the detached worker, refreshes deterministic
  observations, then follows the ordinary lint/anchor-gated agent and companion
  publication path. A post-agent HEAD check keeps superseded forms unpublished.
- `tb init` also establishes local agent readiness. The normal full release
  discovers its pinned bundled pi runtime, verifies every pi distribution file
  against the bundle manifest, adopts pi's selected provider/model, and calls
  pi's credential-safe auth probe. If login or selection is absent, interactive
  init opens pi once for `/login`, `/model`, and `/exit`, then checks again.
  Only harness/provider/model identifiers enter `~/.tbconfig`; credential
  values remain wholly owned by pi. `--core-only` is the explicit local opt-out.
- The selection lifecycle is explicit after init: `tb agent status`, `tb agent
  model`, and `tb agent login`. Model changes may instead use optional
  `--provider`/`--model` arguments on either `tb init` or `tb agent model`.
  Every route validates authentication before replacing the last working
  machine-local selection, and successful init prints the later change command.
- Remote Git probes are non-interactive and bounded. Transient DNS resolution
  is retried three times; DNS, timeout, authentication, access, and local
  fast-forward failures get distinct actionable rejections instead of raw Git
  tracebacks or manual `getent`/`git ls-remote` diagnosis.
- A fresh clone on a locator-less default branch fetches source refs, discovers
  one canonical remote locator, and safely switches a clean worktree to that
  tracking branch. Dirty and ambiguous cases stop with an exact next action.
  Live verification cloned Coal at default `main`; installed `tb init` found
  `origin/ecs`, switched to `ecs`, connected both companion checkouts, passed
  `tb doctor`, and listed all four ECS references. The scratch clone was removed.
- Remote Git and branch rules remain the sole authorization mechanism.
- Configuration is project composition, not mutable inheritance. New projects
  pin the then-current global field sets. Project edits do not rewrite global
  defaults or existing tickets.
- GitHub, GitLab, Jira, and custom integrations use the same editable
  TicketBunker-field ↔ provider-field matrix. Credentials live only in the
  configured local environment file, which is ignored.
- `tb config` opens the remote-backed editor. Its Repository page explains the
  three real steps: create an empty companion repo, connect it, then commit the
  source locator. Its Local settings page is explicitly machine-only. The
  included browser source editor is the default; indentation, tab width,
  wrapping, deterministic save cleanup, and the optional external command are
  stored in `~/.tbconfig` through `local.editor.update`. `tb config --editor`
  uses that same command and does not publish companion data. If an explicitly
  selected external editor cannot launch, source links fall back to the
  included editor instead of asking the user to configure a terminal launcher.
- `tb doctor [--json] [--offline]` checks Git identity, locator/ignore state,
  companion checkouts and remote heads, resolved configuration, ticket index,
  credential presence, hooks, and worktree cleanliness. Every failure names a
  next action. It writes only a local diagnostic ledger below `.git`.

### Tickets, indexes, and integrations

- Canonical tickets are `projects/<PROJECT>/tickets/<TICKET-ID>` on the data
  branch. `projects/<PROJECT>/tickets/index.json` contains bounded summary
  references, source URLs, digests, and counts—not report bodies.
- A fresh connection sparsely checks out indexes only. Tickets, Projects, search,
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
- GitHub Project #8 was live-synchronized into Coal on 23 August 2026: ECS-1
  through ECS-4 are active, assigned to `TimothyBunker`, committed in the
  companion index, and visible in both the personal and Project queries. The
  first full pull created three and refreshed ECS-4; an immediate second pull
  reported all four unchanged without moving the data revision. ECS-4's report
  remained intact. Coal `ecs` commit `e886c58` records the source-base locator
  hint; companion data commit `3a9ae5d` owns the four-ticket synchronization.

### Ticket-to-branch workflow

- `tb branch TICKET --name SHORT [--base BRANCH]` and the viewer's small Work
  plus dispatch the same `workflow.start` command.
- The command rejects a dirty source worktree, fetches origin, switches to the
  selected base, fast-forwards only, creates `<user>/<ticket>-<short-name>`, and
  switches to it. There is no reset, force, autostash, or hidden merge.
- An assignment/session is published only after Git succeeds. Existing
  branches use strict `workflow.attach`; a name without the bounded ticket key
  is rejected with a rename instruction.
- Opening a ticket renders cached companion data and local/already-fetched refs
  first. After the response, one background command refreshes companion state,
  fetches/prunes `origin`, and rebuilds Branches from local refs,
  remote-tracking refs, and explicit assignments. It works with uncommitted
  source files because it never pulls, rebases, switches, merges, resets, or
  stashes; offline opens keep the rendered ticket and provide a retry action.
  The background refresh never queues synthesis.
  A branch missing its computed comparison is labeled `Needs Git state`.
- The localhost Work POST is explicit and CSRF-token protected. Viewer pages
  remain bound to `127.0.0.1`, escape ticket/agent content, disable caching,
  framing, referrer propagation, and MIME sniffing, and never expose ticket,
  comment, report, or lifecycle mutation controls.
- Viewer failures are a product state, not a Python traceback. Git/workflow
  rejections show what stopped, what was preserved, and the exact next action;
  unexpected failures show a diagnostic ID while full detail stays local in
  `.git/ticketbunker/viewer-errors.jsonl`. Browser disconnects are silent.

### Viewer and agent reports

- Tickets lands on the current Git user's active tickets. Search and
  status/assignee/reporter/label/project filters persist in the URL; done and
  closed remain available without cluttering the default page. Projects uses
  the same reference query.
- A ticket page has explicit Ticket and Agent tabs. Ticket is a compact
  Jira-shaped read surface: project, title, mapped status, labels, source update
  time, rich description/acceptance criteria with nested list rendering,
  activity, useful details, and a Metadata disclosure for transport fields.
- One Lifecycle and Branches rail persists beside both tabs. Agent work owns
  only the center Agent surface. Combined is the mechanically generated master
  and separates each branch visibly;
  Who/What/Where/Why synthesis appears before each collapsible full evidence
  trail. Branch selection narrows the view. After any report exists, Report
  scope includes every resolved branch plus retained report, so a new local
  branch remains selectable with a direct Synthesize empty state before its
  first report. Before the first report, there is a direct empty state rather
  than a meaningless Combined selector.
- A compact state cluster sits beside the ticket title. Healthy Companion and
  Git refs are hidden; loading, cached, and stopped states surface as small
  dots. Synthesis stays visible as a dot plus `Updated <age>` when known, or its
  concise state when no successful update exists. Hover, focus, and click reveal
  the full detail and Synthesize, Update synthesis, Retry synthesis, or View log
  action. The localhost log page shows a bounded, escaped tail and the exact
  `.git/ticketbunker/automatic-agent/agent.log` path. The browser reloads only
  when ticket data or refs change.
- One local Pi coworker session exists per user and ticket across every branch.
  The viewer exposes it as Who/What/Where/Why chats with Combined-or-branch
  scope. Category actions and the persistent launcher are icon-only chat
  bubbles. The expanded surface is a right-side drawer whose arrow collapses
  it without implying that the session is closed. Topic, scope, draft,
  reference, and expanded state persist into the included source editor and
  back. Selecting synthesis or full-trail text attaches its bounded
  category/scope/branch quote to the next message and preserves that reference
  in local history. Pi
  receives only read/search tools. Raw chat stays below `.git`; a
  coworker reply can prepare an inert child Reader-brief proposal, and explicit
  acceptance rechecks digest/lint/anchors, updates the named children, then
  regenerates Combined. Rejected proposals show a concise not-saved outcome;
  exact lint/anchor findings remain in private local `diagnostics.jsonl` and do
  not leak into coworker replies or command errors. It cannot edit source, run
  Git mutations, or write Combined directly.
- `@[relative/path]:line` and verified inline-code `relative/path:line` report
  anchors use the same clickable source control in Combined, child synthesis,
  full trails, and coworker copy. The path remains selectable; a drag quotes
  it into the contextual coworker action without also opening it, while a
  path click or the adjacent Open button retains local-open behavior. The default is the
  included localhost source editor. `editor.read` confines paths and bounded UTF-8 content to the current
  checkout; `editor.save` requires the opened digest and atomically writes one
  file without staging or Git. A small clipboard control follows every source
  link; `editor.resolve` applies the same checkout guard and copies the absolute
  local path plus line without opening or reading the file. The editor toolbar
  names the originating ticket as its back destination. Selecting bounded
  source text can attach its guarded path, line range, text, and saved/draft
  state to the same ticket coworker. External editors remain an explicit option
  and fall back to the included editor when unavailable.
- A Tauri 2 desktop shell now gives the existing interface a distinct app
  window without creating a second frontend or backend. It starts the Python
  core as an owned child, loads only that exact loopback origin, exposes no
  native capabilities to ticket content, accepts guarded in-app routes such as
  the included source editor, and stops the core when its window closes.
- `tb view` prefers that app when installed and returns immediately;
  `tb view --browser` explicitly selects the prior detached browser viewer.
  If no app is installed, browser mode remains the automatic fallback. Its
  health/version replacement, page-presence shutdown, `--foreground`, and
  `--no-open` behavior are unchanged.
- Opening the app without CLI arguments now lands on its bundled Projects
  window. A native picker registers normalized Git roots through typed
  `application.*` commands; machine-local identity defaults from Git, and
  remembered projects show Ready, Join, or Connect. Join/Create visibly calls
  the existing `project.connect` backend, and Create requires a user-created
  companion repository URL. The privileged local window and unprivileged
  loopback ticket window are separate labels; only Projects receives the
  directory-dialog capability.
- First-run and Local settings now provide the complete agent path without
  opening Pi's terminal UI. The Projects window starts verified Pi in
  constrained RPC mode with only TicketBunker's pinned auth bridge, renders
  OAuth, device-code, select, and secret prompts itself, and sends replies
  directly from Rust to Pi. Pi alone writes credentials; Python receives only
  readiness and the selected provider/model. Closing setup kills the child,
  and opening/cancelling does not silently enable an agent. The explicit
  **Use without agent** route preserves non-agent work.
- Harness/provider/model identity is displayed separately from work authors.
  Unknown generator fields are omitted, never guessed.
- Report publication still passes lint plus anchor verification and stamps the
  verified branch/commit. Reports that predate newer branch development are
  visibly badged.

### Distribution

- Version 0.2.22 keeps the six-platform core release pipeline while the new
  native app bundle is integrated: Linux, macOS, and Windows on x64 and arm64.
  `tools/build_desktop.py` stages the frozen core under Tauri's target-triple
  sidecar name, verifies and embeds the complete Pi 0.84.2 distribution, and
  emits Debian/AppImage, app/DMG, or NSIS outputs. The app supplies its private
  resource root to the core, whose existing per-file integrity gate verifies
  Pi before use. Release construction also opens the staged Pi RPC runtime and
  requires a valid provider/model catalog from the pinned auth bridge. Native
  installers are flattened into `dist/releases/` with
  adjacent SHA-256 files. The Linux Debian (67 MB) and AppImage (136 MB) were
  built and launched live; the extracted Debian reported bundled Pi integrity
  verified.
- The CLI-first portable archives remain release assets with the same frozen
  core, Pi distribution, adapter, licenses, manifest, and install scripts.
- The wheel/pipx package and stdlib zipapp remain small core-only options. They
  now correctly include the pi adapter, version/checksum manifest, and license;
  an ordinary `tb init` automatically downloads, verifies, installs, and
  records the pinned platform runtime. `TB_AGENT_PI_BIN` remains only an
  explicit override; `--core-only` is the no-download choice.
- Frozen hooks pin the installed `tb` launcher. Detached one-file workers reset
  their PyInstaller environment so the parent's temporary extraction cleanup
  cannot strand a user-requested synthesis. Git hooks themselves now record
  staleness only and do not start that worker.

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
Synthesis tests separately cover explicit request coalescing, existing-hook
preservation/restoration, the gated worker path, frozen-worker isolation,
durable-input provider skipping, Pi-shaped CLI parsing, bounded malformed-draft
repair, fresh-clone session creation, nested-publication suppression, and a
real post-commit hook that records a change without publishing. Focused tests
also cover cached-first truth refresh, private coworker session reuse,
read-only Pi tools, selected synthesis-reference validation/persistence,
checkout-confined editor opening, detached viewer shutdown, and gated
child-only report updates, private proposal-rejection diagnostics, and shared
CLI/HTTP local editor selection, guarded built-in source saves, external-editor
fallback, stale-viewer replacement, compact shared-truth indicators, guarded
absolute-path copying, nested Markdown lists, and source-selection coworker
references. The current full suite is 200 tests, plus 7 Rust shell tests.

Browser QA covers desktop, tablet, and mobile sizes. Ticket/Agent switching,
the persistent rail, compact shared-truth indicators, coworker launcher/panel,
topic tabs, scope selection, Work panel, responsive stacking, and JavaScript
deep links are inspected. A headless-Chrome interaction check also selects
Combined synthesis, opens its contextual bubble, and verifies the quote reaches
the correct topic composer. The included editor is checked at desktop and
mobile widths; a real textarea selection opens the drawer with its source
reference, survives reload, collapses, and reopens without horizontal overflow.
This does not replace the HTTP/CSRF tests.

## Existing agent pipeline

The established agent loop remains green: ticket → branch → Git event marks
staleness → user presses Synthesize → queued bootstrap → compose → scaffold →
harness → lint/anchor-gated publish → master regeneration.
`tb merge` reconciles components through the same gates. `tb survey REPO --run`
publishes a gated repository survey. Git hooks record checkout, commit, merge,
and rebase changes without network or model work. The state-hub action is the
ordinary synthesis route; `tb run` remains the direct CLI path.

Pi/Codex synthesis now gets one bounded format-repair pass when the first
draft fails lint or anchor checks; the exact findings go back to the configured
harness and the repaired child still crosses the unchanged publish gates.
Agent-side `tb infer` commands no longer publish the companion checkout one by
one while the worker is active; the worker owns the single final publication.
The CLI subcommand name is separate from the harness `--cmd` option, fixing the
Pi-only `NoneType.startswith` crash, and fresh clones recreate ignored local
session directories before writing forms.

Pre-agent refresh now takes the base from the committed project locator (for
Coal, `ecs`) instead of silently defaulting new tickets to `main`. If a
collaborator has only the already-fetched remote-tracking base, observation
falls back to `origin/<base>` without changing the worktree. A stopped refresh
keeps its concrete reason and next Git action in synthesis state and its exact
diagnostic in View log; the generic “deterministic refresh failed” sentence is
no longer the only explanation.

Doctor and the ticket state hub distinguish explicit synthesis requests from
legacy automatic-worker history. An old `head-advanced`/Git-event failure can
no longer claim that the user requested synthesis: doctor reports tracking as
ready and the ticket remains **Not synthesized**. Only state carrying the
explicit `user-requested-synthesis` marker can become **Synthesis stopped** and
offer Retry/View log.

Codex and the bundled pi 0.84.2 runtime have both been live-verified. On 26
August 2026, managed pi with `openai-codex / gpt-5.6-sol` generated TB-1 in a
disposable clone on the first pass: all Reader Brief and full-trail claims
passed report lint, every `[real]` anchor resolved, and the published stamp
named the correct harness/provider/model. The deterministic 15-ticket product
exercise intentionally does not run an agent report.

## Remaining product work

1. Exercise GitHub API pagination and rate-limit failure copy with a larger
   disposable Project. The four-ticket live sync and hosted Git publication
   path are verified.
2. Warn when composed `tb.md` exceeds a documented token budget.
3. Add age-based archival policy for closed tickets without deleting data.

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
| Local source opener | `ticketbunker/editor.py` |
| Config editor | `ticketbunker/config_viewer.py` |
| Typed intake | `ticketbunker/handlers/` |
| Report gates | `ticketbunker/processors/reportlint.py`, `anchorcheck.py` |
| Harnesses | `ticketbunker/adapters/harness.py`, `ticketbunker/harness/pi/tb.ts` |
| Pi runtime/readiness | `ticketbunker/agent_runtime.py`, `ticketbunker/runtime/pi-runtime.json` |
| Releases | `tools/build_release.py`, `tools/ticketbunker.spec`, `.github/workflows/release.yml` |
| Product exercise | `tools/mock_product_loop.py` |
