# Audit findings

Severity here means product/correctness priority, not a formal security rating. **P1** means repair before relying on this boundary for broader collaboration or another API client. **P2** means a design/operational gap to resolve before promising the corresponding capability.

## What is strong

- Bunker membership is separate from source Git access. Server capability checks resist local permission-cache tampering.
- Local paths and credentials are excluded from shared publication. Project configuration uses pinned composition, not accidental live inheritance.
- Record revision checks, atomic per-batch transactions, and server idempotency keys are present.
- Snapshot reads are transactionally consistent; local reconciliation protects against overlapping pulls.
- Report seeding preserves divergent local drafts using receipts. File saves and workspace recovery use explicit conflict guards.

The 42 existing tests run during this audit cover these boundaries in part. They are valuable infrastructure to extend, not evidence that every distributed failure case is covered.

## F1 — P1: A write receipt is incorrectly treated as a synchronized replica

**Reproduced.** Another participant creates ticket A; this device creates ticket B without first pulling A. Applying B's acknowledgement stores the latest service cursor and a fresh `last_synced_at`. Local state has one ticket, remote state has two, and both say `change-2`.

The user can be told they are current when they are not. Today's full pull repairs this; this test does **not** demonstrate permanent ticket loss. Reusing this cursor for a future incremental pull would risk skipping unseen records. Acceptance application also lacks an independent monotonic read-watermark rule.

