# TicketBunker architecture audit

Date: 2026-09-19. Audited source: `d6c8cb5` on `claude/jira-git-context-agent-artex0` (equal to the fetched remote branch).

Follow-up implementation: see [Repair progress](repair-progress.md). The findings
preserve the original audit evidence, not a claim that every failure still
reproduces after the repairs.

**The database engine is not the main weakness. The transitions between authoritative data, replicas, working reports, and private sessions are.** Changing SQLite to PostgreSQL would not repair the reproduced failures.

Start here:

1. [Current architecture](current-architecture.md) — ownership, physical stores, and today's write/read flow.
2. [Audit findings](findings.md) — ranked issues, evidence, reproducible results, and recommended ordering.
3. [Independent editor and sessions](proposed-session-boundary.md) — proposed boundary for the Neovim editor; deliberately separate from implemented behavior.

These notes use ordinary Mermaid fences supported by Obsidian. Open the Current architecture note in Reading view. No proprietary diagram format or plugin-specific syntax is required. The supplied `D:\Apps\TicketBunker\TicketBunker` folder is inside the vault whose `.obsidian` directory is at `D:\Apps\TicketBunker`.

## Scope and confidence

This is a backend architecture/data-flow audit, not a complete security, performance, visual, or native-editor audit. It reviews database ownership, synchronization, publication trust, workspace/session identity, and recovery boundaries. It does not implement the proposals or change DESIGN.md. The separate Neovim project's code was not supplied or inspected.

The findings distinguish reproduced failures from code-level gaps and future design recommendations. No real Bunker, tracker, repository worktree, credentials, model, or remote database was modified. An isolated test service used ephemeral localhost sockets. Existing unrelated worktree files were preserved.

## Verification

Historical reproduction at the audited commit (these assertions should stop
passing once the bugs are fixed):

```sh
PYTHONPATH=. python3 docs/audits/2026-09-19/probe_database_flow.py
```

Current regression verification from the TicketBunker source root:

```sh
PYTHONPATH=.:tests python3 -m unittest test_database_flow -v
PYTHONPATH=.:tests python3 -m unittest test_bunkerdb test_service test_sync_recovery test_workspace_membership -q
```

The existing suite passed **42 tests in 23.076 seconds**. The sandbox initially prohibited test sockets; the successful rerun had explicit permission for the disposable localhost service. That initial failure was environmental, not a product defect.

The audit probe reproduced all four described failures. It uses the real SQLite/service/application logic with a transport seam, not a live hosted service; the response-size check uses the real HTTP adapter with a supplied byte stream. All databases are temporary and removed afterward. Its assertions document undesirable behavior at the audited commit; it is intentionally **not** a regression test asserting desired product behavior.

```json
{
  "write_receipt_vs_read_cursor": {
    "local_tickets": 1,
    "remote_tickets": 2,
    "both_cursors": "change-2",
    "last_synced_at_set": true
  },
  "multi_batch_partial_commit": {
    "failure": "ticket_conflict",
    "new_remote_tickets_committed_before_failure": 200,
    "local_tickets": 0,
    "retry_generates_new_idempotency_keys": true
  },
  "report_trust_and_snapshot_size": {
    "unvalidated_combined_report_accepted": true,
    "snapshot_bytes": 10492647,
    "client_limit_bytes": 8388608,
    "pull_error": "The TicketBunker server response was too large."
  }
}
```

Source links in these notes are pinned to the audited Git commit so they remain useful after copying into the vault. Proposed diagrams are discussion material, not a replacement for DESIGN.md.
