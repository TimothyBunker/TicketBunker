# Two-hour QA campaign — 2026-09-10

Requested duration: approximately two hours. Started **06:45:58 UTC**;
target completion **08:45:58 UTC**. Baseline `39621fd`.

The interactive campaign was interrupted around **07:18 UTC** by a usage limit,
then resumed at **13:35 UTC**. Do not count that inactive interval toward the
requested active work. The already-running browser soak completed independently.
After resumption, target roughly another 85 minutes of active review/replays;
the original wall-clock deadline is no longer the active-work accounting.

Four parallel tracks use disposable projects, Git remotes and device data.
No real project, credentials, provider calls, installer or release changes.
The browser lab uses Grid Garden / LAB tickets. Some older backend fixtures
still name synthetic tickets ECS-4/ECS-9; those keys are invented fixture data,
not copies of Tim's Coal tickets or this repository's real dogfood bunker.
This continues the [first campaign](qa-workspace-campaign.md), not a claim that
all remaining product limitations can be removed in two hours.

## Working tickets

| ID | Track / acceptance | Status |
|---|---|---|
| LONG-01 | Backend failures: invalid source/recovery, locks, paths, worktrees; errors actionable and originals intact. | Fixed; independent review and Linux/Windows replay passed |
| LONG-02 | Save/discard/close: failed recovery, delayed saves, native close; no silent loss or trapped dialogs. | Fixed and targeted replay passed |
| LONG-03 | Real interface fault journey: changed checkout, unavailable worker, dirty navigation; repeatable artifacts. | Fresh failure/role-play replay passed; intermittent stops retained |
| LONG-04 | Repository/overlay navigation races: replaced binding, deleted ticket, delayed metadata, invisible retained views. | Regressions and real dirty-binding replacement passed |
| LONG-05 | Shared Bunker two-participant lifecycle and authorization regression expansion. | Integrated suites plus 645 repeated shared regression executions passed |
| LONG-06 | Scale and responsiveness: repeated navigation/models, large paths/trees/reports and bounded worker load. | 3,000-ticket paging; capacity tests; 4,245-cycle steady run passed |
| LONG-07 | Independent review of fixes and expanded negative tests. | Agents A/B review completed; reproduced defects fixed and replayed |
| LONG-08 | Repeat campaigns across the two-hour window, failure cleanup and deterministic reproduction. | In progress |
| LONG-09 | Native Windows targeted parity and native transport boundary checks. | Full 513-test native Windows run passed, four platform skips |
| LONG-10 | Full integration, committed docs/evidence, source commit/push and honest remaining limitations. | Queued |

## Schedule and rules

- First quarter: new failure scenarios and baseline soak.
- Middle hour: reproduce/fix, independent review, new scenarios and fresh-asset replay.
- Final quarter: integration suites, longer repeated journeys, documentation and handoff.
- Main agent coordinates production asset builds; never rebuild under a running journey.
- Reproductions and assertions precede product fixes. Keep file ownership separate.
- Every automated pass states what is mocked, native or real; no live-model claim.
- Evidence stays in dedicated temporary output directories; scripts/checklist are committed.
- Wall-clock duration is checked explicitly; completing a short batch does not end the goal.

## Results log

### First half-hour

- Source saves: reproduced a parent-directory symlink swap redirecting a write,
  malformed Unicode paths/content, and corrupt/locked recovery storage. Added
  held-directory staging on POSIX, checked Windows fallback and actionable errors.
- Save/close: delayed saves cannot discard newer edits; successful source writes
  remain successful if recovery subsequently fails. Explicit discard can close
  without taking over another window's recovery revision. Native-close failures
  remain actionable instead of silently dismissing the guard (mocked native API).
- Navigation: initial and ordinary refresh failures can retry; refreshed ticket
  metadata replaces old context; stale responses cannot resurrect removed overlays.
  Exact-content retry acknowledges a lost save receipt only in the same checkout.
  Detached HEAD additionally requires the original commit, even with identical bytes.
