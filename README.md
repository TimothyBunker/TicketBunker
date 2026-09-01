# TicketBunker

**An intermediate context layer between agile trackers (Jira, etc.) and git.**

Tickets rarely tell the whole story. People forget to log progress, don't name the
branch they worked on, don't say when they last merged main, or are reporting a
problem in *data produced by code they've never seen and aren't allowed to see*.
Anyone picking the ticket up later — especially across an unclass/class boundary —
has to reconstruct the history by hand: who touched what, where, and why.

TicketBunker closes that gap. Given a ticket's context, it:

1. Finds the branches related to that ticket (assuming branches are labeled by
   ticket key).
2. Runs a **deterministic, ordered pipeline** of git operations — diff against the
   reference branch, merge-base and merge history, separation of the author's work
   from incidental main merges — *before* any agent reasoning happens.
3. Lets an agent search the base branch for related code and context.
4. Reports back **who / what / where / why** for the ticket. A compact
   agent-written Reader brief prioritizes the people, work, paths, and purpose
   that matter most. The ticket viewer pairs each category's synthesis with a
   disclosure for its full tagged observation and inference trail.

Sessions are organized **by ticket**. Each session stores the session name and the
ticket information, retrievable on request by an authorized user.

The core discipline: **observations are atomic and mechanical** (git facts,
produced by the deterministic pipeline); **inferences are clearly labeled as
inferences** (agent conclusions built on those facts). A reader should always be
able to tell which is which.

See [DESIGN.md](DESIGN.md) for the architecture and the
[remote service contract](docs/remote-service-contract.md) for the host-neutral
collaboration boundary.
Open the self-contained [Bunker-first architecture diagram](docs/ticketbunker-api-architecture.html)
for the interactive system view.
The disposable [15-ticket, two-user product loop](docs/mock-product-loop.md)
records the current end-to-end behavior and measurements.

## Bunkers, Projects, and repositories

A **Bunker** is the configuration, membership, security, and synchronization
boundary. It contains a recursive tree of **Projects**. A Project is a work
scope, not a Git repository; repositories are attached explicitly after the
Bunker and Project exist.

Every Bunker gets one device-local SQLite database outside all source
repositories:

```
<TicketBunker data home>/
└── bunkers/
    ├── registry.sqlite3
    └── <LOCAL-BUNKER-ID>/bunker.sqlite3
```

The database stores the Project tree, shared repository identities, device-local
checkout mappings, configuration revisions, tickets, reports, migration
records, and remote replica state. Attaching a repository writes no locator,
database, ignore entry, or authorization hint into that repository.

A Bunker may remain local. Team collaboration adds an authenticated relationship
to a compatible TicketBunker service backed by infrastructure the team owns.
The app never connects directly to that remote database. Cloning a source
repository never joins a Bunker.

The harness (Pi) persists its own session data separately; the Bunker holds only
what TicketBunker owns.

## Installing

The normal download is the native TicketBunker app for your platform. It
contains the app, `tb`, and the pinned standalone Pi runtime, and needs neither
Python, Node, nor a separate Pi install:

