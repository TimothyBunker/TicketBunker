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
