# Repository workspace simulation

Grid Garden is a disposable fictional project with two Git repositories and three
tickets. Its engine permits an off-grid coordinate; its renderer labels the first
cell as zero. LAB-1 and LAB-2 track those bugs, and LAB-3 tracks their shared
coordinate documentation. Both repositories include a failing `test_grid.py`.

The lab creates its own temporary Bunker database, settings, source checkouts and
bare Git remotes. Git fetches stay local. It does not use your Bunkers or work
repositories, tracker credentials, model credentials, or an external service.
Reports are deliberately absent: these are new tickets, not fabricated agent
findings. Chat checks use unsent questions; no live model invocation is tested.

## Automated role-play

From the TicketBunker source checkout, install the existing desktop dependencies
if needed, build the current UI, then run:

```bash
npm ci --prefix desktop
npm run build --prefix desktop
node desktop/tests/workspace-simulation.e2e.mjs
```

The script uses installed Google Chrome on Linux if present, otherwise Playwright's
Chromium. If neither is installed, run `npx --prefix desktop playwright install chromium`.
Set `TB_E2E_CHROME` to an alternative Chromium executable or `PYTHON` to your Python
executable. Browser testing requires Python and Git on PATH.

The role-play opens both repositories, fixes the engine bug in the real editor,
runs its test, checks independent drafts and save targets, opens multiple ticket
overlays, switches file/ticket conversation drafts, toggles drawers, and simulates
an external file edit to verify conflict protection. No API mocks replace product
commands; only initial fixture creation bypasses onboarding clicks.

Screenshots and `results.json` go to `ticketbunker-workspace-simulation` under the
system temporary directory (normally `/tmp` on Linux). Set `TB_SIMULATION_OUTPUT`
to choose a different directory. `TB_SIMULATION_HEADED=1` shows the automated browser.

Timing samples measure keydown-to-next-animation-frame in this run. They are
diagnostics, not end-to-end input latency guarantees or Windows/Linux comparisons.
The script does not verify native Tauri dialogs, native rendering, real providers,
or collaborator sharing. Temporary project data is removed after the run.

## Explore the same lab manually

```bash
PYTHONPATH=.:tests python tests/workspace_simulation_fixture.py
```

Open the printed `url` in your browser and choose **Use without agent**. Select
**Grid Garden → Garden engine** or **Garden renderer** in the sidebar. The printed
`repositories` paths are the actual disposable working trees, so you can make an
external edit or run `python -m unittest test_grid` there.

Suggested exercise: open `src/grid.py`, add LAB-1 and LAB-3 overlays, compare
`docs/design.md`, change the engine clamp from `width` to `width - 1`, and save.
Switch to the renderer, change `{position}` to `{position + 1}`, and rerun its test.
Keep one unsaved draft while navigating between repositories. Try editing the same
file outside TicketBunker before saving another draft to see the conflict guard.

Leave the fixture terminal open while exploring. Press Enter to stop the server
and delete the disposable lab. Saving in the browser affects only those temporary
repositories. Do not use this fixture for work you want to keep.

## Keyboard, concurrent windows, and repeated campaigns

```bash
node desktop/tests/workspace-keyboard-simulation.e2e.mjs
node desktop/tests/workspace-multiwindow-simulation.e2e.mjs
node desktop/tests/workspace-simulation-repeat.mjs --runs=3
```

The keyboard journey uses Tab/Enter after onboarding to navigate folder selection,
drawers, the editor, close dialogs and save errors. It checks keyboard escape from
the editor and focus restoration instead of assuming clickable controls are
keyboard-accessible.

The concurrent-window journey opens two browser pages against the same local
worker and checkout, writes distinct drafts, verifies the recovery conflict, and
reloads one page. The unique first-window draft must remain recoverable; a warning
alone does not count as draft preservation. A refresh also verifies that rescuing
one window has not overwritten the other window's durable recovery snapshot; both
windows are then reloaded independently. This is multiple local windows, not a
claim that multi-user remote sharing has been tested.

The repeat runner creates a fresh disposable fixture for every journey. Options
use `--name=value` syntax:

```bash
node desktop/tests/workspace-simulation-repeat.mjs \
  --runs=10 --journeys=roleplay,keyboard,multiwindow \
  --timeout=180000 --output=/tmp/ticketbunker-qa
```

`--runs` accepts 1–100; `--timeout` is each journey's deadline in milliseconds.
Choose `--journeys=roleplay` to repeat just the end-to-end bug-fixing story. Each
run gets its own log, results and screenshots; `campaign.json` summarizes failures
without stopping later runs. A failing regression is reported as failed, not
silently skipped. Do not rebuild UI assets while a campaign is running.

The repeat runner shuts down fixtures and browsers after each journey, including
assertion failures. Its timed-out children receive a graceful signal first; a stuck
process is forcibly stopped after a bounded grace period. Ctrl+C stops the current
campaign. As with any abruptly killed process, a machine crash or forced shutdown
can leave temporary files; none are placed in a user's source repositories.

### Recorded campaign: 9 September 2026

