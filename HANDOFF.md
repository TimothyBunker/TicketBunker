# TicketBunker — agent handoff brief

Start with this file, then read `DESIGN.md` (the design authority) and
`README.md` (the user-facing contract). Work on branch
`claude/jira-git-context-agent-artex0` in `/home/tim/dev/TicketBunker`.

## Compact controls and guarded Project actions (2026-09-08)

Follow-up: ticket sections now form a sticky vertical left chapter menu,
remaining available while reading or editing, with unsaved-file protection.
Close controls use a shared cross button; actual back navigation keeps arrows.
Project Sync tickets stays visible with no board and opens board setup. The
ticket Branches heading has a compact refresh icon; automatic opening fetches
remain unchanged. **Edit checks** replaces the long ticket-scoped action label.
Verified follow-up: 71 UI tests and production assets; browser coverage exercises
chapter navigation from an unsaved editor and the compact refresh control.
No backend behavior or installer version changes in this follow-up.

Source-only changes after rc2; no new installer/tag built. The Bunkers sidebar
uses a four-square overview button and create plus, without the branding block.
One Bunker gear groups settings. Project settings/add/sync and configuration
add/remove controls use named, hoverable icons; incomplete setup and failed
sync have red dots. Sync tickets belongs only on the main Project page.
Boards cache for five minutes across drawer navigation, retain visible results
during reload, and invalidate after credential changes. Refresh boards only
reloads choices. Checks removes the automatic-discovery checkbox, retains and
explains legacy enabled state, and offers Find commands / Ask the agent with
exact editable commands. `.tb-check-build` is explained as CMake scratch output,
not a TB target. A disposable CMake test proves the project target is built.

Right-click/Shift+F10 Project actions: Edit, Rename, Delete. Rename preserves
identity/key/parent/position. Delete is explicitly confirmed and **empty-only**
pending Tim's decision on populated Projects: tickets, subprojects and report
dependencies prevent removal. Local Git folders/tracker issues are untouched.
New inspect/rename/delete worker actions share backend commands; shared writes
require server config:write and revision checks. `project-delete` is a new
typed service change; independently hosted servers need updating. Snapshots
propagate empty removal to members without dropping locally nonempty Projects.

Verified: 70 UI tests, 379 backend tests, production assets, and the real
browser hardening flow (context menu, rename, cancel/confirm delete, icon
navigation, board caching, project-page sync). New shared tests use independent
owner/member data homes and the real HTTP service. All data is disposable.
No real user repositories or agile services changed.

## Bunker repository setup and automatic board sync (2026-09-07)

Post-release UI change: back-navigation controls are arrow-only, with accessible
names and hover titles supplied by the shared `BackButton`. The legacy source
viewer follows the same contract. Navigation and unsaved-draft guards are
unchanged. This change is not included in the already-published rc2 installer.

Repository-flow correction: Bunker **Repositories** rows now expand into
**Projects** availability checkboxes, including nested Projects. Toggles save
through the existing owner-only, revision-checked selection command; unsaved
registry rows must first be saved. Project creation no longer selects remotes.
Project **Configure → Repositories** is device-local: **Add local repository**
chooses a folder and matches `origin` automatically, while remote/base fields
are read-only. Matching never creates shared bindings or grants availability.
The real-browser run caught `application.project.repositories.select` missing
from the worker action allowlist; it is now explicitly exposed and covered by
a real worker regression. Legacy typed selection APIs remain compatible.
These corrections are source changes after rc2, not a new installer release.
Checkout inspection/matching uses the configured `remote.origin.url`, not
`remote get-url`'s expanded transport address; fetch still honors Git URL
rewrites. This is covered with a local `insteadOf` mirror in the browser flow.
Verified after these corrections: 372 backend tests, 60 UI tests, the production
asset build, and the real-browser flow. The registry suite includes independent
owner/member data homes, a member's automatic checkout match, rejected member
policy changes, and syncing one Project's removal while retaining another's
binding and device-local path. No real user repositories or tracker tickets
were changed.

Published: **v0.6.2-rc2** at `1855aaf`, with app/runtime/native version **0.6.2**.
Release: https://github.com/TimothyBunker/TicketBunker/releases/tag/v0.6.2-rc2
All three Release platform jobs and publication passed (run `34171059385`):
Windows x64 NSIS, Linux x64 DEB/AppImage, and macOS Apple Silicon DMG, plus
runtime bundles. All seven binaries have uploaded adjacent SHA-256 files;
their contents were checked against GitHub's uploaded-asset digests. Branch
CI/browser and Windows/macOS portability passed for the candidate. Windows
remains an unsigned prerelease; installed-app interaction still needs Tim's
smoke test. Close the app, install the new build, and reopen. Separately hosted
`tb serve` processes also need updating/restarting for the new server-side
registry and administrator rules; updating a desktop client alone cannot do it.
Candidate rc1 caught a first-click branch-panel reset race and a Windows
fixture cleanup race after successful collaboration checks. The panel now
resets before paint; the fixture bounds retries for Windows sharing violations
while its finished worker closes the log. Other cleanup errors still fail.
The rc1 Windows core run also exceeded a five-second test-only completion
budget after releasing the repository lock. The branch's follow-up test gives
real Git work thirty seconds, preserving its exclusion assertion. This changes
no shipped runtime behavior and is not a reason to rebuild a green rc2 binary.
That test-only follow-up is `731c403`, not part of the immutable rc2 tag.

Changes since the 0.6.1-rc3 installer below:

- Bunker **Repositories** owns the allowed source-remotes registry. Create
  Bunker accepts allowed remotes and an optional first-Project selection.
  Project creation and **Configure → Repositories** select from that registry;
  **Connect folder** still verifies `origin`, fetches, and stores only the
  device-local path. It grants no shared authority and changes no database host.
- `repository-registry` is a Bunker-scoped, revision-checked config document.
  The new `application.project.repositories.select` action and existing backend
  gates validate selections; the service independently enforces them. Project
  binding IDs stay unchanged for branch/report references. Removal is refused
  while any Project uses the remote, including stale-proposal races.
- Existing doc-less Bunkers derive the initial list from their existing
  bindings. Saving it adopts the registry without rewriting user data. Legacy
  binding mutation remains compatible only until a registry is saved; new UI
  creation always writes one. Field templates remain pinned copies, whereas
  repository approval is a Bunker-wide policy, not a Project template copy.
- Only reference-service administrators may create shared Bunkers. One-time
  setup establishes that privilege; a one-time schema migration recognizes
  the original setup user on existing services. Bunker owner invitations do
  not make service administrators. Bunker owners create Projects and configure
  shared repositories; members connect local folders. Forged writes are rejected
  server-side. Local-only Bunkers remain available without server authority.
