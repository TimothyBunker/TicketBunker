# Architecture review — 6 September 2026

Reviewed branch `claude/jira-git-context-agent-artex0`, pulled with a clean
worktree from `88090af` to `2950795`. This records implementation evidence,
not a replacement design. No live Coal data, tracker writes, or model calls
were used. Experiments used disposable test fixtures.

## Assessment

The product is now a Bunker-first native application with an implemented
self-hosted service, not a browser wrapper over companion Git. The main
remaining risk is incomplete authority and identity handling at the cutover
between the new database and the old runtime/workflow engine.

The important implemented boundaries are:

- React/TypeScript is one interface for Tauri and the loopback fallback.
  Native actions cross a private JSONL worker; browser actions cross the
  token-protected action endpoint. Both dispatch Python commands.
- Bunker owns SQLite, membership, shared configuration, and remote service
  connectivity. Recursive Projects own repository allowlists; checkout paths
  are device-local. Cloning source does not enroll a member.
- The reference service implements owner setup, sharing, single-use
  invitations, enrollment, membership reads, snapshots, and transactional,
  revision-checked changes. It is no longer merely a proposed protocol.
- GitHub boards feed normalized tickets through field mappings and typed
  synchronization. Jira and GitLab templates/token setup are present, but
  their sync transports are not implemented.
- `bunkerruntime.py` adapts database tickets to the established observation,
  composition, harness, lint/anchor, and report pipeline. Intentional synthesis,
  Standing/checklists/plans, configured build/test checks, and explicit tracker
  update drafting are current features. Coworker remains a read/advice/report
  proposal agent, not a source-work agent.

## Findings, in priority order

### 1. Remote publication does not enforce the report trust contract

`ticketbunker/service/storage.py:816` accepts a report based on membership,
identity, size, and revision. It does not run report lint or authenticate
evidence of anchor verification. Initial publication has the same limitation
at `_validated_publication`. The ordinary local harness does run its gates;
the alternate authenticated HTTP write path does not.

Reproduced against the real in-process HTTP service: submitted an incomplete
report containing a `[real]` claim referencing `missing.py:999999`; the service
accepted it and returned it in its snapshot. Adding that child also left the
Combined record unchanged. The service accepts Combined as client text rather
than deriving it from all accepted children.

Impact: an authorized API client can bypass the claimed publication discipline,
and shared Combined is not mechanically guaranteed to match its children.
Server-side structural validation, an explicit verification trust boundary,
and authoritative Combined derivation need to be settled. A service without
source access cannot itself prove a source anchor merely by parsing a stamp.

### 2. Imported legacy data remains a live authority

`ticketbunker/bunkerdb.py:1796` (`_refresh_legacy_ticket`) is called on every
`get_ticket`. It rewrites imported ticket data, deletes the ticket's current
reports, and replaces them from the attached legacy files. It does not check
whether the Bunker has since become shared or require service acceptance.

Existing test `test_companion_import_is_read_only_and_copies_tickets_and_reports`
explicitly changes the legacy master after import and expects a later Bunker
read to adopt it. Thus this is tested behavior, but contradicts the documented
one-time import and single-authority contract.

Impact: reading an imported ticket can overwrite database/shared report state
from an older store. Migration needs a completed cutover boundary, not just a
source-unchanged receipt.

### 3. Branch creation is still companion-only

`ticketbunker/workflow.py:20` requires `project.load_locator`; both start and
attach still use that legacy workflow. The standalone ticket rail calls
`workflow.start` (`desktop/src/TicketWorkspace.tsx`).

Reproduced with the Bunker-native runtime fixture: dispatching `workflow.start`
for its existing ticket returns `not_initialized`, “Run 'tb init' before
starting ticket work.” No branch is created.

Impact: new-app onboarding reaches a branch creation dead end even though
synthesis works after a branch has been created externally. The Bunker-native
workflow must consume its Bunker/ticket/repository identity without introducing
a companion locator.

### 4. Multiple repositories are stored but not respected end to end