- Recovery: unmount flushes per-window rescue before the debounce; malformed rescue
  is preserved, not silently overwritten, with save/copy guidance. Durable recovery
  still operates. This is not crash-proof storage or cross-window buffer merging.
- Context: hidden/spaced filenames retain identity in file chats. Report notes match
  exact relative paths (absolute paths require the explicit root), retain nested
  click targets, and parse the verified stamp once per report, not once per claim.
  Coworker read failures expose Retry without discarding the question draft.
- Sharing: incomplete snapshots cannot erase local bindings; topology validation
  rejects cycles among existing Projects; snapshot assembly reads one transaction;
  old pull completion cannot overwrite a newer cursor. Tests also exercise revoked
  bearer/member, config conflicts, rollback, allowlist removal and service restart.
- Transport: malformed roots, invalid IDs, deeply nested JSON, oversized lines and
  closed pipes do not crash the persistent worker. Legacy lone-surrogate JSON data
  is escaped on output. Editor Git queries use the Windows headless helper options.

Intermediate verification: full frontend 161/161 with TypeScript; initial full
Python 447 tests (three platform skips), before subsequent added tests. Sharing
43 Linux / 12 native Windows; worker six Linux / six native Windows. These counts
overlap and are not a final aggregate. Installed native GUI and providers untested.

Real browser failures use fresh Grid Garden labs: initial-service failure/retry,
branch changes, linked worktrees, lost successful save response, and actual separate
worker stop/restart. A dirty worktree replacement confirms Cancel retains the old
checkout and Save writes there before attaching the new one. The first stable soak
stopped after 15 passes plus one test synchronization failure; the Git transition
test now awaits the save receipt. Failed evidence is retained separately, not counted
as a product defect. Resumed 20-minute soak started 07:02:29 UTC at
`/tmp/tb-trackc-soak-20m-v2`; it holds the current assets unchanged until completion.

### Resumption and independent review

- The completed soak ran 07:02:27.984–07:22:46.531 UTC: **156/156 journeys**,
  39 full batches, no failed assertions, 539,125 artifact bytes. Assets may now
  be rebuilt. This was repeated fresh-fixture testing, not one long app session.
- Independent review reproduced a temporary-name collision deleting a pre-existing
  file during failed cleanup; cleanup now verifies ownership. It also found the
  diagnostic error boundary itself could fail while locating its data directory.
- A pre-enrollment snapshot had the same cursor as the post-enrollment snapshot,
  defeating stale-pull protection for membership. Actual enrollment now advances
  service revision/cursor; idempotent enrollment must remain unchanged.
- UI review found a late open-file response appearing clean beneath a new branch
  header and duplicate recovery tabs hiding one draft. Both have regressions/fixes.
- Browser JSON failures now give uncertainty/recovery guidance instead of leaking
  null-property errors or JSON parser exceptions. Deeply nested local HTTP requests
  return a readable rejection instead of closing their response connection.
- Further work: per-window unsent conversation recovery/close guard and one
  persistent-worker resource/navigation soak. Native installed UI remains manual.

### Fresh build and steady-state testing

- New unsent conversation recovery/close regressions pass, including hidden or
  unmounted drawers, text plus quote restoration, failed storage, and concurrent
  local views. Close Save never invokes the coworker. Recovery is explicitly
  per-window and bounded; forced shutdown with unavailable storage remains unsafe.
- The first steady-session pilot reproduced Ctrl+S targeting a hidden repository
  editor three times. Monaco's unscoped `addCommand` was replaced with its
  editor-scoped, disposable `addAction`. Fresh keyboard pilot: 20/20 cycles passed.
- Fresh production assets pass all eight browser failure cases, including ordinary
  refresh retry, detached-commit identity and dirty worktree replacement. The
  persistent worker run began **13:44:35.648 UTC**, scheduled for 30 minutes, at
  `/tmp/tb-trackc-steady-30m`. No asset rebuild is allowed during it.
