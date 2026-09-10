# Workspace QA campaign

Goal: exercise the product as a user would, fix reproducible failures, and retain
the simulations and regression tests. Source baseline: `2dd563f` (2026-09-09).
All repositories, tickets, remotes, profiles and databases in this campaign are
disposable. No real tracker/provider credentials or installer releases are used.

## Tickets

| Ticket | Scenario and acceptance | State |
|---|---|---|
| QA-01 | Pending reads/saves versus edits, navigation and changed checkout: never overwrite a newer draft or save to the wrong file/repository. | Verified |
| QA-02 | Filesystem edge cases: Unicode/spaces, CRLF, unreadable/binary/oversized files, escaping links, invalid directory input; actionable errors and unchanged bytes on refusal. | Verified, including seeded/near-limit cases |
| QA-03 | Editor lifetime and scale: repeated tabs/model reuse, closing files, pane toggles, large files; bounded retained resources and no repeat report parsing while typing. | Verified; native timing excluded |
| QA-04 | Coworker scope races: delayed replies, switching file/ticket/branch, selected unsaved code, no synthesis; context must stay explicit and report gates intact. | Verified with delayed transport doubles |
| QA-05 | Keyboard-only navigation: explorer/context reopen, file tabs, close dialogs, folder picker, error recovery; focus and drafts remain reachable. | Verified in Chromium |
| QA-06 | Multiple windows and process recovery: stale revisions, lost/delayed replies, reopen/reload; preserve independent drafts and state conflicts honestly. | Verified; crash/closed-window rescue excluded |
| QA-07 | Native/backend parity: run targeted real Windows Python/Git tests and native transport checks; distinguish backend success from untested OS dialogs. | Verified backend; OS dialogs pending |
| QA-08 | Repeatable browser stress: multiple simulated user journeys, both mock bugs fixed via UI, clean teardown, useful failure artifacts. | Verified: 30/30 final journeys |
| QA-09 | Shared-project restrictions: owner/member configuration versus local attachment, selection/replacement safeguards; no accidental shared writes. | Verified API/server |
| QA-10 | Integrate and replay: full Python/UI/Rust suites, browser workflows, fresh assets, docs and focused commits; report limitations without claiming coverage we lack. | Verified source; no installer release |

## Execution rules

- Agents own non-overlapping files; the main agent integrates fixes and builds.
- Reproduction precedes a fix; each confirmed defect gets a regression assertion.
- Browser assets are built only when no browser run is using them.
- Latency samples are observations, not portable performance promises.
- A passing mock harness does not establish live provider authentication or quality.
- Native visual/keyboard gaps are recorded separately from backend parity.
- Findings below record evidence, changes and verification as work completes.

## Findings and verification

### Reproduced defects and fixes

- QA-01: delayed first opens stole selection; a pre-save refresh replaced newer
  saved text; 31 simultaneous opens bypassed the 30-file limit. Latest-intent,
  per-file epochs and pending reservations now guard these paths. Existing
  digest/branch/checkout identity refusals remain intact.
- QA-02: CR-only and mixed endings changed on no-op saves; null-character drafts
  created binary files; atomic replacement bypassed read-only mode; malformed
  paths escaped actionable errors; failed revalidation left temporary drafts;
  exactly 2,000 directory entries falsely announced truncation.
  A 1.35 MB periodic mixed-ending file also exceeded an eight-second encoder
  timeout. Common-edge matching and bounded changed-region alignment reduce
  near-limit periodic edits to roughly 45–58 ms on this machine (encoder only).
  One hundred seeded edit cases then caught a blank line disappearing where
  a retained CR met an emptied line's LF; that now has an explicit regression.
- QA-03: closed Monaco models accumulated until the whole workspace unmounted.
  Models now track open files, with a bounded fallback; formatting is model-local.
  The 600 KB editor contract and repeated model replacement are component tests,
  not native memory/latency measurements.
- QA-04: nine delayed-message/state/proposal/forget/quote/note-reply scenarios
  exposed cross-context display, stale state replacement and quote/draft loss.
  Responses and drafts now carry full route identity and request ordering.
