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

See [DESIGN.md](DESIGN.md) for the architecture.
The disposable [15-ticket, two-user product loop](docs/mock-product-loop.md)
records the current end-to-end behavior and measurements.

## Layout of a bunker

The source repo commits a locator and ignores two local companion checkouts.
One bunker per ticket lives on the companion data branch:

```
.ticketbunker.json
.tb/
├── config/         # local checkout of ticketbunker/config
└── data/projects/<PROJECT>/tickets/
    ├── index.json       # bounded committed references; enough for search/listing
    └── <TICKET-ID>/     # hydrated only when this ticket is opened or changed
        ├── tb.md        # composed session context — exactly what a session is fed
        ├── context/     # the fragments tb.md is composed from (modular, auditable)
        ├── observations/# atomic git facts from the bootstrap pipeline (JSON)
        ├── ledger.jsonl # every command + stage marker, logged structurally
        └── reports/
            ├── master.md # mechanically regenerated from children, digest-pinned
            └── branches/ # one agent-written component per branch
```

The harness (pi) persists its own session data separately; the bunker holds
only what TicketBunker owns.

## Installing

The normal download is one platform archive containing both `tb` and the
pinned standalone pi runtime. It needs neither Node nor a separate pi install:

1. Open [TicketBunker Releases](https://github.com/TimothyBunker/TicketBunker/releases).
2. Download the archive whose name matches your machine: `linux`, `darwin`
   (macOS), or `windows`, followed by `x64` or `arm64`.
3. Download its adjacent `.sha256` file and verify it.
4. Unpack the archive and run the installer inside it:

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

Release bundles cover Linux, macOS, and Windows on x64 and arm64. The build
verifies the official pi archive checksum before extraction; the installed
`tb` verifies the bundled pi executable against its own release manifest.
Provider logins, API keys, OAuth tokens, and model weights are never bundled.

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

Maintainers build a full archive with `python3 tools/build_release.py`; tag
builds produce all six platform bundles and SHA-256 sidecars automatically.

## Usage

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
tb view                                            # open the localhost viewer in the background; it stops after the last tab closes
tb view --foreground --no-open                     # attached diagnostic mode without opening a browser
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

`.ticketbunker.json` is committed with the source. `.tb/` is an ignored local
checkout/cache; canonical configuration and data live on separate branches of
the companion repository. A standalone database (`tb ticket --root`, `tb
sync`) and embedded legacy bunkers remain supported. On every later clone,
`tb init` reads the locator and connects automatically. A fresh connection checks
out only committed ticket indexes; opening or changing a ticket fetches that
one bunker without copying every ticket and report.

Opening a ticket renders cached companion data and local or already-fetched
Git refs immediately. After the response, TicketBunker refreshes the companion
checkout and runs a safe `git fetch --prune origin` in the source repository in
the background. It then rebuilds Branches from matching local branches,
remote-tracking branches, and explicit assignments. Fetch does not touch the
worktree, so uncommitted files are preserved and no pull, rebase, switch,
merge, reset, or stash occurs. Offline opens keep the ticket on screen with
local and cached refs plus a direct retry action. This background refresh never
calls the synthesis agent.

`tb init` performs its own remote probe. It retries transient DNS resolution,
uses bounded non-interactive Git operations, and distinguishes DNS, timeout,
authentication, repository access, and local fast-forward failures. The old
`tb setup` spelling remains an alias. If a clone lands on a default branch that
predates the locator, `tb init` finds the single locator-bearing remote branch
and switches a clean worktree to it. Dirty or conflicting choices are refused.

## The backend is a git repo

The team's bunker database lives in a companion git repository — no service to
stand up. `ticketbunker/config` holds field sets and project/provider mapping;
`ticketbunker/data` holds tickets, observations, ledgers, and reports. Each
successful data mutation commits, reconciles, and pushes through ordinary Git.
The repository itself must already exist and be accessible when `tb init`
connects it; an empty repository is valid. TicketBunker creates only its owned
branches, never a GitHub, GitLab, Jira, or other hosting repository.
JSONL ledgers are union-merged; generated masters are regenerated from their
children. Remote and branch permissions are the authority, including who may
publish configuration.

The Ticket fields page has separate project and Global defaults views. Global
changes create a new immutable field-set version for projects initialized in
the future; existing projects retain their pinned composition. Integrations
use an editable field-pair matrix: add or remove rows, then publish the complete
mapping atomically. GitHub Issues, GitLab Issues, and Jira Cloud start with
editable templates; custom integrations use the exact same mapping and local
token surfaces.

The Local settings page stays outside the companion repository. TicketBunker's
included browser editor is the default and needs no terminal or system editor.
That page controls spaces or tabs, tab width, line wrapping, trailing-whitespace
cleanup, and final-newline cleanup. Settings are saved only in `~/.tbconfig`.
An external command remains optional; `TB_EDITOR`, `VISUAL`/`EDITOR`, Git's
`core.editor`, automatic Vim/Nano detection, and Git for Windows' bundled Vim
are retained for that mode.

To move an old embedded database safely:

```sh
tb migrate --project TB --bunker-repo git@host:team/project-ticketbunker.git
```

Migration backs up the complete old `.tb/` below `.git/ticketbunker-backups/`,
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
├── layout.py      # the bunker layout, defined once — navigable like .git
├── api.py         # public read model: ticket + branches + reports, no cache
├── types/         # information type registry + validation rules
├── handlers/      # typed intake: validate → write → ledger (the only write paths)
├── integrations/  # thin stdlib tracker transports; all writes dispatch typed commands
├── builders/      # artifact construction: tb.md, report form, master, fragments
├── processors/    # derivation: bootstrap stages, lint gates
├── adapters/      # external boundaries: git (harness + backend next)
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
If companion publication stops, the accepted local report remains visible as
**Companion publish pending** with its next action.

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
localhost editor. A clipboard control after every source link resolves and
copies its absolute local path plus line through that same checkout guard. Save
checks that the file has not changed since it opened and
atomically writes only that file; it never stages or runs Git. Configure
indentation, wrapping, save cleanup, or an optional external editor on `tb
config` → Local settings. The editor toolbar links directly back to the ticket.
Select source text and use **Ask about selection** to attach its guarded path,
line range, and bounded text to the same coworker; unsaved selections are
labeled as drafts.

`tb view` opens the browser by default and releases the terminal after the
localhost helper is ready. The helper follows live browser tabs and stops a few
seconds after the final tab closes. Use `--no-open` when you only want the URL,
or `--foreground` when debugging the server itself. Starting the viewer or
opening a source reference never publishes companion data. If an optional
external editor cannot open, the source link falls back to the included editor.
After a TicketBunker update, `tb view` detects an older detached worker and
opens the current viewer instead of reconnecting to stale behavior.

The same repository-owned view is available to Python integrations without
going through HTML or scraping CLI output:

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

This query API is deliberately read-only. Mutations use shared command
envelopes—the CLI and localhost editor call the same handlers—so validation,
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

Repeated identical pulls are no-ops: no ticket ledger entry and no companion
commit. Changed source fields update while TicketBunker's branch assignments and
local extensions remain intact. `--fixture snapshot.json` uses the same path
offline for tests and demos. The JSON-only copy/paste wrapper remains under
[examples/github_projects](examples/github_projects/README.md).

## Running synthesis

The user chooses when synthesis is worth the model call. `tb init` installs
local Git hooks for checkout, commit, merge, and rebase events, but each hook
only records the newest ticket branch, HEAD, event, and change time below
`.git`. It performs no network, observation, or agent work. Opening a ticket
also refreshes companion data and Git refs without calling the model.

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
