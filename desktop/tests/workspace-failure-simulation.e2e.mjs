// Real browser/backend fault journeys. Only delivery timing/drop faults are
// intercepted; successful command responses and source saves come from Python.
import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { expect } from '@playwright/test';
import { startLab, enterProject, selectRepo, replaceEditor, editorText, capture } from './workspace-simulation-support.mjs';

const output = process.env.TB_SIMULATION_OUTPUT || join(tmpdir(), 'ticketbunker-failure-simulation');
mkdirSync(output, { recursive: true });
const selected = (process.env.TB_FAILURE_CASES || 'initial,refresh,branches,detached,worktree,worktree-dirty,transport,worker,busy').split(',');
const result = { passed: true, cases: [] };
const git = (repo, ...args) => {
  const run = spawnSync('git', ['-C', repo, ...args], { encoding: 'utf8', env: { ...process.env, TB_AGENT_DISABLE: '1', GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: join(repo, '..', 'gitconfig') } });
  expect(run.status, run.stderr).toBe(0);
  return run.stdout.trim();
};
async function openPath(workspace, path) {
  await workspace.getByLabel('Find file or open path').fill(path);
  await workspace.getByLabel('Find file or open path').press('Enter');
  await expect(workspace.getByRole('tab', { name: path.split('/').pop(), exact: true })).toBeVisible();
}
async function overlay(workspace, id) {
  await workspace.getByRole('button', { name: 'Add ticket overlay' }).click();
  await workspace.getByLabel('Find ticket overlay').fill(id);
  await workspace.locator('.overlay-picker').getByRole('button', { name: new RegExp(`^${id} `) }).click();
  await expect(workspace.getByLabel('Active ticket session')).toHaveValue(id);
}
const action = request => { try { return request.postDataJSON()?.action; } catch { return ''; } };
async function receiptReady(promise, label) {
  let timer;
  try { await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} was not reached within 10 seconds`)), 10000); })]); }
  finally { clearTimeout(timer); }
}
for (const name of selected) {
  const directory = join(output, name);
  const lab = await startLab(directory, { workerProcess: name === 'worker' });
  const page = await lab.context.newPage();
  const record = { name, passed: false, checks: [], errors: [] };
  page.on('pageerror', error => record.errors.push(error.message));
  const expectOffline = async () => {
    const message = page.getByText(/local service is not running|connection to TicketBunker.*local service.*interrupted/i).first();
    await expect(message).toBeVisible({ timeout: 15000 });
    record.offline_message = await message.innerText();
    if (process.env.TB_SIMULATION_REQUIRE_CURRENT_COPY !== '0') {
      expect(record.offline_message).toContain('connection');
      expect(record.offline_message).not.toContain('is not running');
    }
  };
  let release;
  try {
    if (name === 'initial') {
      let failed = false;
      await page.route('**/api/action', async route => {
        if (!failed && action(route.request()) === 'workspace.open') {
          failed = true;
          await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ ok: false, message: 'The simulation temporarily refused this workspace read.' }) });
        } else await route.continue();
      });
    }
    await enterProject(page, lab.url, true);
    let workspace = await selectRepo(page);
    const source = join(lab.repositories.engine, 'README.md');
    const original = readFileSync(source, 'utf8');
    if (name === 'initial') {
      await expect(workspace.getByRole('alert')).toContainText('temporarily refused');
      await workspace.getByRole('button', { name: 'Refresh workspace' }).click();
      await expect(workspace.getByLabel('Find file or open path')).toBeVisible();
      await expect(workspace.getByRole('alert')).toHaveCount(0);
      await page.unrouteAll({ behavior: 'wait' });
    }
    await openPath(workspace, 'README.md');
    if (name === 'initial') {
      expect(await editorText(page, workspace)).toBe(original);
      record.checks.push('A single failed initial workspace read can be retried through Refresh without reloading the app');
    } else if (name === 'refresh') {
      let failed = false;
      await page.route('**/api/action', async route => {
        if (!failed && action(route.request()) === 'workspace.open') {
          failed = true;
          await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ ok: false, message: 'The simulation refused one ordinary workspace refresh.' }) });
        } else await route.continue();
      });
      await workspace.getByRole('button', { name: 'Refresh workspace' }).click();
      await expect(workspace.getByRole('alert')).toContainText('ordinary workspace refresh');
      await workspace.getByRole('button', { name: 'Refresh workspace' }).click();
      await expect(workspace.getByRole('alert')).toHaveCount(0);
      expect(await editorText(page, workspace)).toBe(original);
      record.checks.push('A failed ordinary refresh clears its own stale error after a successful retry');
    } else if (name === 'detached') {
      await workspace.getByRole('button', { name: 'Close README.md' }).click();
      const firstCommit = git(lab.repositories.engine, 'rev-parse', 'HEAD');
      git(lab.repositories.engine, 'commit', '--allow-empty', '-m', 'Another commit with identical source bytes');
      const secondCommit = git(lab.repositories.engine, 'rev-parse', 'HEAD');
      git(lab.repositories.engine, 'switch', '--detach', firstCommit);
      await workspace.getByRole('button', { name: 'Refresh workspace' }).click();
      await expect(workspace.locator('.repository-workspace-header')).toContainText('Detached HEAD');
      await openPath(workspace, 'README.md');
      const draft = `${original}\nThis draft was opened at the first detached commit.\n`;
      await replaceEditor(page, workspace, draft);
      git(lab.repositories.engine, 'switch', '--detach', secondCommit);
      const saveResponse = page.waitForResponse(response => action(response.request()) === 'workspace.file.save');
      await workspace.getByRole('button', { name: 'Save', exact: true }).click();
      record.save_receipt = await (await saveResponse).json();
      record.detached_commits = [firstCommit, secondCommit];
      record.source_after_save = readFileSync(source, 'utf8');
      await expect(workspace.getByRole('alert')).toContainText(/checkout changed|commit changed/i);
      expect(readFileSync(source, 'utf8')).toBe(original);
      expect(await editorText(page, workspace)).toBe(draft);
      git(lab.repositories.engine, 'switch', '--detach', firstCommit);
      await workspace.getByRole('button', { name: 'Refresh workspace' }).click();
      const accepted = page.waitForResponse(response => action(response.request()) === 'workspace.file.save');
      await workspace.getByRole('button', { name: 'Save', exact: true }).click();
      expect((await (await accepted).json()).ok).toBe(true);
      expect(readFileSync(source, 'utf8')).toBe(draft);
      record.checks.push('Moving between detached commits refuses the stale save; returning to its exact original commit permits the save');
    } else if (name === 'branches') {
      const draft = `${original}\nDraft belongs to the original branch.\n`;
      await replaceEditor(page, workspace, draft);
      const saveAtCheckout = async () => {
        const receipt = page.waitForResponse(response => action(response.request()) === 'workspace.file.save');
        await workspace.getByRole('button', { name: 'Save', exact: true }).click();
        return (await receipt).json();
      };
      git(lab.repositories.engine, 'switch', 'main');
      expect((await saveAtCheckout()).ok).toBe(false);
      await expect(workspace.getByRole('alert')).toContainText(/checkout changed/i);
      expect(readFileSync(source, 'utf8')).toBe(original);
      await workspace.getByRole('button', { name: 'Refresh workspace' }).click();
      await expect(workspace.locator('.repository-workspace-header')).toContainText('main');
      expect(await editorText(page, workspace)).toBe(draft);
      git(lab.repositories.engine, 'switch', '--detach', 'HEAD');
      await workspace.getByRole('button', { name: 'Refresh workspace' }).click();
      await expect(workspace.locator('.repository-workspace-header')).toContainText('Detached HEAD');
      expect((await saveAtCheckout()).ok).toBe(false);
      expect(readFileSync(source, 'utf8')).toBe(original);
      git(lab.repositories.engine, 'switch', 'qa/LAB-1-grid');
      await workspace.getByRole('button', { name: 'Refresh workspace' }).click();
      await expect(workspace.locator('.repository-workspace-header')).toContainText('qa/LAB-1-grid');
      git(lab.repositories.engine, 'commit', '--allow-empty', '-m', 'Unrelated commit on the same named branch');
      expect((await saveAtCheckout()).ok).toBe(true);
      await expect.poll(() => readFileSync(source, 'utf8')).toBe(draft);
      await overlay(workspace, 'LAB-1');
      await overlay(workspace, 'LAB-3');
      await workspace.getByRole('button', { name: 'Hide LAB-3 overlay' }).click();
      await expect(workspace.getByLabel('Active ticket session')).toHaveValue('LAB-1');
      await workspace.getByRole('button', { name: 'LAB-1', exact: true }).click();
      await page.getByRole('navigation', { name: 'Ticket sections' }).getByRole('button', { name: 'Workspace', exact: true }).click();
      await expect(workspace.getByRole('tab', { name: 'README.md', exact: true })).toBeVisible();
      record.checks.push('Branch switch and detached HEAD refuse stale save without touching bytes; original branch saves; overlay removal/navigation retain workspace');
    } else if (name === 'worktree' || name === 'worktree-dirty') {
      const worktree = join(lab.root, 'engine-linked');
      git(lab.repositories.engine, 'worktree', 'add', '-b', 'qa/LAB-3-linked', worktree, 'main');
      const oldDraft = `${original}\nThis draft must be saved to the old checkout before replacing its attachment.\n`;
      if (name === 'worktree-dirty') await replaceEditor(page, workspace, oldDraft);
      // The external Git operation is fixture setup; folder attachment/editing use UI.
      await page.locator('.project-repository-nav').getByRole('button', { name: 'Tickets', exact: true }).click();
      await page.getByRole('button', { name: 'Configure', exact: true }).click();
      await page.getByRole('button', { name: 'Repositories', exact: true }).click();
      await page.getByRole('row').filter({ hasText: 'Garden engine' }).getByRole('button', { name: 'Change folder' }).click();
      await page.getByLabel('Local repository path').fill(worktree);
      await page.getByRole('button', { name: 'Connect repository', exact: true }).click();
      if (name === 'worktree-dirty') {
        const unsaved = page.getByRole('dialog', { name: 'You have unsaved changes' });
        await expect(unsaved).toBeVisible();
        await unsaved.getByRole('button', { name: 'Cancel', exact: true }).click();
        await expect(page.getByRole('row').filter({ hasText: 'Garden engine' })).toContainText(lab.repositories.engine);
        expect(readFileSync(source, 'utf8')).toBe(original);
        await page.getByRole('button', { name: 'Connect repository', exact: true }).click();
        await unsaved.getByRole('button', { name: 'Save', exact: true }).click();
        await expect.poll(() => readFileSync(source, 'utf8')).toBe(oldDraft);
      }
      await expect(page.getByLabel('Local repository path')).toHaveCount(0);
      await page.getByRole('button', { name: 'Close configuration' }).click();
      workspace = await selectRepo(page);
      await expect(workspace.locator('.repository-workspace-header')).toContainText('qa/LAB-3-linked');
      await openPath(workspace, 'README.md');
      const draft = `${original}\nOnly the linked worktree changes.\n`;
      await replaceEditor(page, workspace, draft);
      await workspace.getByRole('button', { name: 'Save', exact: true }).click();
      await expect.poll(() => readFileSync(join(worktree, 'README.md'), 'utf8')).toBe(draft);
      expect(readFileSync(source, 'utf8')).toBe(name === 'worktree-dirty' ? oldDraft : original);
      record.checks.push(name === 'worktree-dirty' ? 'Dirty attachment replacement supports cancel, then saves the old draft to its old checkout before connecting the new worktree' : 'Linked worktree connects through real Project settings and edits only its own checkout');
    } else if (name === 'transport') {
      let entered;
      const requested = new Promise(resolve => { entered = resolve; });
      const gate = new Promise(resolve => { release = resolve; });
      await page.route('**/api/action', async route => {
        const request = route.request();
        if (action(request) === 'workspace.file.read' && request.postDataJSON().payload.path === 'docs/design.md') { entered(); await gate; }
        await route.continue();
      });
      await workspace.getByLabel('Find file or open path').fill('docs/design.md');
      await workspace.getByLabel('Find file or open path').press('Enter');
      await requested;
      await openPath(workspace, 'src/grid.py');
      release();
      await expect(workspace.getByRole('tab', { name: 'design.md', exact: true })).toBeVisible();
      await expect(workspace.getByRole('tab', { name: 'grid.py', exact: true })).toHaveAttribute('aria-selected', 'true');
      await page.unrouteAll({ behavior: 'wait' });
      await workspace.getByRole('tab', { name: 'README.md', exact: true }).click();
      const draft = `${original}\nThe save succeeds but its reply is lost.\n`;
      await replaceEditor(page, workspace, draft);
      let dropped = false;
      await page.route('**/api/action', async route => {
        if (!dropped && action(route.request()) === 'workspace.file.save') {
          dropped = true;
          const response = await route.fetch();
          expect(response.ok()).toBe(true);
          await route.abort('failed');
        } else await route.continue();
      });
      await workspace.getByRole('button', { name: 'Save', exact: true }).click();
      await expect.poll(() => readFileSync(source, 'utf8')).toBe(draft);
      await expectOffline();
      await page.unrouteAll({ behavior: 'wait' });
      page.once('dialog', dialog => dialog.accept());
      await page.reload();
      await page.locator('.nav-project').filter({ hasText: 'Grid Garden' }).click();
      workspace = await selectRepo(page);
      await expect(workspace.getByRole('textbox', { name: /Repository file editor/ })).toBeVisible();
      expect(await editorText(page, workspace)).toBe(draft);
      const retry = workspace.getByRole('button', { name: 'Save', exact: true });
      if (await retry.isEnabled()) await retry.click();
      await expect(workspace.getByRole('tab', { name: 'README.md', exact: true })).toBeVisible();
      expect(readFileSync(source, 'utf8')).toBe(draft);
      record.checks.push('Delayed old file read does not steal active tab; lost successful save reply is safely retried after reload');
    } else if (name === 'busy') {
      let recoveryReady;
      const recoveryHeld = new Promise(resolve => { recoveryReady = resolve; });
      const gate = new Promise(resolve => { release = resolve; });
      let sourceCompleted = false;
      let held = false;
      let holdSource = false;
      let writeReady;
      let releaseWrite;
      const writeHeld = new Promise(resolve => { writeReady = resolve; });
      const writeGate = new Promise(resolve => { releaseWrite = resolve; });
      let writes = 0;
      const saves = [];
      await page.route('**/api/action', async route => {
        const request = route.request();
        if (action(request) === 'workspace.file.save') {
          saves.push(request.postDataJSON().payload);
          const response = await route.fetch();
          sourceCompleted = true;
          if (holdSource) {
            writes++;
            if (writes === 1) { writeReady(); await writeGate; }
          }
          await route.fulfill({ response });
        } else if (sourceCompleted && !held && action(request) === 'workspace.save') {
          held = true;
          const response = await route.fetch();
          expect(response.ok()).toBe(true);
          recoveryReady();
          await gate;
          await route.fulfill({ response });
        } else await route.continue();
      });
      const firstDraft = `${original}\nFirst saved revision before recovery reply.\n`;
      const nextDraft = `${original}\nSecond edit while workspace recovery is pending.\n`;
      await replaceEditor(page, workspace, firstDraft);
      const saveButton = workspace.getByRole('button', { name: 'Save', exact: true });
      await saveButton.click();
      await receiptReady(recoveryHeld, 'Held recovery receipt');
      expect(readFileSync(source, 'utf8')).toBe(firstDraft);
      await replaceEditor(page, workspace, nextDraft);
      await expect(workspace.getByRole('tab', { name: 'README.md •', exact: true })).toBeVisible();
      await expect(saveButton).toBeEnabled();
      await page.evaluate(() => {
        document.addEventListener('keydown', event => {
          if (!event.ctrlKey || event.key.toLowerCase() !== 's') return;
          const region = event.target.closest('.repository-workspace');
          const button = [...region.querySelectorAll('button')].find(item => item.textContent === 'Save');
          globalThis.__busySaveKey = { disabled: button.disabled, editor_focused: !!event.target.closest('.monaco-editor') };
        }, true);
      });
      const receipt = page.waitForResponse(response => action(response.request()) === 'workspace.file.save');
      await page.keyboard.press('Control+s');
      expect((await (await receipt).json()).ok).toBe(true);
      record.save_keydown = await page.evaluate(() => globalThis.__busySaveKey);
      expect(record.save_keydown).toEqual({ disabled: false, editor_focused: true });
      expect(saves).toHaveLength(2);
      expect(saves[1].expected_root).toBe(lab.repositories.engine);
      expect(saves[1].content).toBe(nextDraft);
      expect(readFileSync(source, 'utf8')).toBe(nextDraft);
      release();
      record.checks.push('While the actual recovery reply is held, the enabled Ctrl+S saves the next explicit edit to its original repository using the confirmed source digest');
      // Keep interception installed throughout: re-registering it during queued
      // recovery traffic can miss the intended fault, invalidating the test.
      holdSource = true;
      release = releaseWrite;
      const pendingDraft = `${original}\nSave waiting for its source receipt.\n`;
      const newerDraft = `${original}\nNewer text must not be implicitly queued.\n`;
      await replaceEditor(page, workspace, pendingDraft);
      await expect(saveButton).toBeEnabled();
      await page.keyboard.press('Control+s');
      await receiptReady(writeHeld, 'Held source receipt');
      await replaceEditor(page, workspace, newerDraft);
      await page.keyboard.press('Control+s');
      await expect(workspace.getByRole('alert')).toContainText('press Save again');
      expect(writes).toBe(1);
      expect(readFileSync(source, 'utf8')).toBe(pendingDraft);
      release();
      await expect(saveButton).toBeEnabled();
      await page.waitForTimeout(200); // No implicit follow-up write after receipt.
      expect(writes).toBe(1);
      expect(await editorText(page, workspace)).toBe(newerDraft);
      await page.keyboard.press('Control+s');
      await expect.poll(() => readFileSync(source, 'utf8')).toBe(newerDraft);
      expect(writes).toBe(2);
      await page.unrouteAll({ behavior: 'wait' });
      record.checks.push('Ctrl+S during an actual pending source receipt explains the wait, preserves newer text, and requires another explicit save without queuing a write');
    } else if (name === 'worker') {
      const draft = `${original}\nUnsaved while the worker restarts.\n`;
      await replaceEditor(page, workspace, draft);
      await lab.control('worker.stop');
      // Autosave/heartbeat detects the stopped process; the offline banner itself
      // can cover Refresh, so do not force-click through that real product state.
      await expectOffline();
      expect(readFileSync(source, 'utf8')).toBe(original);
      const restart = await lab.control('worker.restart');
      expect(restart.pid).not.toBe(lab.worker_pid);
      page.once('dialog', dialog => dialog.accept());
      await page.reload();
      await page.locator('.nav-project').filter({ hasText: 'Grid Garden' }).click();
      workspace = await selectRepo(page);
      await expect(workspace.getByRole('textbox', { name: /Repository file editor/ })).toBeVisible();
      expect(await editorText(page, workspace)).toBe(draft);
      await workspace.getByRole('button', { name: 'Save', exact: true }).click();
      await expect.poll(() => readFileSync(source, 'utf8')).toBe(draft);
      record.checks.push('Actual worker process stops/restarts on same port; browser reload reacquires token and keeps unsaved draft');
    } else throw new Error(`Unknown failure case: ${name}`);
    expect(record.errors).toEqual([]);
    record.passed = true;
    await capture(page, join(directory, 'finished.png'));
  } catch (error) {
    record.failure = String(error);
    record.failure_stack = error.stack;
    result.passed = false;
    await page.screenshot({ path: join(directory, 'failure.png') }).catch(() => {});
  } finally {
    release?.();
    writeFileSync(join(directory, 'results.json'), JSON.stringify(record, null, 2));
    result.cases.push(record);
    await lab.stop();
  }
}
writeFileSync(join(output, 'results.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
process.exitCode = result.passed ? 0 : 1;