1. Open [TicketBunker Releases](https://github.com/TimothyBunker/TicketBunker/releases).
2. Download the TicketBunker AppImage or Debian package on Linux, or the setup
   executable on Windows for your architecture. Its adjacent
   `.sha256` file lets managed installs verify the download first.
3. Install or open it, then choose **Create Bunker** or **Join Bunker**.

On first run, TicketBunker uses your Git identity when it is complete, then
offers agent setup. Choose a provider, sign in through a normal app dialog, and
select a model, or choose **Use without agent** to keep synthesis off on that
computer. Pi owns provider credentials; TicketBunker saves only the selected
provider and model. Change the choice later from your identity menu →
**Configure** beside Agent, or with `tb agent model` in the CLI.

The CLI-first portable archive remains beside the app for servers, air-gapped
transfer, and managed installs. It contains the same frozen core and Pi runtime.
Download its adjacent `.sha256` file, verify it, then run its installer:

```sh
# Linux/macOS, after checking the adjacent digest with sha256sum or shasum:
./install.sh                  # installs below ~/.local and puts tb in ~/.local/bin

# Windows PowerShell, after unpacking the .zip:
.\install.ps1                 # prints the one bin directory to add to PATH
```

For example, Linux can verify an archive with
`sha256sum -c ticketbunker-<version>-linux-x64.tar.gz.sha256`. In PowerShell,
compare `Get-FileHash .\ticketbunker-<version>-windows-x64.zip -Algorithm SHA256`
with the value in its `.sha256` file. The installer prints the installed path
and the only PATH change, if one is needed.

Linux and Windows are the first tested desktop targets on x64 and arm64. The
macOS build remains experimental until it has been verified on real hardware;
the portable core keeps the same platform-neutral command and data contracts.
The build verifies the official Pi archive checksum before extraction; the
installed `tb` verifies the bundled Pi distribution against its own release manifest.
Provider logins, API keys, OAuth tokens, and model weights are never bundled.

## Platform boundary

TicketBunker's Bunker, Project, ticket, report, Git, integration, and remote
service behavior is operating-system neutral. One machine-local platform
service selects the application-data directory, executable names, command
parsing, editor/terminal discovery, and detached-process flags. Linux and
Windows therefore run the same Python domain handlers and SQLite schema.

WSL is not a TicketBunker backend or repository type. When the core runs in
WSL, it uses the normal Linux platform service. A native shell talking to a
core in another local environment is a launcher/transport concern; WSL-specific
paths and repair instructions do not enter shared Bunker state or domain errors.

The stdlib-only core remains available for constrained or air-gapped setups.
These forms initially install TicketBunker without the large pi runtime:

```sh
pipx install .                # isolated Python CLI installation
# — or —
pip install .                 # installs the tb entry point in the active environment
# — or —
python3 tools/build_tb.py     # builds dist/tb, a Python 3.10+ zipapp
cp dist/tb ~/bin/tb
```

On the first ordinary `tb init`, a core installation automatically downloads
the pinned pi distribution for that machine, verifies the upstream SHA-256,
installs it below TicketBunker's local data directory, and records `pi_bin` in
`~/.tbconfig`. The user does not set `TB_AGENT_PI_BIN`; that variable is only
an explicit administrator/developer override. `tb init --core-only` prevents
the download and disables local agent execution intentionally.

Maintainers build a native app with `python3 tools/build_desktop.py` after
freezing `dist/tb`, or a portable archive with `python3
tools/build_release.py`. The native builder flattens each installer and its
checksum into `dist/releases/`; tag builds produce both formats on all six
targets.

## Usage

The desktop launcher is the primary onboarding path:

1. **Create Bunker** makes a local Bunker and its first Project without asking
   for a repository.
2. **Join Bunker** first shows the service and Bunker identity from the
   invitation, then authenticates and enrolls the user before creating a local
   replica.
3. Inside a Bunker, **Add project** builds the recursive work hierarchy.
4. **Attach repository** selects a local Git checkout, previews its canonical
   identity, and records the device path only after confirmation.
5. **Connect service** records an optional remote-service relationship; it
   never accepts a database connection string.

Existing companion-Git projects appear under **Migration** with **Import copy**.
Import is read-only with respect to both source and companion repositories and
records its source digest and imported counts. The old store does not remain a
second live writer.

The commands below are the compatibility and automation surface for the
existing ticket/report engine while its storage handlers are moved behind the
new Bunker database. They remain supported during migration, but `tb init` is
not how a new Bunker establishes membership:

```sh
# First create an empty Git repository on any host you control. TicketBunker
# connects to that URL; it never creates the hosting repository.
cd your-repo && tb init             # owner: connect a new companion; clone: use the committed locator
tb init --project TB --bunker-repo git@host:team/project-ticketbunker.git  # direct equivalent
tb init --provider openai-codex --model gpt-5.6-sol  # optional first-setup selection
tb init --core-only                 # explicit opt-out from local synthesis execution
tb agent status                     # active runtime, provider, model, and login state
tb agent model                      # open Pi's model picker; selection is saved after validation
tb agent model --provider openai-codex --model gpt-5.6-sol  # scriptable equivalent
tb agent login                      # open Pi login, then validate and adopt its selection
tb config                           # shared project config plus machine-local settings
tb config --editor builtin          # included source editor (the default)
tb config --editor 'code --goto {file}:{line}'  # optional external editor
tb doctor                           # readiness checks with exact next commands (--json / --offline available)
tb github OWNER/REPO#1267           # idempotently create/update one GitHub Issue
tb github --project 8 --project-owner OWNER  # or synchronize every Issue in a Project

tb ticket TB-1267                                  # opens $EDITOR on a ticket template (flags and --from FILE also work)
tb ticket --from-json ticket.json                  # complete provider-neutral object; ID may come from JSON
tb ticket --from-json -                            # paste one JSON object, then Ctrl-D; pipes use the same gate
tb comment TB-1267 --author ann --when 2026-08-10  # carry a tracker comment over verbatim (paste in $EDITOR or pipe stdin)
tb branch TB-1267 --name fourier-alg-impl          # refreshes the base, creates, and switches to Tim/TB-1267-fourier-alg-impl
tb branch TB-1267 --assign Tim/TB-1267-existing    # attach an existing convention-named branch

tb set TB-1267 --status in-review                  # move ticket fields through the same gate (status, priority, labels...)
tb list                                            # every ticket, grouped by status, priority+recency ordered
tb show TB-1267                                    # the whole ticket: fields, comments, development state, reports
tb checkout TB-1267                                # refresh companion data and compile the master
tb view                                            # open this project in the TicketBunker app
tb view --browser                                  # explicitly use the localhost browser fallback
tb view --browser --foreground --no-open           # attached diagnostic mode without opening a browser
tb open '@[ticketbunker/viewer.py]:1'               # open a verified local source reference in your editor
tb list --fetch --sort updated                     # include remote-only tickets; flat Jira-style sorting
tb comment TB-1267 --bulk                          # paste a whole tracker thread ('--- author [when] [source]' separators)
tb bootstrap TB-1267 --repo . --baseline main                  # run the observation pipeline
tb synthesize TB-1267                # same explicit synthesis request as the viewer button
tb run                             # direct CLI synthesis path; the viewer button is the ordinary route

# the pieces tb run drives, individually addressable by ticket ID:
tb session TB-1267 --branch Tim/TB-1267-fourier-alg-impl --data enclave=high
tb scaffold TB-1267 --branch Tim/TB-1267-fourier-alg-impl --out form.md
tb infer TB-1267 --tag real --claim "..." --anchor src/x.py:10
tb lint form.md && tb publish TB-1267 --branch Tim/TB-1267-fourier-alg-impl --file form.md
tb survey . && tb status TB-1267
```

`.ticketbunker.json`, `.tb/`, companion branches, and `tb init` belong to the
legacy storage adapter. They remain readable so existing users can verify and
import their data, but they are not the membership, authorization, discovery,
or synchronization contract for new Bunkers.

The legacy ticket viewer renders its imported/cached ticket data and local or
already-fetched Git refs immediately. Its safe source fetch does not touch the
worktree, so uncommitted files are preserved and no pull, rebase, switch, merge,
reset, or stash occurs. The Bunker-native ticket read model will retain these
invariants while replacing companion refresh with remote-service sync. Neither
path calls the synthesis agent merely because a ticket was opened.

`tb init` performs its own remote probe. It retries transient DNS resolution,
uses bounded non-interactive Git operations, and distinguishes DNS, timeout,
authentication, repository access, and local fast-forward failures. The old
`tb setup` spelling remains an alias. If a clone lands on a default branch that
predates the locator, `tb init` finds the single locator-bearing remote branch
and switches a clean worktree to it. Dirty or conflicting choices are refused.

## Storage and collaboration

The backend contract is TicketBunker's typed API and domain services. Local
mutations use the Bunker's SQLite transaction boundary. A shared Bunker syncs
revisioned, idempotent changes through an authenticated TicketBunker service;
the service owns membership and capability checks. The host may use SQLite,
PostgreSQL, or another transactional engine without exposing that choice to the
desktop app.

Git owns source commits, refs, branch protection, and code review. It does not
own TicketBunker membership, configuration, tickets, reports, or database
transport. Git bundles and JSON/markdown packages remain useful explicit
export/import formats, especially across air gaps, but they are not live state.

The Ticket fields page has separate project and Global defaults views. Global
changes create a new immutable field-set version for projects initialized in
the future; existing projects retain their pinned composition. Integrations
use an editable field-pair matrix: add or remove rows, then publish the complete
mapping atomically. GitHub Issues, GitLab Issues, and Jira Cloud start with
editable templates; custom integrations use the exact same mapping and local
token surfaces.

The Local settings page stays outside synchronized Bunker configuration. TicketBunker's
included browser editor is the default and needs no terminal or system editor.
That page controls spaces or tabs, tab width, line wrapping, trailing-whitespace
cleanup, and final-newline cleanup. Settings are saved only in `~/.tbconfig`.
An external command remains optional; `TB_EDITOR`, `VISUAL`/`EDITOR`, Git's
`core.editor`, automatic Vim/Nano detection, and Git for Windows' bundled Vim
are retained for that mode.

The older direct migration command remains available for companion-to-companion
moves. For the Bunker database cutover, prefer the desktop's read-only **Import
copy** action. The legacy command is:

```sh
tb migrate --project TB --bunker-repo git@host:team/project-ticketbunker.git
```

That legacy migration backs up the complete old `.tb/` below `.git/ticketbunker-backups/`,
publishes both companion branches, and only then removes the tracked legacy
paths for the source commit.

Every git command any of these run is written to the ledger automatically —
by the executing wrapper, never by the agent — so an unlogged command is
impossible by construction. Likewise, all information enters through typed,
validated intake handlers: an unlabeled or malformed payload is rejected at
the door, and the rejection is ledgered.

## Architecture

```
ticketbunker/
├── bunkerdb.py    # per-Bunker SQLite schema, transactions, hierarchy, imports
├── remote.py      # authenticated service protocol; no direct remote DB access
├── application.py # shared desktop/automation action registry
├── layout.py      # legacy companion layout and runtime artifact authority
├── api.py         # legacy ticket read model during storage-handler migration
├── types/         # information type registry + validation rules
├── handlers/      # typed intake: validate → write → ledger (the only write paths)
├── integrations/  # thin stdlib tracker transports; all writes dispatch typed commands
├── builders/      # artifact construction: tb.md, report form, master, fragments
├── processors/    # derivation: bootstrap stages, lint gates
├── adapters/      # external boundaries such as Git and the agent harness
├── contracts.py   # stage contracts and the marker-bracketing runner
├── ledger.py      # append-only execution ledger
└── cli.py         # thin orchestration over the above
```

The viewer lands on the current Git user's active tickets. Search and the
status/assignee/reporter/label/project filters persist in the URL; done and
closed tickets appear only when requested. Projects uses the same controls and
pages through lightweight ticket references before a specific ticket is
opened. Listing never composes report bodies or claims a ticket into the source
repository.

On a ticket, Lifecycle and Branches remain in one right rail while Ticket and
Agent switch the center view. Descriptions and reports retain nested ordered
and unordered Markdown lists. Branch state and the Work control therefore stay
in the same place while reading tracker details or agent evidence. Small status
indicators sit beside the ticket title. Healthy Companion and Git refs stay out
of sight; loading, cached, or stopped states surface as dots. Synthesis keeps a
dot plus its last update age, or its concise action state. Hover, focus, or click
reveals the full detail and **Synthesize**, **Update synthesis**, **Retry
synthesis**, or **View log** action. The log page shows a bounded, escaped tail
and the exact `.git/ticketbunker/automatic-agent/agent.log` path. Branches
without a generated comparison say **Needs Git state**, which names the missing
program output.

Agent defaults to **Combined**, the mechanically assembled master report, with
each child branch kept visibly separate. Once at least one report exists, Report
scope also lists resolved branches that do not have a report yet; selecting one
shows the direct Synthesize empty state instead of hiding the branch. Every Who,
What, Where, and Why
synthesis can open the same ticket coworker. The viewer presents four topic
conversations, but all scopes and branches use one underlying Pi session for
that local user and ticket. The coworker can read and search the repository,
explain the current report, and prepare a child Reader-brief proposal. It has
no shell, edit, write, or Git tool. Raw chat stays below
`.git/ticketbunker/coworker/`; accepting a proposal rechecks the current child,
lint, and real anchors before changing that child and regenerating Combined.
Rejected proposals show only a concise outcome in the conversation; exact
validator findings remain in the private local coworker diagnostics.
If remote synchronization stops, the accepted local report remains visible
with a clear sync-pending state and next action.

The category actions and persistent launcher are compact chat bubbles.
Highlighting part of a synthesis or its full trail reveals a contextual bubble;
pressing it attaches the selected excerpt, topic, report scope, and branch to the next local
coworker message, and the quote remains visible in conversation history. The
expanded coworker is a right-side drawer; its arrow collapses it without ending
the ticket session. Its topic, scope, draft, reference, and expanded state stay
with you when a linked source file opens.
Verified locations written as `@[relative/path]:line` are clickable. Verified
inline-code `relative/path:line` anchors in Combined, branch synthesis, and
full trails use that same control, matching coworker chat. Drag across the path
to quote it to the contextual coworker bubble; click it to open the file. The same
guarded operation is available as `tb open '@[relative/path]:line'`; it rejects
paths outside the checkout and opens the current branch's file in the included
source editor. A clipboard control after every source link resolves and
copies its absolute local path plus line through that same checkout guard. Save
checks that the file has not changed since it opened and
atomically writes only that file; it never stages or runs Git. Configure
indentation, wrapping, save cleanup, or an optional external editor on `tb
config` → Local settings. The editor toolbar links directly back to the ticket.
Select source text and use **Ask about selection** to attach its guarded path,
line range, and bounded text to the same coworker; unsaved selections are
labeled as drafts.

`tb view` prefers the installed TicketBunker desktop app and releases the
terminal immediately. The app loads the bundled interface and owns one private
typed core session for its lifetime; no local web server sits between the native
window and the Python core. If the desktop app is not installed, the command
serves those same compiled assets on loopback automatically. Use `--browser` to choose
that fallback explicitly, `--no-open` when you only want its loopback service, or
`--foreground` when debugging the server itself. Starting the app/viewer or
opening a source reference never publishes shared report data. If an optional
external editor cannot open, the source link falls back to the included editor.
After a TicketBunker update, browser mode detects an older detached worker and
opens the current viewer instead of reconnecting to stale behavior.

The desktop application lives in `desktop/` and is built with React, TypeScript,
Vite, and Tauri 2. Its
security and process-lifecycle contract is documented in
[`desktop/README.md`](desktop/README.md). Opening the app itself lands on
Bunkers. **Create Bunker** and **Join Bunker** are peer actions. Each Bunker row
shows its recursive Projects, local database, optional remote-service state,
repository attachments, and ticket counts. Source repositories enter only
through **Attach repository** inside an existing Project.
The local profile defaults from Git and can be changed from the identity button
without changing global Git configuration. The existing portable CLI release
continues to provide the same backend commands. Agent login and model choice
are available directly in the app; the app never exposes Pi's terminal UI.
The launcher keeps one private, bounded JSONL core session for its lifetime, so app
actions do not start a new Python process per click. Every response is matched
to its request and all behavior still passes through the shared typed action
registry.

**Configure** opens either Bunker defaults or the selected Project's pinned
configuration. New Projects copy the current defaults; later default changes do
not silently alter old Projects. Ticket fields and the complete GitHub, GitLab,
Jira, or custom integration matrix publish as revision-checked documents. Local
Bunkers are editable on the device. Remote replicas render their service-owned
snapshot read-only until an authenticated service sync publishes the change, so
a cached capability can never elevate itself.

The compatibility ticket view is available to Python integrations without going
through HTML or scraping CLI output:

```python
from ticketbunker.api import TicketCatalog, TicketDatabase, TicketQuery

tickets = TicketDatabase.discover(".")
ticket = tickets.get_ticket("TB-1267")
for branch in ticket.branches:
    print(branch.name, branch.state)

catalog = TicketCatalog(".")
page = catalog.query(TicketQuery(status="active", assignee="Tim", page=1))
for reference in page.tickets:
    print(reference.project, reference.id, reference.title)
```

This legacy query API is deliberately read-only while the Bunker-native read
model is completed. Mutations use shared command
envelopes—the CLI and included editor call the same handlers—so validation,
Git publication, and the ledger cannot be bypassed. Optional
`acceptance_criteria`, `metadata`, and provider-neutral `activity` entries
preserve tracker-shaped and project-specific information in `ticket.json`.
Activity kinds such as comments, history, work logs, logs, and notes are
mirrored from the configured source; the read-only viewer offers no parallel
comment or note authoring path.

If Git or a remote prevents a viewer action, the page says what stopped, what
state was preserved, and what to do next. It does not expose Python tracebacks
or raw Git stderr. Unexpected faults show a short diagnostic ID and keep their
details locally in `.git/ticketbunker/viewer-errors.jsonl`; closing or reloading
the browser during a response is treated as a normal disconnect.

### GitHub Issues and Projects

`tb github` is a packaged stdlib transport, not a second ticket implementation.
It reads one Issue or all Issue items in a Project, applies the editable GitHub
field matrix, and sends the normalized snapshots through the same typed batch
command used by programmatic callers. Save the token locally on the Integrations
page opened by `tb config`, or provide `GITHUB_ACCESS_TOKEN` in the environment:

```sh
tb github https://github.com/TimothyBunker/coal/issues/4 --project 8
tb github --project 8 --project-owner TimothyBunker
```

Repeated identical pulls are no-ops: no ticket ledger entry and no storage
revision. Changed source fields update while TicketBunker's branch assignments and
local extensions remain intact. `--fixture snapshot.json` uses the same path
offline for tests and demos. The JSON-only copy/paste wrapper remains under
[examples/github_projects](examples/github_projects/README.md).

## Running synthesis

The user chooses when synthesis is worth the model call. `tb init` installs
local Git hooks for checkout, commit, merge, and rebase events, but each hook
only records the newest ticket branch, HEAD, event, and change time below
local Git administrative state. It performs no network, observation, or agent
work. Opening a ticket may sync its Bunker and refresh Git refs without calling
the model.

Press **Synthesize** or **Update synthesis** in the ticket state hub to queue
the detached worker. It refreshes deterministic observations first, then runs
the same lint/anchor-gated publish path as every report. A claimed request stays
visibly **Synthesizing** until it finishes. The worker compares the branch's
durable ticket/Git/context fingerprint with the last successful publication,
so an already-current request does not spend another provider call. The last
result and exact log path are visible from the state hub; the file remains at
`.git/ticketbunker/automatic-agent/agent.log` for direct inspection.

`tb init` checks the shipped or automatically managed pi runtime, records its
resolved executable path, adopts pi's already-selected provider and model when
they are authenticated, and saves only those non-secret identifiers in
`~/.tbconfig`. If either login or selection is missing, an interactive init
opens pi once and asks for `/login`, `/model`, then `/exit`; init does not call
the setup complete until pi's credential-safe `auth check` passes. Optional
`--provider` and `--model` init arguments compose with the current selection,
so either, both, or neither can be supplied. Every successful init prints
`tb agent model` as the later change path. A replacement selection is validated
before it replaces the working one; `tb agent status --json` exposes the same
machine-local result for scripts. A
non-interactive init reports the exact interactive next step. `--core-only` is
the explicit local opt-out, and a later ordinary `tb init` enables setup again.

The requested worker reuses the harness stamped on an existing branch report.
For a first report it uses the pinned pi selection. `TB_AGENT_*` environment
values can still provide deliberate development/test overrides or a custom
command harness. Provider authentication remains pi's local responsibility;
TicketBunker never reads credential values.

`tb run` remains available as a diagnostic/manual recovery surface. It drives
the whole session: compose tb.md for the branch, scaffold
the pre-filled form, invoke the harness headless, publish the filled form
through the gate, regenerate the master. The publish stamp records the harness,
provider, and model values orchestration actually knows; the viewer shows those
separately from the people whose code or ticket work the report describes.

From inside your repo on a convention-named branch, `tb run` needs no
arguments: it reads the current branch, extracts the ticket ID, and finds
the bunker (in the current directory, or under `tb config --bunker-root`).

```sh
# reference harness: the pi bundled with TicketBunker
tb run --harness pi --provider anthropic --model "claude-*"   # on branch Tim/TB-1267-...
# or fully explicit:
tb run TB-1267_bunker --branch Tim/TB-1267-fourier-alg-impl --repo . \
    --harness pi --provider anthropic --model "claude-*"

# codex (OpenAI Codex CLI) — tb.md rides at the top of the prompt:
tb run --harness codex --codex-bin codex

# any other CLI (or a test stub) via the command profile:
tb run TB-1267_bunker --branch Tim/TB-1267-fourier-alg-impl --repo . \
    --harness command --cmd "python3 tools/stub_agent.py {form}"
```

The packaged pi adapter (`ticketbunker/harness/pi/tb.ts`) mirrors every agent tool call into the
bunker ledger and marks harness session start/end — structurally, via pi's
tool_call hook, never by the agent's own volition.

## Status

The full loop is implemented and verified with both a deterministic stub and
a live Codex session: init → tracker JSON intake → branch resolution → Git
event marks staleness → user-requested bootstrap/run (compose, scaffold, agent
session, gated publish, master regeneration) → sync. Git-event change tracking
and the gated root-surveyor runner are implemented.
Pi remains an optional reference harness; using it requires the `pi` binary and
that harness's own provider login, while Codex is independently supported and
records its identity on accepted reports.