Cause: [_record_acceptance](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/bunkerdb.py#L812) updates `sync_cursor`, `config_revision`, and `last_synced_at` after applying only the caller's proposal.

**Recommendation:** separate `last_observed_server_revision`, `last_write_receipt`, and `last_applied_snapshot_cursor/time`. Only applying a complete snapshot/delta advances the read watermark. Show "Your change saved" separately from "Shared data refreshed."

## F2 — P1: Large imports can fail after committing remotely; retries lose operation identity

**Reproduced.** A 201-ticket proposal encounters a conflict in its final batch. The first 200 changes remain committed remotely; the command raises before local apply, leaving zero tickets in this device's replica. Recreating the proposal generates new idempotency keys.

The batch transaction works correctly; the enclosing user action is not atomic. A dropped response after service commit has a related uncertain-outcome problem. This is not justification for blind write retries.

Sources: [_publish_changes](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/application.py#L906), [new proposal keys](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/bunkerdb.py#L2808), [server batch transaction](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/service/storage.py#L737).

**Recommendation:** durable local operation journal before transmission, stable operation/change IDs, per-batch receipts, and explicit recovery of an unknown result. Decide whether large imports are resumable partial operations or server-staged atomic imports. For a simple first release, resumable batches with truthful progress are reasonable. Pull accepted state after partial failure; preserve unpublished proposals separately. An operation journal does not require promising offline edits to every shared record.

## F3 — P1: The remote report boundary trusts client verification and permits combined-only writes

**Reproduced.** The same store entry point used by the HTTP changes route accepts a `combined` report containing plain untagged prose, no sections, no anchor evidence, and no accompanying child report.

The normal synthesis/coworker pipeline has report gates. This finding is about an authorized client bypassing that workflow through the shared API, not an unauthenticated write vulnerability. `_apply_report` checks identity, content size, repository existence and revision, but not report structure, evidence verification or derivation of Combined. The application explicitly assumes the gates already ran.

Sources: [application assumption](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/application.py#L1172), [service report acceptance](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/service/storage.py#L857), [normal combined builder](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/bunkerdb.py#L3004).

**Recommendation:** one supported child-report publication contract for every client. Perform structural/provenance validation at the service and derive Combined there, or validate a dependency manifest against accepted child revisions. A service without source access cannot independently prove an anchor: represent verifier identity, repository identity, commit and evidence digest explicitly, and distinguish client-attested verification from server verification. Do not label arbitrary text "verified" merely because a member wrote it. The exact attestation trust policy remains a design decision.

## F4 — P1: Valid growth can make a Bunker impossible to pull

**Reproduced.** Five individually accepted 2 MiB report records plus a small Combined record produce a 10,492,647-byte snapshot. The client rejects it at its 8,388,608-byte response ceiling. Existing replicas cannot refresh; a join also depends on a full snapshot. A large initial publication has the related request-size ceiling.

This is a deterministic protocol limit, not speculation about SQLite scale. The service's `after` endpoint still includes a full snapshot, so changing only the caller's cursor would not fix it.

Sources: [client ceiling](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/remote.py#L22), [full snapshot and changes-after](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/service/storage.py#L470), [request ceiling](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/service/server.py#L22).

**Recommendation:** bounded snapshot pages tied to one snapshot version, followed by true incremental synchronization. Keep large report bodies behind bounded detail/blob retrieval. Never apply a half-fetched snapshot as authoritative deletion. Add a test where total data exceeds 8 MiB while every page stays below the limit. Merely increasing the limit postpones the same failure.

Related performance risk, **not benchmarked here**: [list_tickets](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/bunkerdb.py#L1643) loads all ticket payloads before filtering and paginating in Python. A 25-row UI page is not a bounded database query. Move indexed filtering/paging into storage before marketing large-scale support.

## F5 — P1 for independent editor integration: Workspace/session identity is still ticket-coupled

**Confirmed by code inspection.** Every workspace operation first resolves a Bunker and repository binding. Coworker persistence is addressed by Git directory + project key + ticket key + sanitized display username, not an opaque session ID; the path does not include Bunker identity.

Consequences:

- A repository can be edited without selecting a ticket, but cannot use this workspace API without a Bunker attachment.
- Two Bunkers using the same project/ticket keys, checkout and username address the same coworker directory. This follows from the key function; a full cross-Bunker chat journey was not exercised.
- Changing user display name, moving a checkout, or opening another worktree does not have a first-class session relocation/identity policy.
- Reusing this model in Neovim would make a ticket optional in appearance but structurally mandatory for conversations.

Sources: [workspace identity](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/workspace.py#L18), [coworker path](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/coworker.py#L143).

**Recommendation:** see [independent editor and sessions](proposed-session-boundary.md). Give local sessions explicit IDs and optional fully scoped ticket references. TB stays a ticket/context/report service, not the owner of all editor activity.

## P2 gaps to define, not silently assume

1. **Deletion and retention.** Snapshot reconciliation removes absent bindings and supported empty Projects, but only upserts tickets, reports and configuration documents. General ticket/report deletion is explicitly unsupported in the current service contract. Before implementing it, define tombstones, retention, stale replicas and preservation of private drafts. Missing data in a malformed response must never mean deletion. Sources: [reconciliation](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/bunkerdb.py#L3219), [deletion contract](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/docs/remote-service-contract.md#L208).
2. **Backup/export/restore product flow.** The design describes portable exports and operator-owned backups; this audit did not find a complete, tested Bunker export/restore command path. Legacy companion import is not that round trip. Define separate portable shared-data exports and private device recovery backups; exclude credentials and local paths from portable bundles. Use consistent database backup operations, not an unqualified live copy of a WAL-backed local SQLite file. Test restore into a fresh data home. Sources: [design storage/backup assumptions](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/DESIGN.md#L2178), [local WAL mode](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/bunkerdb.py#L126).
3. **Provider snapshots and TB-owned annotations.** Branch assignments are preserved explicitly inside imported ticket payload JSON, but there is not a general field-provenance/ownership layer for all future annotations. Add an ownership rule for each new field instead of extending provider replacement exceptions indefinitely. Source: [ticket import merge](https://github.com/TimothyBunker/TicketBunker/blob/d6c8cb5/ticketbunker/bunkerdb.py#L2786).
4. **Operational collaboration.** Invitation/revision checks are implemented; account recovery, membership removal and join-request management still need a complete operator/user lifecycle. Do not equate an invite-only prototype with full enterprise identity administration. Existing offline copies cannot be retroactively erased by revocation.

## Recommended order

1. Repair F1/F2 together: separate read/write state and persist operation identity. Test a lost acknowledgement, a restart between remote acceptance/local apply, and batch 2 failure.
2. Close F3 before adding another report-writing client. Keep evidence verification responsibilities explicit.
3. Address F4 before bigger shared Bunkers: bounded bootstrap, report retrieval and real delta semantics.
4. Specify/export the local session contract in parallel with Neovim design, then implement F5 without rewriting the existing ticket engine wholesale.
5. Add backup/restore and explicit deletion/retention tests before making stronger durability/hosting promises.

Do not begin by replacing the database engine or moving every store into one database. First define each authority and the acknowledgements that move data between them.