- **Link board** invokes the existing sync handler automatically. Failed sync
  retains the saved board and existing tickets, with **Sync tickets** as retry.
  A successful retry clears the error and refreshes the catalog. Provider HTTP
  is mocked in the new browser scenario; no real tracker tickets were touched.
- Setup controls say **Save** / **Connect folder** / **Share Bunker**, not
  Publish. Report-publication gate vocabulary is unchanged.

Verification commands: `python -m unittest discover -s tests`,
`npm run build --prefix desktop`, `npm test --prefix desktop`, and
`node desktop/tests/hardening.e2e.mjs`. New coverage lives in
`tests/test_repository_registry.py`, the configuration/Hub component tests,
and the committed real-browser flow. It includes separate admin/member data
homes on a dummy service, forbidden Project/Bunker creation, registry
validation, local remote matching, and automatic sync recovery.

Verified: 369 backend tests, 54 interface tests, TypeScript/production asset
build, and the full real-browser hardening flow passed. The 14 focused registry
tests also passed after the final shared/local input-limit alignment.

## Interface hardening (2026-09-07)

Published: `v0.6.1-rc3` at `c86b912`, with Windows x64 NSIS, Linux x64
DEB/AppImage, Apple Silicon macOS DMG, runtime bundles and adjacent checksums.
Release: https://github.com/TimothyBunker/TicketBunker/releases/tag/v0.6.1-rc3
All three Release platform jobs passed, alongside branch CI/browser and focused
Windows/macOS portability checks. The backend suite is now 355 tests; UI 50;
native-shell 9. Windows is an unsigned prerelease. Installed-app interaction
still needs Tim's smoke test; CI packaging is not a claim of that manual run.
The candidate history below explains why rc1/rc2 were not published.

Release preparation: runtime, UI and native package versions are now 0.6.1;
the intended installer tag is `v0.6.1-rc3`. The browser version assertion reads
the package version rather than hardcoding it. This remains a prerelease under
the existing unsigned-Windows policy; do not bypass the stable signing gate.
Verify the tag's Release workflow and assets before claiming an installer is
available. The historical validation notes below predate release preparation.
Candidate rc1 passed Linux CI but its macOS release job found an audit-harness
path assertion comparing /var with /private/var; directory identity comparison
now handles canonical paths. No product path/authentication rule was relaxed.
Windows also exposed the harness's stripped USERPROFILE, which prevented
Python home-directory resolution; the fixture now supplies a disposable profile
and has a regression assertion. It does not inherit the developer's profile.
Candidate rc2 passed that macOS exercise but exposed an existing race test's
mock ending before the HTTP handler's post-response queue call. Both HTTP tests
now await that call before releasing the mock; the repository lock is unchanged.
Windows diagnostics then exposed a real Checks path-length failure: detached
worktrees now use a short owned OS temporary directory through layout.py;
observations/logs remain in the ticket runtime, with cleanup assertions. A
path-triggered portability workflow exercises the two-user/locking fixtures on
Windows and macOS without repeatedly compiling installers.
The Windows dummy client now decodes the worker's UTF-8 protocol explicitly;
its former locale-default decoding corrupted punctuation in the comparison,
while the seeded report retained the correct Unicode text.

Follow-up: configuration now uses the unsaved guard for fields, mappings,
repositories and Checks, including sidebar navigation and opening a ticket
behind the drawer. Publishing one section no longer clears other drafts or
replaces their original expected revisions. Save publishes dirty sections
separately, retains failures and skips successful sections on retry. Inputs
are disabled during publication. This does not claim draft persistence across
app crashes or protection for credential/onboarding forms.
Follow-up validation: 50 UI tests and the expanded real-browser regression
passed with a production UI rebuild. The browser checks real local configuration
publication, draft retention, cancel/discard/save and the prior file/Git flows.
Python behavior was unchanged in this follow-up; the full 354-test result below
is from the preceding fix pass. No installer release or push has been made.

Implemented the audit's five primary fixes plus recovery/accessibility polish:
source editor Save / Do not save / Cancel guard (ticket/file/scope navigation,
native close hook, browser unload warning); default `task` and canonical runtime
labels with validation before Git mutation; failed branch-link reread and
existing-branch attach; independent check outcomes with commit/time/log and
staleness; browser repository-path connection. Error toasts are dismissible and
do not expire; failed Bunker sync stays visible across ticket navigation, with
explicit retry and unchanged last-success time. Added clear filters, full dirty
filenames, editor/chat labels, valid inline source-action markup. Ticket open
now queues a safe ref fetch; Branches exposes retry, never an implicit pull or
synthesis. UI rebuilt into tracked application assets.

Regression: `npm run test:hardening --prefix desktop` starts its own disposable
worker/database/Git fixture and exercises real browser navigation, default-type
branch creation, clear filters, cancel/discard/save, and an external-editor
conflict. It checks bytes on disk and cleans up the fixture. Added to browser CI.
The old default-type expected failure is now an ordinary passing regression.
Native close is covered with an API-boundary test and Cargo checking, not a
Windows/macOS installed-app run. No release or push performed by this change.
Final validation: 354 Python tests and 43 UI tests passed; production UI build,
real-browser hardening flow (including a conflicting external edit), offline
Cargo check, and `git diff --check` passed. The hardening flow's popup capture
is `/tmp/ticketbunker-unsaved-changes.png`; the fixture itself is removed.

## Prior interface exercise (2026-09-06)

Two isolated users role-played 22 scenarios against real app workers, sharing
service and dummy Git clones through Chrome. See
`docs/roleplay-audit-2026-09-06.md` and its 26-image evidence gallery. Production
code was not changed during the audit. Highest-priority reproductions: unsaved
source edits silently disappear on returning to the ticket; a missing ticket
type becomes `Task` and fails legacy intake after Git branch creation; failed
branch publication leaves stale UI with an unusable retry instruction. The
save/commit/resynthesize loop, sharing/read-only member configuration and
offline/reconnect path were exercised successfully, with recovery caveats in
the report. Agent prose uses the deterministic stub; native picker result is an
explicit shim, not a native OS test. All lab listeners were stopped. Interactive
helpers: `tools/roleplay_lab.py`, `desktop/tests/roleplay-driver.mjs`.

## Product in one paragraph