- The 17-check real HTTP/process collaboration loop passes on Linux and native
  Windows. Native Windows exposed UNC path quoting in the dummy SSH helper;
  forward-slash paths plus shell quoting fix that fixture, not product behavior.
- CI now runs all default failure cases and a short retained-editor keyboard-save
  journey. The extended 20/30-minute runs remain explicit local campaign commands.

The first persistent run stopped after **2,106 successful cycles / 15m54.732s**:
cycle 2,107 produced no save response to Ctrl+S. The draft remained unsaved and
visible. This is **not a completed 30-minute pass**. The harness did not record
whether Save was enabled at keydown; source-save completion precedes recovery
completion, which keeps Save disabled temporarily. That is a possible cause,
not a proven explanation. Preserve `/tmp/tb-trackc-steady-30m` as failed evidence.
The readiness-aware replay waits for the visible Save control to be enabled,
asserts editor focus, and records readiness/requests without retrying shortcuts.
Its 141-cycle / 60.226s pilot passed; a fresh 30-minute run began
**14:02:55.954 UTC**, `/tmp/tb-trackc-steady-30m-v2`.

That readiness-aware run completed: **4,245 cycles / 1,800,426ms
(30m00.426s), no browser errors, no readiness deferrals**. It kept one worker
and one page alive, with four models and two retained editors throughout.
Passing this run does not identify the cause of the earlier cycle-2,107 stop.
Observed DOM elements stayed 319–321; worker RSS changed 39.7→51.3 MB;
JavaScript heap 16.1→52.3 MB (57.7 MB maximum). SQLite changed 12,288→20,480
bytes; the append-only Git ledger grew 6,407→17,320,440 bytes. These samples
describe this high-frequency workload, not a leak-proof or daily-size guarantee.
Artifacts total 1,337,874 bytes; the owned fixture/worker/browser were cleaned up.

Fresh final role-play, keyboard, multiwindow and all eight then-default failure
cases passed at `/tmp/tb-trackc-final-replay`. A controlled busy-recovery case
held a successful recovery reply, confirmed Save disabled and editor focused at
Ctrl+S, then verified an explicit enabled retry saved the newer draft correctly.
It is now the ninth default failure case. This proves the busy guard contract,
not the unknown cause of the first sustained-run timeout. A separate ten-cycle
pilot passed with exact-keydown repository/focus/readiness diagnostics enabled.

### Integration and boundary review

- A 3,000-ticket, four-project fixture verifies complete/unique reference-only
  pagination, parent/child scope, compound filters, Unicode/literal search and
  concurrent batch rollback. Thirty pages measured ~1.25s Linux / ~3.75s Windows,
  machine-specific. Paging still scans scoped JSON payloads in Python; this is not
  a proof of large-enterprise database performance or SQL-side pagination.
- Fixed literal `"None"` update times when manual tickets omit an upstream timestamp.
- Conversation capacity tests exercise 128 routes and ~1.99M stored characters;
  overflow does not truncate live input or evict existing unsent recovery. Twenty-
  four keystrokes parse no unrelated ticket payloads. Same-ticket reparsing remains
  a possible optimization, not a demonstrated correctness failure.
- Browser transport previously accepted arbitrary Host values before returning its
  launch token/local bootstrap data. It now validates loopback authority, exact
  listening port, optional same-origin Origin, and a single ASCII launch token.
  Independent malformed/duplicate-header probes pass on Linux and native Windows.
  No actual external DNS attack was performed; all requests targeted scratch HTTP.
- First native full run: 489 tests, 555.348s, four existing OS skips, three failures.
  One was the old expected cursor after membership versioning; two were subcases
  using Windows' WSL Bash launcher rather than native Git Bash. Corrected assertions
  and native Git Bash/stdin fixture selection pass targeted replay without reducing
  coverage on this machine. The clean second full run passed: **511 tests in
  539.641s, four platform skips, no failures/errors**, native Windows Python/Git.

