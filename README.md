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
tb init TB-1 --key PROJ-123 --assignee ann --reporter bob     # create a bunker
tb bootstrap TB-1_bunker --repo /path/to/repo --baseline main # run the observation pipeline
tb session TB-1_bunker --branch feat/PROJ-123 --user ann      # per-session config (user data injection)
tb compose TB-1_bunker --branch feat/PROJ-123                 # recompose tb.md for that session
tb scaffold TB-1_bunker --branch feat/PROJ-123 --out form.md  # pre-filled report form
tb infer TB-1_bunker --tag real --claim "..." --anchor src/x.py:10  # save a tagged inference
tb lint form.md                                               # check a report component
tb publish TB-1_bunker --branch feat/PROJ-123 --file form.md  # gate + accept + regen master
tb survey /path/to/repo                                       # seed or lint the project tbroot.md
tb status TB-1_bunker                                         # state, staleness, ledger health
```

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

## Status

Harness-neutral core implemented (bootstrap pipeline, context composition,
ledger, report lint gate, master regeneration). Not yet built: the backend
API service, git-hook triggers, and the pi extension adapter.