TicketBunker is a Bunker-owned context layer between agile trackers and source
work. A Bunker is the configuration, membership, authorization, and sync
boundary; it contains recursive Projects, and Projects explicitly attach Git
repositories as source/workflow tools. Every Bunker has one device-local SQLite
database and may optionally replicate through an authenticated, team-selected
TicketBunker service whose physical database never becomes a client contract.
Cloning source does not join a Bunker. Tickets preserve normalized tracker
facts, provider extensions, activity, branch relationships, deterministic Git
observations, composed agent context, and gated agent reports. Desktop and
optional automation call the same stdlib-only Python action registry.

## Trust rules

- **observation** is a mechanical pipeline fact.
- **real** is an agent-located claim with a path/line anchor verified at the
  publish gate.
- **speculation** is interpretation ranked grounded, adjacent, or ungrounded.
- Tags upgrade only through verification; finding nothing is reportable.
- Typed intake is the only write route. Git execution is ledgered by the
  wrapper as an execution side effect. Report lint and anchor verification
  cannot be bypassed.
- Ours, pipeline, and agent data stay separate at rest and meet only during
  composition.

## Current product state

Everything below is covered by the test suite and deterministic product
exercise unless a live boundary is explicitly called out.

### Host portability boundary

- `ticketbunker/platform_services.py` is the only Python boundary that detects
  the operating system and architecture. It supplies application-data paths,
  Pi/release keys, executable suffixes, Windows-safe command parsing, editor
  candidates, terminal launchers, detached-process flags, and no-console flags
  for piped helpers. Windows production builds use the graphical subsystem;
  the native core worker, Pi setup bridge, readiness probes, report sessions,
  and coworker sessions never allocate or flash a terminal window.
- Bunker storage, runtime discovery, the desktop launcher, browser worker,
  synthesis worker, and external editor consume that boundary. The domain has
  no WSL branch; WSL runs the ordinary Linux implementation. DNS failures give
  a host-neutral recovery action instead of prescribing `wsl --shutdown`.
- `application.state` exposes the selected local platform shape so future
  standalone transports can choose environment capabilities without deriving
  them from repository paths. Linux and Windows are the initial tested desktop
  targets. macOS remains build-compatible but experimental pending real-device
  verification.

### Bunker database and application onboarding

- **Isolated-participant validation:** `tools/two_participant_loop.py` exercises
  Alice and Bob in separate worker processes, data/settings/credential stores,
  and Git clones against the real local service. See
  `docs/two-participant-exercise.md` for the 17 checks and limitations. The
  default `Task` versus runtime `task` mismatch was fixed on September 7;
  its regression now passes without an expected-failure annotation. The
  collaboration fixture still uses an explicit lowercase type. Real-device/installed-app
  participant testing and shared file conversations remain unverified.

- **6 September cutover slice:** imported tickets are one-time copies, never
  reread from companion storage. Native workflow state/start/attach use Bunker
  configuration and publish repository-scoped ticket branch links through the
  service when shared. The app offers a repository selector only when needed,
  uses actual base refs, and retains branch-creation confirmation. Check
  discovery and ticket update drafting receive the selected binding too.
- Runtime directories are now repository-scoped. Clean accepted report copies
  reconcile on materialization; local drafts retain their accepted baseline
  and cannot overwrite a newer database revision. Normal publication derives
  Combined from all repository children, not caller text. Ticket/report applies
  are revision-checked inside SQLite write transactions. Tests in
  `test_bunker_cutover.py` exercise disposable source repos and a local service.
- **Still open:** service-side report gates/trusted publisher policy, server
  aggregate derivation, draft-conflict UI, deletion reconciliation, and full
  Bunker-owned coworker-session storage. Do not represent this slice as the
  complete architecture migration. Native installed-app testing on Tim's
  machines, second-participant product validation, and Jira transport remain
  separate work. Keep the existing release pipeline, Standing, editor/file
  conversations, and project/ticket Checks contracts intact.
- Cutover validation: 349 Python tests, 30 UI tests, production UI build, and
  the real browser smoke flow (including opt-in source branch creation) passed
  on disposable Linux fixtures. This is not live Windows/macOS installer or
  second-participant validation. Existing Markdown tests still emit their
  pre-existing nested-element warnings; this slice does not alter that renderer.

- `ticketbunker/bunkerdb.py` owns the new storage boundary: one SQLite database
  per Bunker plus a machine registry. The schema includes recursive Projects,
  shared repository identities, device-local checkout paths, versioned config
  documents, tickets, reports, remote relationships, and migration receipts.
- Creating a Bunker needs only its name and first Project. Adding child Projects
  and configuring repositories are separate typed actions. Each Project owns a
  revision-checked allowlist of canonical remote identities. Local attachment
  validates a checkout against that list and fetches `origin` before saving its
  machine-only path, without writing `.ticketbunker.json`, `.tb/`, `.gitignore`,
  hooks, credentials, or a
  database file into source.
- `ticketbunker/remote.py` defines the versioned service boundary. HTTPS is
  required except for localhost development. Join invitations are inspected
  locally, bearer material is omitted from returned application state, and the
  remote must authenticate/enroll the user before `create_replica` creates a
  local database. Clients receive service/snapshot/change contracts, never a
  remote database connection string. `docs/remote-service-contract.md` records
  the endpoint, enrollment, capability, conflict, and error contracts.
- The desktop launcher now lands on **Bunkers** with peer **Create Bunker** and
  **Join Bunker** actions. Bunker rows show their local/remote relationship and
  project/repository/ticket counts. Selecting a Bunker shows its Project
  catalog (each Project's allowed repositories, this computer's connection
  state, and ticket count including descendants), never a ticket list; the
  Bunker header offers only Configure, Connect remote, and Add project.
  Selecting a Project shows its subprojects, then the ticket catalog. Project
  Configure opens to an indexed Repositories table with shared allowlist fields
  and per-device Connect/Disconnect actions.
- Sharing exists end to end against the reference service. `tb serve`
  (`ticketbunker/service/`) is a stdlib-only HTTP service over one SQLite file
  that implements `/api/v1`: one-time owner setup token, publish, invitations
  (issue, list, revoke; single use; roles), redeem with credential issuance,
  members, snapshots, and all-or-nothing revision-checked typed changes.
  **Share Bunker** publishes a local Bunker and makes the user its owner;
  **Members** lists members and gives owners invitation links; **Join** takes
  only a link. Owners keep configuring after sharing: `propose_*` validates,
  the service accepts, then `apply_*_change` writes locally. Members see a
  read-only editor. Credentials live only in the mode-0600 machine-local store
  (`ticketbunker/credentials.py`); Bunker databases hold references.
  `tests/test_service.py` and `SharedBunkerTests` in `tests/test_application.py`
  drive the owner and member flows against the in-process service.