Final integrated Linux run: **513 tests, 179.565s, OK (3 platform skips)**.
Final native Windows run, including the last report-process fix: **513 tests,
522.398s, OK (4 platform skips)**. Separate report-process/bootstrap targets:
13 tests, 19.804s, passed on native Windows.
Frontend: **194 tests passed**, TypeScript clean; Rust: **12 tests passed**.
These are separate suites, not a summed count of unique product scenarios.

Additional hardening repetition stopped on run 3/10 after two passes: after
Project-page sync reported success, the ticket list displayed a local connection
interruption and did not render TOOLS-7 within five seconds. There was no deliberate
transport abort in that step. Diagnostic replay was requested; do not count this as a
completed ten-run batch or dismiss the connection failure as environmental.

Fifteen independent instrumented hardening replays subsequently passed. Their
30 failed-request events were session heartbeat cancellations at deliberate
reload, not failed commands/list/bootstrap requests; every run received the
TOOLS-7 list. No fixture traceback or premature exit was captured. The original
failure remains unreproduced, not explained away. One earlier diagnostic run
passed its UI flow but its evidence collector hung during shutdown; that run is
instrumentation-aborted, not a product pass or failure.

A final launch-contract review found the three direct anchor-verification Git
queries and configured pipeline check shell omitted the existing headless
platform options. Two new regressions failed before and pass after the fix;
13 tests with the real scratch-worktree check flow pass. The argument assertions
do not claim to observe native console visibility. Full suites are rerunning.

Smaller-window hardening: three full flows each at **1280×720** and **1024×768**
passed. The chosen viewport now persists through the whole flow. At the smaller
size, the configuration drawer legitimately covers a background ticket target;
the test uses visible sidebar navigation to trigger the same unsaved guard, not
a forced click through the drawer. Original covered-target and wrong-selector
test failures are retained separately. The editor is usable but narrow with
both drawers expanded. CI preserves the request/fixture diagnostics and runs its
hardening flow at 1280×720; this is still Chromium, not installed native WebView.

Shared-concurrency repetition completed **129 iterations / 645 test executions
over 905.920s (15m05.9s), zero failures, exit 0**. Each iteration creates fresh
disposable HTTP/data fixtures for one-use enrollment races, enrollment rollback,
stale membership snapshots, malformed member-capability rollback and stale-pull
guards including the initial empty cursor. The runner stops on first failure;
these are repeated executions of five regressions, not 645 distinct scenarios.

## Remaining manual or architectural checks

- Installed Windows/Linux shortcut launch, native folder chooser, actual native
  close events and visible no-console behavior. Source tests verify arguments and
  mocked native callbacks; they do not prove packaged GUI rendering or interaction.
- Real Pi/provider login and live conversation/report quality. The collaboration
  loop substitutes only the model with the stub and exercises real report gates;
  workspace conversation journeys use unsent drafts, not live agent responses.
- Remote deployment over TLS/on separate machines. Sharing checks use two private
  local data homes and a real loopback HTTP service, not company infrastructure.
- Browser/app crash recovery is not guaranteed, especially when session storage
  or durable recovery is unavailable. Explicit save/send/copy guidance remains.
- Catalog filtering scans scoped ticket JSON before paging; 3,000-ticket results
  are not evidence of efficient 100,000-ticket or multi-company deployment.
- Repeated same-ticket recovery parsing and append-only workspace Git ledger
  retention may deserve profiling under real work. Synthetic rapid-save/resource
  samples should not be presented as normal daily storage consumption.
- External Git/filesystem changes cannot be made transactional by this app's own
  process lock. Checked identities/digests reject reproduced conflicts and redirects,
  but this campaign is not a proof against every adversarial filesystem interleaving.
