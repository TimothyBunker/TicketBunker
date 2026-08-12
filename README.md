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
4. Reports back **who / what / where / why** for the ticket, including a compiled
   list of relevant paths and code locations: changes, new work, old work, and
   candidate places to implement future work.

Sessions are organized **by ticket**. Each session stores the session name and the
ticket information, retrievable on request by an authorized user.

The core discipline: **observations are atomic and mechanical** (git facts,
produced by the deterministic pipeline); **inferences are clearly labeled as
inferences** (agent conclusions built on those facts). A reader should always be
able to tell which is which.

See [DESIGN.md](DESIGN.md) for the architecture.

## Layout of a bunker

One bunker per ticket, named by its unique ticket ID:

```
<ticket_id>_bunker/
├── tb.md            # composed session context — exactly what a session is fed
├── context/         # the fragments tb.md is composed from (modular, auditable)
├── observations/    # atomic git facts from the bootstrap pipeline (JSON)
├── ledger.jsonl     # every command + stage marker, logged structurally
└── reports/
    ├── master.md    # mechanically regenerated from children, digest-pinned
    └── branches/    # one agent-written component per branch
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
cd your-repo && tb init            # once per repo: creates .tb/ at the git root (fails outside a repo)
tb config --user Tim               # once per machine: team identifier (defaults from git user.name)

tb ticket TB-1267                                  # opens $EDITOR on a ticket template (flags and --from FILE also work)
tb comment TB-1267 --author ann --when 2026-08-10  # carry a tracker comment over verbatim (paste in $EDITOR or pipe stdin)
tb branch TB-1267 --name fourier-alg-impl --repo . # creates Tim/TB-1267-fourier-alg-impl
tb branch TB-1267 --assign legacy-fix --repo .     # or adopt an existing branch whose name carries no ticket ID

tb set TB-1267 --status in-review                  # move ticket fields through the same gate (status, priority, labels...)
tb list                                            # every ticket, grouped by status, priority+recency ordered
tb show TB-1267                                    # the whole ticket: fields, comments, development state, reports
tb checkout TB-1267                                # fetch + collect each branch's report component, compile the master
tb bootstrap TB-1267 --repo . --baseline main                  # run the observation pipeline
tb run                             # on the ticket branch: everything else is inferred

# the pieces tb run drives, individually addressable by ticket ID:
tb session TB-1267 --branch Tim/TB-1267-fourier-alg-impl --data enclave=high
tb scaffold TB-1267 --branch Tim/TB-1267-fourier-alg-impl --out form.md
tb infer TB-1267 --tag real --claim "..." --anchor src/x.py:10
tb lint form.md && tb publish TB-1267 --branch Tim/TB-1267-fourier-alg-impl --file form.md
tb survey . && tb status TB-1267
```

`.tb/` is committed with your repo — the bunker database shares the code's
remote and access boundary. A separate standalone database (`tb ticket
--root`, `tb sync`) is still supported.

## The backend is a git repo

The team's bunker database lives in a remote git repository and moves
through ordinary git — no service to stand up. `tb sync` mirrors git:
init if needed, add what `.tbignore` admits, commit, pull --rebase, push.
Teammates clone and read any bunker by its fixed layout. The default
`.tbignore` keeps harness session files and regenerable diff patches
local; customize it per team. JSONL ledgers are union-merged so
concurrent appenders never conflict.

Every git command any of these run is written to the ledger automatically —
by the executing wrapper, never by the agent — so an unlogged command is
impossible by construction. Likewise, all information enters through typed,
validated intake handlers: an unlabeled or malformed payload is rejected at
the door, and the rejection is ledgered.

## Architecture

```
ticketbunker/
├── layout.py      # the bunker layout, defined once — navigable like .git
├── types/         # information type registry + validation rules
├── handlers/      # typed intake: validate → write → ledger (the only write paths)
├── builders/      # artifact construction: tb.md, report form, master, fragments
├── processors/    # derivation: bootstrap stages, lint gates
├── adapters/      # external boundaries: git (harness + backend next)
├── contracts.py   # stage contracts and the marker-bracketing runner
├── ledger.py      # append-only execution ledger
└── cli.py         # thin orchestration over the above
```

## Running the agent

`tb run` drives the whole session: compose tb.md for the branch, scaffold
the pre-filled form, invoke the harness headless, publish the filled form
through the gate, regenerate the master.

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

Full loop implemented and verified end-to-end with a stub harness: init →
branch → bootstrap → run (compose, scaffold, agent session, gated publish,
master regen) → sync. The pi profile is wired and verified up to provider
login; a real-model run needs only credentials (`pi /login`). Not yet
built: git-hook triggers, the root-surveyor session runner.
