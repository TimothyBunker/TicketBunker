# Independent editor and sessions — proposal

**Proposed, not implemented.** This note reflects the new direction described by Tim: a Neovim-specialized local work environment that can use TicketBunker, but neither requires it nor exists solely to complete tickets. It does not redefine the existing ticket coworker as a coding agent.

## Product contract

Opening a repository and creating a work session should require neither a Bunker, a Project, a ticket, a remote server nor an agent login. Model-backed actions can require model authentication when invoked. TicketBunker is an optional context/publication provider.

The editor owns its buffers, navigation, work sessions and any independent work-agent policy. TicketBunker owns ticket facts, permissioned shared configuration and accepted ticket reports. Reading ticket context gives a work session no implicit right to change tracker truth, publish reports, edit files or execute commands.

## Dependency boundary

```mermaid
flowchart LR
    subgraph Local[Local work product]
        Editor[Neovim editor]
        Session[Local session service]
        Private[(Private session and workspace storage)]
        Repo[Checkout and worktree adapter]
        Editor --> Session
        Session --> Private
        Editor --> Repo
    end
    Provider[Optional TicketBunker adapter]
    Session -.->|explicit ticket references| Provider
    Provider --> TB[TicketBunker application API]
    TB --> Replica[(Local Bunker DB)]
    TB -.->|authenticated sync and publication| Server[Optional shared service]
    Session -.->|explicit report proposal through TB gates| Provider
```

The editor should not read `bunker.sqlite3` or runtime files directly. The adapter returns versioned context and accepts typed proposals. Similarly, TicketBunker should not need access to every private buffer or conversation. The stores shown are logical owners; deciding whether local session storage is a separate file/process comes after agreeing the contract.

## Identity model

```mermaid
erDiagram
    LOCAL_WORKSPACE ||--o{ CHECKOUT : includes
    LOCAL_WORKSPACE ||--o{ WORK_SESSION : contains
    WORK_SESSION ||--o{ SESSION_CHECKOUT : uses
    CHECKOUT ||--o{ SESSION_CHECKOUT : participates
    WORK_SESSION ||--o{ CONTEXT_LINK : optionally_references
    TICKET_REFERENCE ||--o{ CONTEXT_LINK : identifies
    WORK_SESSION ||--o{ MESSAGE : records
    MESSAGE ||--o{ SOURCE_REFERENCE : cites
```

- **Workspace ID:** opaque local identity, not a project/ticket key. Scope can contain multiple checkouts.
- **Checkout ID:** stable local record with path and Git/worktree identity. Canonical remote is descriptive/matching data, not an authorization credential. Moving/replacing a checkout requires explicit reconciliation.
- **Session ID:** opaque durable identity. A session may reference zero, one or multiple tickets and checkouts. Display title can change without moving its storage.
- **Ticket reference:** fully qualified `(provider/authority, Bunker ID, ticket ID)` plus selected context revision. Use remote Bunker identity when shared; maintain a mapping when a local Bunker is shared later. Never identify a ticket by `ECS-4` alone.
- **Source reference:** checkout ID + relative path + observed branch/commit and content digest + line/range. Mark unsaved-buffer references explicitly; line numbers alone do not remain valid across edits.
- **Context link:** an explicit relationship, not ownership. Removing it must not delete the local session; a revoked/unreachable provider marks it unavailable rather than preventing local work.

One session can relate to multiple tickets. This is not an instruction to merge today's per-ticket coworker histories automatically. Any migration must preserve origin and avoid cross-Bunker collisions.

## Proposed synchronization states

```mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> Prepared: User requests shared action
    Prepared --> Sending: Persist operation ID and payload
    Sending --> Accepted: Service returns durable receipt
    Sending --> OutcomeUnknown: Timeout or process interruption
    OutcomeUnknown --> Accepted: Replay same operation ID or query receipt
    OutcomeUnknown --> Conflict: Service rejects base revision
    Sending --> Conflict: Service rejects base revision
    Accepted --> ReplicaApplied: Apply acknowledged records locally
    ReplicaApplied --> Reconciled: Pull and apply complete remote coverage
    Conflict --> Draft: Preserve intent and request review
```

`Accepted` and `Reconciled` are different promises. An action may be saved remotely while other team changes have not yet reached this device. Source-buffer saves are separate from this state machine: they write the local checkout under file/branch conflict guards, not the shared Bunker.

## Smallest useful integration slice

1. Local session create/list/resume works with no TicketBunker installation or service.
2. Optional adapter can attach/detach a fully qualified ticket and retrieve a versioned context snapshot.
3. The editor can send a source selection with explicit checkout/commit/buffer provenance.
4. Report proposals use one existing-or-hardened TicketBunker publication gate, never direct Combined writes.
5. Leaving a ticket, disconnecting a Bunker, losing the network, or changing a display name does not destroy the session.

Acceptance scenarios: unticketed maintenance; one task spanning two checkouts; two unrelated Bunkers both containing `ECS-4`; branch switching while a cited buffer is dirty; revoked ticket access with local work still usable; exporting a ticket report without exporting a private chat.

Open decisions for a later implementation discussion: whether the local session API is embedded or a separate process; which messages can ever be shared; backup scope for private sessions; and whether a work-agent exists in the new editor at all. None needs to be settled by expanding TicketBunker's authority over source work.
