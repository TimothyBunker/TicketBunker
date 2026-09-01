# TicketBunker remote service contract

This is the host-neutral collaboration boundary for a shared Bunker. A desktop
client connects to this API, never to the service's physical database. Hosts
may use SQLite, PostgreSQL, or another transactional engine while preserving
the same protocol.

The current client contract is `/api/v1`. JSON responses are bounded to 8 MiB.
Production origins require HTTPS; plain HTTP is accepted only for localhost
development.

## Authentication and enrollment

Source-repository access is not TicketBunker authentication. A clone, source
remote, or Git identity must never grant Bunker membership.

An invitation has this transport form:

```text
ticketbunker://join?server=https%3A%2F%2Ftickets.example.com&bunker=engineering&invite=<one-time-value>
```

The desktop parses it locally and displays only `server` and `bunker`. The
one-time value never appears in application state or child-process arguments.
It is sent only during enrollment.

Before redemption, the native application authenticates the user with the
selected service and stores the resulting credential in the operating-system
vault. The HTTP adapter receives the credential only as an ephemeral bearer,
plus its machine-local vault reference. The service returns the authorized
snapshot; it never returns or invents a device credential reference.

`POST /api/v1/invitations/redeem`

```json
{
  "bunker_id": "engineering",
  "invite": "one-time-value"
}
```

The request requires the authenticated user's bearer credential. A successful
response is:

```json
{
  "snapshot": {
    "bunker": {"id": "engineering", "name": "Engineering"},
    "projects": [],
    "repository_bindings": [],
    "config_documents": [],
    "tickets": [],
    "reports": [],
    "members": [{"id": "user-7", "display_name": "Tim", "role": "member"}],
    "current_member_id": "user-7",
    "capabilities": ["tickets:read"],
    "revision": "42",
    "cursor": "change-104"
  }
}
```

The service must validate all three facts before returning this snapshot:

1. the authenticated user is valid;
2. the invitation is valid, unexpired, and scoped to the named Bunker;
3. enrollment grants that user the returned membership and capabilities.

The client creates its local SQLite replica only after all three checks succeed
and the snapshot validates. A failed or malformed enrollment leaves no replica.

## Endpoints

`GET /api/v1/capabilities`

Returns protocol compatibility and supported service features. It must not
expose tenant or Bunker data before authentication.

`POST /api/v1/invitations/inspect`

Optionally returns display-safe invitation information. The desktop's first
review remains local, so this endpoint is not required to reveal the invite
bearer or to establish membership.

`POST /api/v1/invitations/redeem`

Authenticates, authorizes, consumes the invitation, and returns the initial
snapshot shown above. Redemption is idempotent for the same authenticated user
and enrollment attempt; it must not create duplicate memberships.

`GET /api/v1/bunkers/{bunker_id}/snapshot?after={cursor}`

Returns the authorized snapshot or bounded changes after a known cursor. Every
request checks current membership and capabilities. Revocation takes effect at
this boundary even if an older local replica still exists offline.

`POST /api/v1/bunkers/{bunker_id}/changes`

```json
{
  "cursor": "change-104",
  "changes": []
}
```

Each change carries an idempotency key, record identity, expected revision, and
typed payload. The service applies the accepted set transactionally and returns
the new cursor and per-change results. Revision conflicts are explicit; the
service never asks the client to merge database files.

Configuration changes replace one complete `ticket-fields` or `integrations`
document. They name `scope_type` (`bunker` or `project`), `scope_id`, `kind`,
and `expected_revision`; the service requires an active membership plus its
configured `config:write` capability before accepting the change. A Bunker
document is the default copied into Projects created afterward. A Project
document is a pinned composition and does not track later Bunker-default
updates. The client never treats cached capabilities as authorization: a local
remote replica stays read-only until the service accepts the typed change and
returns the new revision.

## Stable failures

- `401`: authentication is missing or expired;
- `403`: the member lacks the required capability;
- `404`: the Bunker or invitation is unavailable to this user;
- `409`: an expected revision or cursor conflicts;
- `429`: bounded retry is appropriate;
- other failures: no local accepted state is discarded.

Response bodies may carry host diagnostics, but the desktop renders only the
stable category and next action. Raw server output, database errors, bearer
values, and connection strings never enter user-facing application state.

## Database independence

The service owns its migrations, backups, retention, and database credentials.
The client contract contains only Bunker/Project/ticket/report identities,
membership and capabilities, configuration revisions, typed changes, and sync
cursors. No endpoint returns SQL, a database path, or a connection string.