- Each Project links one or more boards in its `tracker-sources` document
  (per board: id, kind, owner, number or repository, title, link); a single
  `tracker-source` from earlier builds is read as a one-item list.
  `application.project.link` appends, `application.project.unlink` removes,
  and sync reads every board, keeping one copy per tracker identity. `application.tracker.connect`
  verifies a token with `integrations/github.py` `discover`, keeps it through
  `credentials.update`, and records the account in the Bunker's `connections`
  document; `application.tracker.boards` lists what the token can see and the
  Project's Board view links one by selection. The field matrix and ticket
  fields live under that view as disclosures; Bunker Configure has Trackers
  and Defaults. **Sync tickets** (`application.project.sync`)
  reads the board with `integrations/github.py` (`collect_entries`,
  `build_tickets`), keys issues by Project key plus issue number when titles
  carry none, and upserts tickets through `propose_ticket_sync` /
  `apply_ticket_sync_change`. Shared Bunkers send `ticket` changes (members
  hold `tickets:write`) before writing locally. `TrackerSyncTests` and the
  shared-Bunker test cover it with the GitHub transport mocked.
- Agent reports are Bunker records. `ticketbunker/bunkerruntime.py` projects a
  Bunker ticket into `<data home>/bunkers/<id>/runtime/repositories/<binding>/<TICKET>_bunker/` so the
  unchanged pipeline (bootstrap, compose, harness, lint, anchors, master) runs
  there; `tb run`/`tb publish`/coworker acceptance then call
  `publish_reports`, which writes the child and master into `reports` and, on
  a shared Bunker, pushes `report` changes first. `truth.*` and
  `autoagent.resolve_bunker` accept a `bunker_id`/runtime directory, so
  Synthesize works on tickets that came from a board with no `.tb` layout.
  `application.bunker.sync` reconciles a replica from the service snapshot
  (`bunkerdb.apply_snapshot`); the Bunker page pulls on open and on Sync now.
  Ticket pages show each branch report's verification stamp and record time,
  present Ticket and Agent work as two sections of one page rather than tabs,
  and a failed synthesis names its summary and first blocking finding, or the
  provider line that stopped it (for example a usage limit), never a warning.
  Bunker citations such as `runtime/ECS-4_bunker/ticket.json` normalize to
  the bunker layout at the anchor gate; a report verified at the current
  commit that the Bunker refused is shared on the next request without
  another model run; a `changes_invalid` answer from the service tells the
  user to restart an older `tb serve`.
  `tests/test_bunkerruntime.py` runs the stub harness end to end on a local and
  a shared Bunker.
- Existing companion-backed projects appear in a separate Migration section.
  **Import copy** reads legacy locator/config/ticket/report material into a new
  local Bunker, records its digest and counts, and does not modify the source or
  companion repository. The old and new stores are never concurrent writers.
- The Bunker database and onboarding action tests live in
  `tests/test_bunkerdb.py` and `tests/test_application.py`. The legacy ticket
  engine and renderer remain as import/compatibility paths, while the ordinary
  standalone ticket catalog and detail reads now come from the Bunker database.

### Legacy companion database and configuration

The behaviors below remain compatibility and import coverage for installations
created before the Bunker database cutover. They are not the authority model for
new Bunkers.

- `tb init` is the one public connection command. Without a locator it connects
  an owner-created repository, publishes `ticketbunker/config` and
  `ticketbunker/data`, then writes `.ticketbunker.json`; with a committed
  locator it connects or refreshes a collaborator checkout. `tb setup` remains
  only as an alias.
- Every successful `tb init` installs checkout, commit, merge, and rewrite Git
  hooks. They preserve and chain existing hooks, record the newest ticket
  branch/HEAD/event/change time below `.git`, and never call a provider or
  start a worker. The user starts synthesis explicitly from the ticket state
  hub. That request queues the detached worker, refreshes deterministic
  observations, then follows the ordinary lint/anchor-gated agent and companion
  publication path. A post-agent HEAD check keeps superseded forms unpublished.
- `tb init` also establishes local agent readiness. The normal full release
  discovers its pinned bundled pi runtime, verifies every pi distribution file
  against the bundle manifest, adopts pi's selected provider/model, and calls
  pi's credential-safe auth probe. If login or selection is absent, interactive
  init opens pi once for `/login`, `/model`, and `/exit`, then checks again.
  Only harness/provider/model identifiers enter `~/.tbconfig`; credential
  values remain wholly owned by pi. `--core-only` is the explicit local opt-out.
- The selection lifecycle is explicit after init: `tb agent status`, `tb agent
  model`, and `tb agent login`. Model changes may instead use optional
  `--provider`/`--model` arguments on either `tb init` or `tb agent model`.
  Every route validates authentication before replacing the last working
  machine-local selection, and successful init prints the later change command.
- Remote Git probes are non-interactive and bounded. Transient DNS resolution
  is retried three times; DNS, timeout, authentication, access, and local
  fast-forward failures get distinct actionable rejections instead of raw Git
  tracebacks or manual `getent`/`git ls-remote` diagnosis.
- A fresh clone on a locator-less default branch fetches source refs, discovers
  one canonical remote locator, and safely switches a clean worktree to that
  tracking branch. Dirty and ambiguous cases stop with an exact next action.
  Live verification cloned Coal at default `main`; installed `tb init` found
  `origin/ecs`, switched to `ecs`, connected both companion checkouts, passed
  `tb doctor`, and listed all four ECS references. The scratch clone was removed.
- Remote Git and branch rules remain the sole authorization mechanism.
- Configuration is project composition, not mutable inheritance. New projects
  pin the then-current global field sets. Project edits do not rewrite global
  defaults or existing tickets.
- GitHub, GitLab, Jira, and custom integrations use the same editable
  TicketBunker-field ↔ provider-field matrix. Credentials live only in the
  configured local environment file, which is ignored.
- `tb config` opens the remote-backed editor. Its Repository page explains the
  three real steps: create an empty companion repo, connect it, then commit the
  source locator. Its Local settings page is explicitly machine-only. The
  included browser source editor is the default; indentation, tab width,
  wrapping, deterministic save cleanup, and the optional external command are
  stored in `~/.tbconfig` through `local.editor.update`. `tb config --editor`
  uses that same command and does not publish companion data. If an explicitly
  selected external editor cannot launch, source links fall back to the
  included editor instead of asking the user to configure a terminal launcher.
- `tb doctor [--json] [--offline]` checks Git identity, locator/ignore state,
  companion checkouts and remote heads, resolved configuration, ticket index,
  credential presence, hooks, and worktree cleanliness. Every failure names a
  next action. It writes only a local diagnostic ledger below `.git`.

### Tickets, indexes, and integrations