`bunkerruntime.materialize` selects the first attached repository and uses one
runtime directory per ticket, with child filenames keyed only by branch.
`bunkerruntime.resolve` discards the repository identity correctly discovered
by `find_bunker_ticket`. The ticket UI likewise selects the first local path
and uses branch names alone for report scope.

Reproduced with two attached repositories: lookup correctly identified the
second checkout, but resolving its ticket produced a runtime marker naming
the first repository. Repository ordering comes from the name-sorted query.

Impact: synthesis can use the wrong repository binding; identical branch names
cannot be reliably selected in the interface. Carry repository identity through
runtime, selection, source links, coworker context, and publication.

### 5. Accepted database reports and runtime working copies can diverge

`bunkerruntime._seed_reports` never replaces an existing runtime child. Snapshot
sync updates SQLite but leaves those files intact; truth/coworker still read
the runtime copies.

Reproduced by materializing accepted version 1, accepting version 2 in the
database, and materializing again: the database returned version 2 while the
runtime still contained version 1. There was no local edit to preserve.

Impact: the report the user reads and the report the coworker discusses can
differ after shared updates. Separate accepted revisions from dirty working
drafts and reconcile clean copies, with explicit conflicts for dirty ones.

## Further gaps and caveats

- Sync still transports whole snapshots, including all report bodies. The
  client caps a response at 8 MiB (`remote.py`), so growth can prevent joining
  or syncing; changing the physical database engine would not solve that
  protocol limit. Incremental/paged sync and deletion propagation remain work.
- Ticket listing loads all ticket payloads before filtering and pagination
  (`bunkerdb.list_tickets`), unlike the bounded-index design description.
- Build/test checks run shell commands with inherited host environment in a
  detached worktree (`processors/stages.py:552`). A worktree is not a process
  sandbox. This new subprocess route also omits the headless platform options
  used by other helpers. Windows console behavior and process-tree timeout
  cleanup need targeted verification; they were not live-tested here.
- The frontend tests emit invalid HTML nesting warnings when the floating
  source toolbar places a `div` inside a Markdown paragraph. Assertions pass,
  but the warning remains real; `FloatingActions.tsx` is the relevant component.
- Documentation mixes current and legacy behavior: combined-vs-branch defaults,
  CLI parity, import ownership, test counts, signing exceptions, release matrix,
  and “no write-back” conflict with newer sections or implementation. Explicit
  GitHub comment posting now exists. Follow code/tests for current behavior and
  resolve contradictions in DESIGN rather than treating every paragraph as
  simultaneously implemented.
- Join approvals, role changes/removal, deployment recipes, an OS credential
  vault, and portable verified export/import remain incomplete. PostgreSQL is
  a possible service implementation, not a shipped backend.

## Validation performed

- Source-tree Python suite: **334 passed** on the pulled baseline.
- Frontend TypeScript/Vite production build: **passed**.
- Frontend unit/component suite: **28 passed**, with the nesting warning above.
- Rust host suite: **9 passed**.
- Existing Playwright standalone flow: **passed**, using a disposable
  Bunker-native fixture and stub-generated report. It exercises settings,
  Project navigation, configuration, ticket/report reading, responsive widths,
  and opening the source editor. It does not create a branch or prove a
  two-device report reconciliation flow.
- Additional scratch probes reproduced findings 1, 3, 4, and 5. Finding 2 is
  established by the current implementation and its existing import test.
- No Windows executable, production hosting, live provider quality, or new
  GitHub Actions run was verified during this review.

## Narrow release repair

The artifact-only publish job has no checkout. Its `gh release create` call
now names `--repo "$GITHUB_REPOSITORY"` explicitly, keeping `--verify-tag`,
generated notes, and pre-release classification. A regression test executes the
workflow shell with a recording CLI stand-in in a temporary non-repository
directory for stable and pre-release tags. It failed before the fix and passes
afterward; all four release tests pass.

No release was published or tag moved during this review. Re-running the old
failed tag job does not pick up a workflow edit on a newer commit; recovery
must deliberately use the corrected workflow or publish existing artifacts
with an explicit repository.
