# TicketBunker remote service contract

This is the host-neutral collaboration boundary for a shared Bunker. A desktop
client connects to this API, never to the service's physical database. Hosts
may use SQLite, PostgreSQL, or another transactional engine while preserving
the same protocol.

The current client contract is `/api/v1`. JSON responses are bounded to 8 MiB.
Production origins require HTTPS; plain HTTP is accepted only for localhost
development.

## Roles and capabilities

Every service uses the same membership vocabulary, defined in
`ticketbunker/remote.py`:

| Role | Capabilities |
|---|---|
| `owner` | `tickets:read`, `tickets:write`, `config:read`, `config:write`, `bunker:share`, `members:invite`, `members:manage` |
| `member` | `tickets:read`, `tickets:write`, `config:read` |

The user who publishes a Bunker is its owner. An invitation carries the role it
grants. The service enforces capabilities on every request; a client's cached
copy only decides what its editor offers.

`GET /api/v1/capabilities` returns protocol facts and the optional features a
service supports. The reference service advertises `setup-token`,
`invitation-credentials`, `invitations`, `members`, and `changes`.

## Setup and publication

A fresh reference service prints a one-time owner setup token. Claiming it
creates the first user and returns that user's credential:

`POST /api/v1/setup`

```json
{"setup_token": "one-time-value", "profile": {"display_name": "Tim", "email": "tim@example.test"}}
```

```json
{"ok": true, "user": {"id": "user-1", "display_name": "Tim", "email": "tim@example.test"}, "credential": "bearer-value"}
```

The client stores the credential behind a machine-local reference and never
writes it into a Bunker database. A second claim, or a wrong token, fails with
`setup_complete` or `setup_token_invalid`.

`POST /api/v1/bunkers` (bearer required) publishes a local Bunker. The body is
the complete local publication: `bunker` (`id` is a suggestion, `name`),
`projects`, `repository_bindings` (shared fields only, never device paths),
`config_documents`, `tickets`, and `reports`. The service validates the whole
document, assigns the Bunker identity, makes the caller its owner, and returns
the same `snapshot` shape as enrollment. Bunker-scope configuration documents
are re-addressed to the assigned Bunker id.

## Invitations

`POST /api/v1/bunkers/{bunker_id}/invitations` (requires `members:invite`)

```json
{"role": "member", "expires_in": 604800}
```

```json
{"ok": true, "invitation": {"id": "inv-1", "role": "member", "token": "one-time-value", "expires_at": "2026-09-11T00:00:00+00:00"}}
```

The raw token appears only in this response; the client composes the
`ticketbunker://join` link from its known server origin. `GET` on the same path
lists invitations with `state` `pending`, `redeemed`, `revoked`, or `expired`
and never includes tokens. `DELETE /api/v1/bunkers/{bunker_id}/invitations/{id}`
revokes a pending invitation.

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

Before or during redemption the client obtains a credential for the selected
service and stores it behind a machine-local reference (today a mode-0600 file
in the application data home; an operating-system vault backend may replace it).
The HTTP adapter receives the credential only as an ephemeral bearer plus that
reference. A service may return a credential *value* when it creates the member
at redemption; it never returns or invents a device credential *reference*.

`POST /api/v1/invitations/redeem`

```json
{
  "bunker_id": "engineering",
  "invite": "one-time-value"
}
```

The request carries the authenticated user's bearer credential. A service that
advertises `invitation-credentials` also accepts an unauthenticated redemption
with a `profile` (`display_name`, `email`); it then creates the member, issues
their credential, and returns it beside the snapshot as `credential`. A service
without that feature answers `401` and the client tells the user to sign in
first. A successful response is:

```json
{
  "snapshot": {
    "bunker": {"id": "engineering", "name": "Engineering"},
    "projects": [],
    "repository_bindings": [{
      "id": "coal",
      "project_id": "ecs",
      "name": "Coal",
      "canonical_remote": "github.com/example/coal",
      "default_branch": "main"
    }],
    "config_documents": [],
    "tickets": [],
    "reports": [{
      "ticket_id": "ECS-4",
      "scope": "branch:tim/ECS-4-position",
      "repository_id": "coal",
      "content": "# Report"
    }],
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

`GET /api/v1/bunkers/{bunker_id}/members` returns the active members with
roles and capabilities to any member.

`POST /api/v1/bunkers/{bunker_id}/changes` (requires `config:write`)

```json
{
  "cursor": "change-104",
  "changes": [
    {
      "key": "3f9c…",
      "kind": "config-document",
      "record_id": "bunker:engineering:ticket-fields",
      "expected_revision": "local:1",
      "revision": "shared:0f1e2d3c4b5a",
      "payload": {"scope_type": "bunker", "scope_id": "engineering", "kind": "ticket-fields", "content": {"fields": []}}
    }
  ]
}
```

Change kinds are `project` (payload: the project row, `expected_revision`
empty for creation), `config-document` (payload: scope, kind, content),
`repository-bindings` (record id: the project id; payload: the complete list;
revisions are the content-derived digest both sides compute), and `ticket`
(record id: the ticket id; payload: `id`, `project_id`, `payload`,
`source_kind`, `source_reference`; revisions are content digests). The first
three require `config:write`; `ticket` requires `tickets:write`, so members
may sync a Project's tracker while only owners change its setup. A batch that
mixes kinds needs every capability involved. Each change
carries an idempotency `key`, a record identity, the expected revision, and the
revision the client proposes. The service applies the whole set transactionally
and returns `results` (per key: `ok`, `revision`, and `replayed` when the key
was seen before), the new `revision`, and the new `cursor`. A conflict returns
`409` with `revision_conflict` or `project_key_taken` and applies nothing; the
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

Repository bindings are a whole-list, revision-checked Project configuration.
Only an authorized service transaction may add, change, or remove the canonical
remotes allowed for a Project. Device paths never synchronize. A client may map
an allowed binding to a checkout only after the checkout's canonical `origin`
matches and its fetch succeeds. Tickets name one `project_id`; every branch
report also names its `repository_id`, allowing one ticket to span several
Project repositories without assigning the ticket itself to one repository.

## Stable failures

- `401`: authentication is missing or expired (`authentication_required`,
  `setup_token_invalid`);
- `403`: the member lacks the required capability (`capability_required`);
- `404`: the Bunker or invitation is unavailable to this user
  (`bunker_unavailable`, `invitation_invalid`);
- `409`: an expected revision conflicts, or setup was already completed
  (`revision_conflict`, `project_key_taken`, `ticket_conflict`,
  `setup_complete`);
- `410`: the invitation was used, revoked, or expired (`invitation_used`,
  `invitation_revoked`, `invitation_expired`);
- `413`: the request exceeds the 8 MiB bound;
- `429`: bounded retry is appropriate;
- other failures: no local accepted state is discarded.

Every failure body is `{"ok": false, "code": "...", "message": "..."}`. Clients
map the stable `code` to their own next action and ignore the message text.

Response bodies may carry host diagnostics, but the desktop renders only the
stable category and next action. Raw server output, database errors, bearer
values, and connection strings never enter user-facing application state.

## Database independence

The service owns its migrations, backups, retention, and database credentials.
The client contract contains only Bunker/Project/ticket/report identities,
membership and capabilities, configuration revisions, typed changes, and sync
cursors. No endpoint returns SQL, a database path, or a connection string.