- Canonical tickets are `projects/<PROJECT>/tickets/<TICKET-ID>` on the data
  branch. `projects/<PROJECT>/tickets/index.json` contains bounded summary
  references, source URLs, digests, and counts—not report bodies.
- A fresh connection sparsely checks out indexes only. Tickets, Projects, search,
  facets, and pagination work from those references. Opening or mutating one
  ticket hydrates only that ticket. Index-less older data branches fall back to
  a full checkout.
- `ticket.upsert` and `ticket.sync` are shared command handlers. Provider sync
  preserves TicketBunker-owned branch assignments and local extensions.
- Packaged `tb github ISSUE` and `tb github --project NUMBER
  --project-owner OWNER` normalize GitHub payloads through the configured field
  matrix and shared batch command. An identical pull creates no ledger event or
  companion commit. `--fixture FILE` drives the same path offline.
- Provider-neutral activity mirrors comments, history, worklogs, logs, notes,
  and extension-defined kinds. It is read-only in TicketBunker because the
  configured tracker remains that activity's ground truth.
- GitHub Project #8 was live-synchronized into Coal on 23 August 2026: ECS-1
  through ECS-4 are active, assigned to `TimothyBunker`, committed in the
  companion index, and visible in both the personal and Project queries. The
  first full pull created three and refreshed ECS-4; an immediate second pull
  reported all four unchanged without moving the data revision. ECS-4's report
  remained intact. Coal `ecs` commit `e886c58` records the source-base locator
  hint; companion data commit `3a9ae5d` owns the four-ticket synchronization.

### Ticket-to-branch workflow

- `tb branch TICKET --name SHORT [--base BRANCH]` and the viewer's small Work
  plus dispatch the same `workflow.start` command.
- The command rejects a dirty source worktree, fetches origin, switches to the
  selected base, fast-forwards only, creates `<user>/<ticket>-<short-name>`, and
  switches to it. There is no reset, force, autostash, or hidden merge.
- An assignment/session is published only after Git succeeds. Existing
  branches use strict `workflow.attach`; a name without the bounded ticket key
  is rejected with a rename instruction.
- Opening a ticket renders cached companion data and local/already-fetched refs
  first. After the response, one background command refreshes companion state,
  fetches/prunes `origin`, and rebuilds Branches from local refs,
  remote-tracking refs, and explicit assignments. It works with uncommitted
  source files because it never pulls, rebases, switches, merges, resets, or
  stashes; offline opens keep the rendered ticket and provide a retry action.
  The background refresh never queues synthesis.
  A branch missing its computed comparison is labeled `Needs Git state`.
- The localhost Work POST is explicit and CSRF-token protected. Viewer pages
  remain bound to `127.0.0.1`, escape ticket/agent content, disable caching,
  framing, referrer propagation, and MIME sniffing, and never expose ticket,
  comment, report, or lifecycle mutation controls.
- Viewer failures are a product state, not a Python traceback. Git/workflow
  rejections show what stopped, what was preserved, and the exact next action;
  unexpected failures show a diagnostic ID while full detail stays local in
  `.git/ticketbunker/viewer-errors.jsonl`. Browser disconnects are silent.

### Viewer and agent reports

- Tickets lands on the current Git user's active tickets. Search and
  status/assignee/reporter/label/project filters persist in the URL; done and
  closed remain available without cluttering the default page. Projects uses
  the same reference query.
- A ticket page has explicit Ticket and Agent tabs. Ticket is a compact
  Jira-shaped read surface: project, title, mapped status, labels, source update
  time, rich description/acceptance criteria with nested list rendering,
  activity, useful details, and a Metadata disclosure for transport fields.
- One Synthesis and Branches rail persists beside both tabs. Agent work owns
  only the center Agent surface. Combined is the mechanically generated master
  and separates each branch visibly;
  Who/What/Where/Why synthesis appears before each collapsible full evidence
  trail. Branch selection narrows the view. After any report exists, Report
  scope includes every resolved branch plus retained report, so a new local
  branch remains selectable with a direct Synthesize empty state before its
  first report. Before the first report, there is a direct empty state rather
  than a meaningless Combined selector.
- A compact state cluster sits beside the ticket title. Healthy Companion and
  Git refs are hidden; loading, cached, and stopped states surface as small
  dots. Synthesis stays visible as a dot plus `Updated <age>` when known, or its
  concise state when no successful update exists. Hover, focus, and click reveal
  the full detail and Synthesize, Update synthesis, Retry synthesis, or View log
  action. The localhost log page shows a bounded, escaped tail and the exact
  `.git/ticketbunker/automatic-agent/agent.log` path. The browser reloads only
  when ticket data or refs change.
- One local Pi coworker session exists per user and ticket across every branch.
  The viewer exposes it as Standing/Who/What/Where/Why chats with
  Combined-or-branch scope; the Standing topic covers the verdict, plan,
  checklist, and defects, and its proposals name the part they edit. A
  `file` category keyed by `path` holds one conversation per file; the
  drawer folds them into one Files entry with a list and per-file Forget
  (`coworker.file.forget`), editor note replies and Send to chat open the
  file's thread directly, and it never proposes report changes. Category actions and the persistent launcher are icon-only chat
  bubbles. The expanded surface is a right-side drawer whose arrow collapses
  it without implying that the session is closed. Topic, scope, draft,
  reference, and expanded state persist into the included source editor and
  back. A drag, long touch, double click, or keyboard selection over synthesis
  or full-trail text reveals the quote action; ordinary taps and text-field
  caret movement do not revive it. The bounded category/scope/branch quote is
  attached to the next message, uses an explicit Clear quote action, and stays
  in local history. Git-backed truth polling pauses while the user is typing
  and otherwise runs at a five-second cadence. Pi
  receives only read/search tools. Raw chat stays below `.git`; a
  coworker reply can prepare an inert child Reader-brief proposal, and explicit
  acceptance rechecks digest/lint/anchors, updates the named children, then
  regenerates Combined. Rejected proposals show a concise not-saved outcome;
  exact lint/anchor findings remain in private local `diagnostics.jsonl` and do
  not leak into coworker replies or command errors. It cannot edit source, run
  Git mutations, or write Combined directly.
- Every composition reseeds the explanation and workflow fragments, so Bunker
  runtimes (which were never seeded before) and older legacy bunkers brief the
  agent with the current rules. A multi-line ticket description prefills as
  one `[observation]` item per bullet. The anchor check accepts only
  `ticket.json`, `observations/`, `inferences/`, and `reports/` as bunker
  evidence; the form, transcript, prompt, and context fragments never
  resolve. A repair pass also carries the advisory warnings. The `locate`
  stage places ticket terms on the baseline so the agent starts from hits.
  While a synthesis runs the page polls and shows phase, tool calls, and
  elapsed time, then re-reads once when it stops.