- QA-05: collapsing drawers lost keyboard focus; the selective Monaco bundle
  omitted Ctrl+M Tab-focus mode. Real keyboard journeys failed before fixes and
  pass afterward, including close/cancel focus and rejected-save recovery.
- QA-06: two windows retained drafts only while open; reload replaced one draft
  with the other. Session-storage rescue now preserves each window's dirty
  buffer **and original revision**, so reload cannot seize the other window's
  durable recovery record. A stronger browser regression exposed that second
  flaw after a buffer-only assertion initially passed. Lost recovery receipts
  can be retried only while the complete stored snapshot still matches.
- QA-09 found no authorization defect: eight real HTTP scenarios verify member
  local attachment without shared writes, server-side permission enforcement,
  forged cached capability rejection, owner configuration propagation without
  local paths, Bunker registry restrictions and stale allowlist rejection.

### Durable regression sources

- `desktop/src/RepositoryWorkspaceLifecycle.test.tsx`
- `desktop/src/WorkspaceEditor.test.tsx`
- `desktop/src/CoworkerDrawer.races.test.tsx`
- `tests/test_editor_boundaries.py`, `tests/test_directory_browser.py`
- `tests/test_workspace.py`, `tests/test_workspace_membership.py`
- `desktop/tests/workspace-{keyboard,multiwindow}-simulation.e2e.mjs`
- `desktop/tests/workspace-simulation-repeat.mjs`

Run `npm run test:simulation:repeat --prefix desktop -- --runs=10` after building
the UI. Each iteration creates fresh repositories, tickets, settings and a worker.
See [simulation instructions](workspace-simulation.md) for manual exploration,
screenshots, custom output locations and bounded timeout handling.

### Final verification (2026-09-09)

| Check | Result |
|---|---|
| Full Python discovery | 432 tests, OK; three expected platform skips |
| Full frontend suite | 121 passed |
| Rust tests | 12 passed |
| Production frontend | TypeScript + Vite build passed; bundled editor size warning remains |
| Existing browser hardening | Passed |
| Final repeated browser campaign | 30/30: ten role-play, ten keyboard, ten strengthened multiwindow; no retries/timeouts |
| Native Windows source boundaries | 15 tests, OK; one expected trailing-space filename skip |
| Native Windows workspace / directory / membership | 7 / 4 / 8 tests, all passed |
| Repeat-runner failure handling | Deliberate one-second timeout exited in 1.49 s and left no new fixture processes (Linux) |
| Whitespace/integration checks | `git diff --check` clean; unrelated worktree directories preserved |

Local detailed evidence is `/tmp/tb-qa-workspace-final/campaign.json` and its
per-run directories (`010-roleplay`, `010-keyboard`, `010-multiwindow` are
representative). This final campaign took about 190 seconds; it follows the
earlier failure reproductions, shorter replays and independent agent test waves.
Temporary evidence is not durable across machine cleanup; committed scripts
recreate it, and CI uploads the three-journey evidence artifact.

The 410 diagnostic keydown-to-frame samples had per-run medians of 1.3–1.9 ms
and p95 values of 7.5–15.8 ms on this host. They do **not** establish native
end-to-end input latency, installed-app performance or cross-OS equivalence.
Campaign browser/worker children exited; a pre-existing manually launched lab
was deliberately left untouched.

### Explicit limits / follow-up checklist

- [ ] Interactive installed Windows/Linux folder-dialog, typing, close and reopen
  smoke tests. Native Python/Git/JSONL-worker tests are not native GUI tests.
- [ ] Crash-proof independent recovery for conflicting windows. Current rescue
  is session-local; save or copy a conflicting draft before closing its window.
- [ ] Durable unsent chat drafts. Current per-context drafts remain in memory;
  published/sent conversation storage is unchanged.
- [ ] Live model authentication, report quality and external tracker-provider
  tests with deliberately provisioned accounts. This campaign uses no secrets.
- [ ] Native input-latency profiling and large-workspace resource measurements.
  Browser timing samples and mocked model bounds are narrower evidence.

No installer release, real project change, remote tracker write or model call is
part of this campaign. The mock fixes are not copied into any user's repository.