The final Linux Chromium campaign completed 30/30 journeys: ten full role-plays,
ten keyboard journeys, and ten concurrent-window journeys. There were no retries,
timeouts, browser exceptions or failed assertions in that campaign. A separate
deliberate one-second timeout correctly produced a failed/timed-out result and
cleaned up its fixture and browser.

The concurrent-window acceptance checks both layers: each tab recovers its own
unsaved text after reload, while the durable workspace recovery keeps the other
tab's accepted snapshot. Restoring a local rescue must retain its original
revision; adopting the newer shared revision would allow an unintended overwrite.
The regression was reproduced against the earlier build before verifying the fix.

Keyboard checks likewise reproduced lost drawer focus and an editor that could
not release Tab focus with Ctrl+M, then passed after those fixes. These results
cover the browser worker on this Linux environment—not native Windows/Mac dialogs,
whole-browser crash recovery, remote participants, or live agent conversations.

## Failure and lifecycle journeys

```bash
npm run test:simulation:failures --prefix desktop
```

The failure suite runs nine cases by default, each in its own disposable fixture:

- `initial`: refuse one initial workspace read, then retry using the interface.
- `refresh`: refuse one ordinary workspace refresh; a successful retry must clear
  that error without masking a distinct source-save error.
- `branches`: externally switch a real checkout and detach HEAD while a draft is
  open; stale saves must preserve disk and the draft. Return to its branch, save,
  and remove/switch ticket overlays without losing repository context.
- `worktree`: create a linked Git worktree, attach it through Project settings,
  and verify editor saves touch only that worktree.
- `detached`: move between two detached commits with identical source bytes;
  a draft from the first commit must not save into the second. Return to the
  first commit and verify saving works.
- `worktree-dirty`: replace a checkout attachment while a source draft is open;
  cancellation preserves the old attachment, and Save writes the old checkout
  before connecting the new one.
- `transport`: delay a real file read while selecting another file; then let a
  source save complete in Python but drop its HTTP reply. Reload/retry must
  reconcile the identical saved content without overwriting divergent changes.
- `worker`: stop the actual isolated browser-worker process, observe the offline
  interface, restart on the same port with a new PID/token, and recover the draft.
- `busy`: complete a real source save and hold its recovery reply. Type another
  edit while Save is disabled; Ctrl+S must not write it until recovery finishes
  and Save becomes enabled, then the shortcut must save to the correct checkout.

Use `TB_FAILURE_CASES=worker,transport` to select cases. Fault interception controls
only delivery or injects the deliberately failed initial response; it does not
replace successful command responses. External Git commands are scenario setup,
not claims that TicketBunker exposes those Git operations. Worktree attachment,
editing, navigation, retry and recovery use the real interface. This worker test
is an actual process restart, but is not a simulated machine/browser crash.
Current connection-interruption wording is checked by default;
`TB_SIMULATION_REQUIRE_CURRENT_COPY=0` permits legacy wording for diagnostics only.

For a bounded twenty-minute soak with small artifacts:

```bash
TB_SIMULATION_SCREENSHOTS=failures node desktop/tests/workspace-simulation-repeat.mjs \
  --journeys=roleplay,keyboard,multiwindow,failures --duration=1200000 \
  --fail-fast=1 --output=/tmp/ticketbunker-twenty-minute-soak
```

The duration is checked between complete batches; the final batch may extend past
the deadline. `--runs` still caps batches (100 when duration is supplied alone).
Each child retains its own timeout. Failure-only screenshots omit repeated success
images, never assertions; run one normal batch first for representative images.
The campaign records elapsed wall time, counts, failures and artifact bytes. A
fail-fast soak stops at a regression so investigation can precede another batch.

## One persistent session

```bash
TB_SIMULATION_SCREENSHOTS=failures TB_SIMULATION_OUTPUT=/tmp/ticketbunker-steady \
  npm run test:simulation:steady --prefix desktop -- --duration=1800000
```

This is one browser page and one worker process for the entire requested duration,
not repeated fixture launches. It alternates both repositories, opens a temporary
file tab, edits and saves with Ctrl+S, closes the tab, preserves unsent file-chat
drafts, changes overlays, toggles drawers and periodically refreshes. It makes no
model requests. `--cycles=10 --duration=60000` is a short pilot; the first limit
reached stops the run. Source code changes are confined to the disposable fixture.

The harness reads Monaco's real model registry and asserts that transient tabs
return to the warmed-up model/editor/tab counts. `progress.json` and `results.json`
record actual elapsed time, cycle count, browser errors, worker PID, Linux process
RSS (unavailable elsewhere), local recovery-database/observation-log sizes, and
Chromium diagnostic metrics. Memory observations
are not cross-platform performance guarantees or arbitrary leak thresholds.
Successful screenshots can be suppressed; failure screenshots are always retained.
The optional `--save-button=1` is diagnostic only and explicitly labels the result;
it does not validate the keyboard-save journey.

The keyboard journey waits for the visible Save button to be enabled and records
focus/readiness and recent command metadata before each shortcut. New runs also
capture those conditions at the keydown event itself. This distinguishes an
unavailable action from a shortcut that fails despite being ready. A prior run
that timed out without that evidence remains an unexplained failure; a later
successful soak or a deliberately blocked-save probe cannot prove its cause.