- The agent reads the located and touched files and records definite errors
  as `Defect:` claims ([real] and anchored, lint-enforced; a suspicion is a
  Risk). A Defect blocks the Standing verdict and appears in the drafted
  update. The `scan` stage parses touched and located Python/JSON/TOML files
  at the branch head and prefills syntax errors as observations.
- Agent work has Draft update: `application.ticket.update.draft` composes a
  tracker comment from the report's Standing and Checklist (`updates.py`),
  and `application.ticket.update.post` posts the edited text to the GitHub
  issue behind the ticket after a confirming click.
- Version 0.6.0. Pre-release tags (`v0.6.0-rc1`) publish unsigned test builds
  as GitHub pre-releases; stable tags still require the Windows certificate.
  The interface carries its build version and shows a banner when the core's
  version differs. GitHub Actions jobs on the private repository have not
  started since September 2 (zero steps, no runner, three-second failures),
  which is the account's Actions minutes or billing, not the code.
- The `checks` document also carries `discover` and per-ticket `tickets`
  overrides; `bunkerruntime.effective_checks` resolves ticket → project →
  discovered → bunker and names the source; `application.ticket.checks.set`
  edits one ticket's commands from its page; `application.checks.discover`
  asks Pi (read-only tools) for commands at project or ticket level.
- A Project's `checks` document (build, test, timeout) makes the bootstrap
  run the team's own commands in a detached scratch worktree at the branch
  head (`check` stage); outcomes prefill What and State and a failure asks
  for a blocked verdict. The Checks tab detects suggestions from the
  repository root's manifests; `application.project.checks.detect`.
- Details leads the ticket page, then Description. The source editor scrolls
  to and flashes the opened line, and shows the report's anchored claims for
  the open file as margin notes beside tinted lines (`sourceNotes` in
  `report.ts`), each with Ask about this.
- The report page reads first: trust tags are muted words in a fixed column,
  references show file name and line, the four cards share one fixed height
  with the body scrolling and Full trail pinned to the bottom, and Standing
  lists Defects above the plan.
- The Reader brief opens with `### Standing` (one Verdict claim, optional
  `Next:` and `Ask:`), carries `### Checklist` (one `Done:`/`Partial:`/
  `Missing:`/`Unclear:` claim per ticket item) when the ticket has items, and
  closes with `### Plan` (one to seven ordered `Step:` claims). The lint checks
  their shape; older reports without them stay admissible. The page renders
  Standing as a section above the four cards with the verdict as a pill, the
  plan numbered (derived from Next, Defects, and unfinished items when a
  report has none), and the checklist marked.
- The bootstrap runs nine stages: the six Git stages plus `locate`, `people` (recent
  authors of the touched files on the baseline, last committer on each other
  branch of the ticket) and `risk` (baseline overlap since the merge-base,
  TODO/FIXME markers added by the patch, source changed without a test).
  The scaffold pre-fills Who, What, and Where from them, Who guidance names
  people and forbids file-only or "no one else" claims (lint warns
  `who-people`, and a Who `[real]` claim may cite `(ticket)`/`(authorship)`/
  `(people)` instead of a path), and each brief part may add one `Risk:` and
  one `Next:` claim, rendered as a second chip in the viewer.
- Every reference form the anchor checker accepts (`path:line` or `path` in a
  code span, `@path:line`, `@path`, and the older `@[path]:line`) renders as
  one reference control in Combined, child synthesis, full trails, the ticket
  description, and coworker copy. Clicking it shows a small toolbar beside
  it: Open in editor, Copy path, Send to chat. Selected report text gets the
  same toolbar with Send to chat and Copy; selection never opens the chat by
  itself and the toolbar never clears the selection. The default editor is the
  included source editor. `editor.read` confines paths and bounded UTF-8 content to the current
  checkout; `editor.save` requires the opened digest and atomically writes one
  file without staging or Git. A small clipboard control follows every source
  link; `editor.resolve` applies the same checkout guard and copies the absolute
  local path plus line without opening or reading the file. The editor toolbar
  names the originating ticket as its back destination. Selecting bounded
  source text can attach its guarded path, line range, text, and saved/draft
  state to the same ticket coworker. External editors remain an explicit option
  and fall back to the included editor when unavailable.
- The primary interface is one React/TypeScript application compiled into the
  Python package. Tauri loads it directly without localhost; the explicit
  browser fallback serves the exact same assets on loopback. Bunkers, Projects,
  ticket search/detail, Ticket/Agent views, lifecycle and branch controls,
  synthesis/trails, coworker chat, source links, and the included editor are
  one UI, not a port of the old Python HTML renderer.
- Native actions use one bounded `_app-worker` JSONL session, correlate every
  response by request ID, preserve typed user rejections, and never replay an
  action after an ambiguous transport break. Browser actions cross a per-launch
  CSRF-protected `/api/action` transport into the same allowlist and handlers.
  The legacy Python renderer remains compatibility code only.
- Browser-fallback presence tolerates hidden-tab timer throttling (three-minute
  session life, re-announce on visibility/focus), the worker prints why it
  stopped, and a stopped service shows one banner with the fix instead of
  silent `ERR_CONNECTION_REFUSED` failures. `tb view` still requires a legacy
  `.tb/` or locator; a repository attached only through a Bunker must be opened
  with `_app-browser-worker --repo PATH` until `tb view` learns Bunker
  attachments.
- `tb view` prefers that app when installed and returns immediately;
  `tb view --browser` explicitly selects the detached shared-UI browser fallback.
  If no app is installed, browser mode remains the automatic fallback. Its
  health/version replacement, page-presence shutdown, `--foreground`, and
  `--no-open` behavior are unchanged.
- Opening the app without CLI arguments now lands on its bundled Bunkers
  window. Create makes a local Bunker plus first Project before Git is involved;
  Join reviews a bounded invitation and calls the remote enrollment contract
  before creating a replica. Each Bunker shows its recursive Project tree,
  local/remote storage relationship, and counts. Project repository
  configuration is separate from Bunker remote connectivity. Checkout
  validation and fetch happen only after a native picker selection and never
  mutate the checkout. Linux sessions with a stale advertised D-Bus socket use GTK's in-memory settings backend,
  preventing picker warning floods. The bundled local window alone receives
  the directory-dialog and agent-setup capabilities; remote HTML is never
  loaded into it.
