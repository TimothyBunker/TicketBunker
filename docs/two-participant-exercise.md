# Two isolated participants

The Bunker collaboration path works between two independent work areas on one
machine. This exercise uses separate Python processes, settings, credentials,
SQLite databases, runtimes, Git configurations, and source clones for Alice
and Bob, with a real loopback HTTP service and one scratch bare Git origin.
It is process/data isolation, not a hostile-user OS sandbox or multi-OS test.

Run from the TicketBunker source checkout:

```sh
PYTHONPATH=. python3 tools/two_participant_loop.py --keep
```

The optional `--keep` retains the dummy work areas and prints their directory;
omit it for automatic temporary-fixture cleanup. The service stops at the end.
No credentials or invitation links are printed. The script never reads personal
TicketBunker settings, Git configuration, SSH keys, or provider tokens. Git's
real upload/receive-pack processes communicate with the scratch origin through
a test transport instead of a live SSH server. The deterministic stub replaces
the model provider; observations, configured Checks, lint, anchor verification,
and report publication still run through the product pipeline.

## Covered

- Cloning does not enroll a member; an invitation joins the correct shared
  Bunker while creating a distinct local database identity.
- Checkout mappings and credentials remain device-local; an invitation cannot
  be reused; a member cannot publish owner configuration.
- Concurrent branch creation on the same ticket accepts one shared revision.
  The rejected writer keeps its branch and can sync/attach to preserve both links.
- One participant receives another's gated reports; later reports update clean
  seeded runtime copies. Combined retains both child reports.
- Project Checks run on each participant's checked-out branch.
- Sync retains unpublished local drafts, and stale drafts cannot overwrite
  newer shared reports. Both source clones remain clean and companion-free.

These 17 checks are repeatable through `tests/test_two_participant_loop.py`.

## Finding: default ticket type crosses incompatible schemas

With no explicit type, `bunkerdb._ticket_reference` supplies `Task`.
`bunkerruntime.materialize` projects that value unchanged, but the legacy
ticket intake gate accepts lowercase `task`, `bug`, or `story`.

Reproduced through `workflow.start`: Git creates the branch and shared link
publication succeeds, then `handlers.assign_branch` rejects the local runtime
ticket. The desktop transport returns `internal_error`; the ledger records:

```text
'type' must be one of ('bug', 'task', 'story'), got 'Task'
```

The passing collaboration fixture explicitly uses `type: task` to continue
past this defect. A separate expected-failure test tracks the missing default
normalization. Production code has not been changed by this testing exercise.
The correction should reconcile the Bunker field contract with the runtime
adapter and validate local projection before committing Git/shared work.

## Not proven here

Native installer behavior, Windows/macOS rendering, real provider output
quality, Jira synchronization, TLS deployment, hostile-client report
verification, deletion propagation, and an interactive draft-conflict resolution
flow. This exercise does not settle the outstanding service verification trust
decision. It also does not test shared file conversations or membership removal.
