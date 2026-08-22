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

The core is a stdlib-only Python package. Two ways to get a plain `tb`
command on your PATH — no `python something.py` calls in daily use:

```sh
pip install .                 # installs the `tb` entry point
# — or, where pip isn't an option —
python3 tools/build_tb.py     # builds dist/tb, a single self-contained file
cp dist/tb ~/bin/tb           # drop it anywhere on PATH; runs on any Python 3.10+
```

## Usage

```sh
# First create an empty Git repository on any host you control. TicketBunker
# connects to that URL; it never creates the hosting repository.
cd your-repo && tb init             # once: opens companion repository setup
tb init --project TB --bunker-repo git@host:team/project-ticketbunker.git  # direct equivalent
tb setup                            # every later clone: connect from .ticketbunker.json
tb config                           # remote-backed identity, storage, fields, integrations editor
tb github OWNER/REPO#1267           # idempotently create/update one GitHub Issue
tb github --project 8 --project-owner OWNER  # or synchronize every Issue in a Project

tb ticket TB-1267                                  # opens $EDITOR on a ticket template (flags and --from FILE also work)
tb ticket --from-json ticket.json                  # complete provider-neutral object; ID may come from JSON
tb ticket --from-json -                            # paste one JSON object, then Ctrl-D; pipes use the same gate
tb comment TB-1267 --author ann --when 2026-08-10  # carry a tracker comment over verbatim (paste in $EDITOR or pipe stdin)
tb branch TB-1267 --name fourier-alg-impl --repo . # creates Tim/TB-1267-fourier-alg-impl
tb branch TB-1267 --assign legacy-fix --repo .     # or adopt an existing branch whose name carries no ticket ID

tb set TB-1267 --status in-review                  # move ticket fields through the same gate (status, priority, labels...)
tb list                                            # every ticket, grouped by status, priority+recency ordered
tb show TB-1267                                    # the whole ticket: fields, comments, development state, reports
tb checkout TB-1267                                # refresh companion data and compile the master
tb view                                            # read-only Tickets / Projects / Ticket / Agent viewer at http://127.0.0.1:8377
tb hooks --install                                 # git hooks: observations + master refresh on every commit/merge
tb list --fetch --sort updated                     # include remote-only tickets; flat Jira-style sorting
tb comment TB-1267 --bulk                          # paste a whole tracker thread ('--- author [when] [source]' separators)
tb bootstrap TB-1267 --repo . --baseline main                  # run the observation pipeline
tb run                             # on the ticket branch: everything else is inferred

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
sync`) and embedded legacy bunkers remain supported. A fresh `tb setup` checks
out only committed ticket indexes; opening or changing a ticket fetches that
one bunker without copying every ticket and report.

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

## Running the agent

`tb run` drives the whole session: compose tb.md for the branch, scaffold
the pre-filled form, invoke the harness headless, publish the filled form
through the gate, regenerate the master. The publish stamp records the harness,
provider, and model values orchestration actually knows; the viewer shows those
separately from the people whose code or ticket work the report describes.

From inside your repo on a convention-named branch, `tb run` needs no
arguments: it reads the current branch, extracts the ticket ID, and finds
the bunker (in the current directory, or under `tb config --bunker-root`).

```sh
# reference harness: pi (https://pi.dev) — npm i -g @earendil-works/pi-coding-agent; pi /login once
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

The pi adapter (harness/pi/tb.ts) mirrors every agent tool call into the
bunker ledger and marks harness session start/end — structurally, via pi's
tool_call hook, never by the agent's own volition.

## Status

The full loop is implemented and verified with both a deterministic stub and
a live Codex session: init → tracker JSON intake → branch resolution → bootstrap
→ run (compose, scaffold, agent session, gated publish, master regeneration) →
sync. Git refresh hooks and the gated root-surveyor runner are implemented.
Pi remains an optional reference harness; using it requires the `pi` binary and
that harness's own provider login, while Codex is independently supported and
records its identity on accepted reports.