- **Configure** edits revision-checked Bunker defaults or a selected Project's
  pinned field/integration documents and repository allowlist. New Projects
  copy the current defaults;
  existing Projects do not mutate when defaults change. GitHub, GitLab, Jira,
  and custom mappings share one editable matrix. Local Bunkers can publish;
  remote replicas remain read-only until their service authorizes a shared
  change, so cached capabilities cannot self-elevate.
- First-run and Local settings now provide the complete agent path without
  opening Pi's terminal UI. The Bunkers window starts verified Pi in
  constrained RPC mode with only TicketBunker's pinned auth bridge, renders
  OAuth, device-code, select, and secret prompts itself, and sends replies
  directly from Rust to a headless Pi child. Pi alone writes credentials; Python receives only
  readiness and the selected provider/model. Closing setup kills the child,
  and opening/cancelling does not silently enable an agent. The explicit
  **Use without agent** route preserves non-agent work.
- Harness/provider/model identity is displayed separately from work authors.
  Unknown generator fields are omitted, never guessed.
- Report publication still passes lint plus anchor verification and stamps the
  verified branch/commit. Reports that predate newer branch development are
  visibly badged.

### Distribution

- Version 0.5.0 makes the installed native app the ordinary product boundary.
  Debian installation registers a `TicketBunker` application-menu entry with
  `Terminal=false`; NSIS installs per user and registers the Windows launcher.
  Users open the app, select repositories with the native picker, complete
  profile/model setup, and do ordinary ticket work without opening a shell.
  A native single-instance gate focuses the existing window and forwards a
  second repository launch when the host session supports it. The CLI remains
  an optional automation and migration surface over the same typed handlers.
- Releases build three platforms by default (Linux x64, macOS Apple Silicon,
  Windows x64); the six-platform matrix is a manual `all_platforms` input,
  because GitHub bills macOS minutes at ten times Linux. CI validates on
  Linux for every push with superseded runs cancelled; the Windows job runs
  for pull requests or on request. Linux is locally built and exercised; Windows is a first-class
  CI/build target using the same UI, core, and platform boundary.
  `tools/build_desktop.py` stages the frozen core under Tauri's target-triple
  sidecar name, verifies and embeds the complete Pi 0.84.2 distribution, and
  emits Debian/AppImage, app/DMG, or NSIS outputs. The app supplies its private
  resource root to the core, whose existing per-file integrity gate verifies
  Pi before use. Pi executable paths are archive-relative because the pinned
  Windows archives are flat while Unix archives include a `pi/` directory.
  The desktop builder resolves the concrete build-tool path before launch so
  Windows uses the installed `npm.cmd` shim without invoking a shell.
  Linux x64 emits Debian and AppImage installers; Linux arm64 emits the verified
  Debian installer because the upstream arm64 `linuxdeploy` AppImage path is not
  currently reproducible on GitHub's native arm runner.
  Release workflow run `33583291812` passed all six matrix jobs at code commit
  `f8cb223`; its Windows x64 artifact contains
  `TicketBunker_0.5.0_x64-setup.exe` plus the portable bundle, and both published
  SHA-256 files were independently verified after download.
  Git execution is repository-serialized inside each TicketBunker process, and
  branch creation holds the source-repository transaction throughout its Git
  sequence so ticket-open background fetches cannot race it on slower hosts.
  Release construction also opens the staged Pi RPC runtime and
  requires a valid provider/model catalog from the pinned auth bridge. Native
  installers are flattened into `dist/releases/` with
  adjacent SHA-256 files. The final Linux Debian (89 MB) and AppImage (158 MB)
  were built from the 0.5.0 frozen core; both checksums pass, the Debian payload
  contains the core and complete Pi distribution, and the frozen core passed
  the real browser interaction flow.
- `application.release.check` performs a bounded GitHub release API read,
  accepts only stable semantic versions and exact official TicketBunker GitHub
  release/download paths, selects the current host's installer, and returns the
  URL for the operating system browser. Cached URLs are revalidated on every
  read. The privileged app never renders remote release HTML and never executes
  a downloaded installer. Failed automatic checks are timestamped so they do
  not repeat on every launch; manual **Check now** remains available in Settings
  → System.
- Tagged Windows releases now require the repository's Authenticode certificate
  and password secrets. The workflow signs the frozen core before bundling,
  supplies the certificate thumbprint to NSIS, and refuses to publish unless
  every Windows executable verifies as valid. Manual workflow runs may still
  produce explicitly unsigned test artifacts. Exact maintainer setup is in
  `docs/release-signing.md`; no signing secret enters the repository or bundle.
- The CLI-first portable archives remain release assets with the same frozen
  core, Pi distribution, adapter, licenses, manifest, and install scripts.
- The wheel/pipx package and stdlib zipapp remain small core-only options. They
  now correctly include the pi adapter, version/checksum manifest, and license;
  an ordinary `tb init` automatically downloads, verifies, installs, and
  records the pinned platform runtime. `TB_AGENT_PI_BIN` remains only an
  explicit override; `--core-only` is the no-download choice.
- Frozen hooks pin the installed `tb` launcher. Detached one-file workers reset
  their PyInstaller environment so the parent's temporary extraction cleanup
  cannot strand a user-requested synthesis. Git hooks themselves now record
  staleness only and do not start that worker.

### Deterministic product exercise

Run:

```sh
python3 tools/mock_product_loop.py
python3 tools/mock_product_loop.py --json
```

The script creates disposable local source and companion remotes, never reads
an existing user repository, and exercises 15 GitHub-shaped `DEMO` tickets
with two Git users. It proves first sync, revision-level no-op sync, a
three-ticket update,
active/faceted catalog queries, zero-ticket sparse setup, one-ticket
fetch-on-open, separate convention-named branches, remote reconciliation, two
clean doctor reports, and the Work UI. Measurements and explicit live-boundary
caveats are in `docs/mock-product-loop.md`; the scenario is also a unit test.
Synthesis tests separately cover explicit request coalescing, existing-hook
preservation/restoration, the gated worker path, frozen-worker isolation,
durable-input provider skipping, Pi-shaped CLI parsing, bounded malformed-draft
repair, fresh-clone session creation, nested-publication suppression, and a
real post-commit hook that records a change without publishing. Focused tests
also cover cached-first truth refresh, private coworker session reuse,
read-only Pi tools, selected synthesis-reference validation/persistence,
checkout-confined editor opening, detached viewer shutdown, and gated
child-only report updates, private proposal-rejection diagnostics, and shared
CLI/HTTP local editor selection, guarded built-in source saves, external-editor
fallback, stale-viewer replacement, compact shared-truth indicators, guarded
absolute-path copying, nested Markdown lists, and source-selection coworker
references. The current full suite is 263 Python tests, 9 Rust host tests, 2
React component tests, and the real-browser standalone interaction flow.

Browser QA covers desktop, tablet, and mobile sizes. Ticket/Agent switching,
the persistent rail, compact shared-truth indicators, coworker launcher/panel,
topic tabs, scope selection, Work panel, responsive stacking, and JavaScript
deep links are inspected. A headless-Chrome interaction check also selects
Combined synthesis, verifies an ordinary click stays inert, opens the
contextual bubble only for a real drag selection, and confirms focused typing
hides stale selection UI. The included editor is checked at desktop and
mobile widths; a real textarea selection opens the drawer with its source
reference, survives reload, collapses, and reopens without horizontal overflow.
This does not replace the HTTP/CSRF tests.

## Existing legacy agent pipeline

The established agent loop remains green: ticket → branch → Git event marks
staleness → user presses Synthesize → queued bootstrap → compose → scaffold →
harness → lint/anchor-gated publish → master regeneration.
`tb merge` reconciles components through the same gates. `tb survey REPO --run`
publishes a gated repository survey. Git hooks record checkout, commit, merge,
and rebase changes without network or model work. The state-hub action is the
ordinary synthesis route; `tb run` remains the direct CLI path.

Pi/Codex synthesis now gets one bounded format-repair pass when the first
draft fails lint or anchor checks; the exact findings go back to the configured
harness and the repaired child still crosses the unchanged publish gates.
Agent-side `tb infer` commands no longer publish the companion checkout one by
one while the worker is active; the worker owns the single final publication.
The CLI subcommand name is separate from the harness `--cmd` option, fixing the
Pi-only `NoneType.startswith` crash, and fresh clones recreate ignored local
session directories before writing forms.

Pre-agent refresh now takes the base from the committed project locator (for
Coal, `ecs`) instead of silently defaulting new tickets to `main`. If a
collaborator has only the already-fetched remote-tracking base, observation
falls back to `origin/<base>` without changing the worktree. A stopped refresh
keeps its concrete reason and next Git action in synthesis state and its exact
diagnostic in View log; the generic “deterministic refresh failed” sentence is
no longer the only explanation.

Doctor and the ticket state hub distinguish explicit synthesis requests from
legacy automatic-worker history. An old `head-advanced`/Git-event failure can
no longer claim that the user requested synthesis: doctor reports tracking as
ready and the ticket remains **Not synthesized**. Only state carrying the
explicit `user-requested-synthesis` marker can become **Synthesis stopped** and
offer Retry/View log.

Codex and the bundled pi 0.84.2 runtime have both been live-verified. On 26
August 2026, managed pi with `openai-codex / gpt-5.6-sol` generated TB-1 in a
disposable clone on the first pass: all Reader Brief and full-trail claims
passed report lint, every `[real]` anchor resolved, and the published stamp
named the correct harness/provider/model. The deterministic 15-ticket product
exercise intentionally does not run an agent report.

## Remaining product work

1. Retire the remaining legacy-only paths: the `tb` ticket/comment/set
   commands still address companion layouts; route them through the Bunker
   runtime and database the way reports and the coworker session now are, and
   let `tb view` open a repository attached only through a Bunker.
2. Extend the reference service and app with join requests and owner
   approval, member management (roles, removal), deploy recipes for platforms
   that provide HTTPS and a persistent disk, and ticket/report synchronisation
   Add GitLab and Jira transports behind the same tracker source, incremental
   pull after the cursor instead of whole-snapshot reconcile, and deletion
   propagation for tickets and reports.
3. Add an OS-vault backend behind the credential reference store and native
   sign-in for services that require authentication before redemption.
4. Add signed/versioned JSON and markdown export/import for local-only and
   air-gapped Bunkers, then verify round trips from SQLite and a remote replica.
5. Remove legacy companion publication only after parity and verified imports;
   keep its reader available for a documented deprecation window.

## Non-negotiable implementation rules

- Stdlib-only core; no package dependency additions.
- Update `DESIGN.md` in the same commit as meaningful behavior changes.
- Resolve legacy companion/runtime paths through `ticketbunker/layout.py`.
  New durable Bunker data must go through `ticketbunker/bunkerdb.py`; callers do
  not construct SQLite paths or issue SQL outside that module.
- Never weaken typed intake, structural ledgering, report lint, anchor checking,
  or provenance separation to make a test pass.
- User failures must say what happened and what to do next; a raw traceback is
  a bug.
- Treat this repository's own `.tb/` and the real voxel repo at
  `/mnt/c/Users/Tim/Desktop/voxel-engine-starter` as real data. Use disposable
  directories for experiments unless Tim explicitly requests a live action.
- Use focused commits and push this branch after each verified behavior slice.
- After package changes, run `pip install --force-reinstall .` before testing the
  installed `tb`, or explicitly use `PYTHONPATH=.` for source-tree tests.

## Key paths

| Concern | Path |
|---|---|
| Design authority | `DESIGN.md` |
| Bunker SQLite domain | `ticketbunker/bunkerdb.py` |
| Remote service protocol | `ticketbunker/remote.py` |
| Reference service | `ticketbunker/service/storage.py`, `server.py`; `tb serve` |
| Machine-local credentials | `ticketbunker/credentials.py` |
| Desktop/API action registry | `ticketbunker/application.py` |
| Legacy layout | `ticketbunker/layout.py` |
| Shared commands | `ticketbunker/commands.py`, `ticketcommands.py` |
| Companion storage | `ticketbunker/project.py` |
| Committed references | `ticketbunker/ticketindex.py` |
| GitHub transport | `ticketbunker/integrations/github.py` |
| Branch workflow | `ticketbunker/workflow.py` |
| Diagnostics | `ticketbunker/doctor.py` |
| Read model | `ticketbunker/api.py` |
| Ticket viewer | `ticketbunker/viewer.py` |
| Local source opener | `ticketbunker/editor.py` |
| Config editor | `ticketbunker/config_viewer.py` |
| Typed intake | `ticketbunker/handlers/` |
| Report gates | `ticketbunker/processors/reportlint.py`, `anchorcheck.py` |
| Harnesses | `ticketbunker/adapters/harness.py`, `ticketbunker/harness/pi/tb.ts` |
| Host platform boundary | `ticketbunker/platform_services.py` |
| Safe update discovery | `ticketbunker/release_updates.py`, `docs/release-signing.md` |
| Pi runtime/readiness | `ticketbunker/agent_runtime.py`, `ticketbunker/runtime/pi-runtime.json` |
| Releases | `tools/build_release.py`, `tools/ticketbunker.spec`, `.github/workflows/release.yml` |
| Product exercise | `tools/mock_product_loop.py` |
