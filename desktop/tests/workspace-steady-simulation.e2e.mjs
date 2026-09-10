// One persistent app/worker session, unlike the fresh-fixture repeat campaign.
import { readFileSync, writeFileSync, statSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { expect } from '@playwright/test';
import { startLab, enterProject, selectRepo, replaceEditor, editorText, capture } from './workspace-simulation-support.mjs';

const options = Object.fromEntries(process.argv.slice(2).map(value => value.replace(/^--/, '').split('=')));
const duration = Number(options.duration || 60000);
const maxCycles = Number(options.cycles || 10000);
if (!Number.isFinite(duration) || duration < 1000 || duration > 3600000 || !Number.isInteger(maxCycles) || maxCycles < 1 || maxCycles > 10000) throw new Error('Use --duration=1800000 (milliseconds, max one hour) --cycles=10000');
const output = process.env.TB_SIMULATION_OUTPUT || join(tmpdir(), 'ticketbunker-steady-simulation');
const lab = await startLab(output, { workerProcess: true });
// Monaco documents globalAPI in monaco.d.ts. Expose the real registry for
// read-only model counts; do not replace editor factories or product methods.
await lab.context.addInitScript(() => {
  globalThis.MonacoEnvironment = { globalAPI: true };
  document.addEventListener('keydown', event => {
    if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return;
    const workspace = event.target.closest?.('.repository-workspace');
    const button = workspace && [...workspace.querySelectorAll('button')].find(item => item.textContent === 'Save');
    globalThis.__simulationSaveKey = { at: new Date().toISOString(), workspace: workspace?.getAttribute('aria-label'), disabled: button?.disabled, editor_focused: !!event.target.closest?.('.monaco-editor') };
  }, true);
});
const page = await lab.context.newPage();
const result = { passed: false, requested_duration_ms: duration, save_method: options['save-button'] === '1' ? 'button (diagnostic)' : 'keyboard', started_at: new Date().toISOString(), worker_pid: lab.worker_pid, cycles: 0, errors: [], samples: [], recent_commands: [] };
page.on('pageerror', error => result.errors.push(error.message));
page.on('request', request => {
  try {
    const data = request.postDataJSON();
    if (!data?.action) return;
    result.recent_commands.push({ at: new Date().toISOString(), action: data.action, path: data.payload?.path, expected_root: data.payload?.expected_root });
    result.recent_commands = result.recent_commands.slice(-40);
  } catch {}
});
let performance;
let browserInfo;
const paths = { 'Garden engine': lab.repositories.engine, 'Garden renderer': lab.repositories.renderer };
const tickets = { 'Garden engine': 'LAB-1', 'Garden renderer': 'LAB-2' };
const originals = Object.fromEntries(Object.entries(paths).map(([name, path]) => [name, readFileSync(join(path, 'README.md'), 'utf8')]));
const rss = pid => {
  if (process.platform !== 'linux') return null;
  try { return Number(/^VmRSS:\s+(\d+)/m.exec(readFileSync(`/proc/${pid}/status`, 'utf8'))?.[1] || 0) * 1024; } catch { return null; }
};
const bytes = path => readdirSync(path, { withFileTypes: true }).reduce((total, entry) => total + (entry.isDirectory() ? bytes(join(path, entry.name)) : entry.isFile() ? statSync(join(path, entry.name)).size : 0), 0);
const storage = () => Object.fromEntries(['workspace-git.jsonl', 'workspaces.sqlite3', 'workspaces.sqlite3-wal', 'workspaces.sqlite3-shm'].map(name => {
  try { return [name, statSync(join(lab.root, 'data', name)).size]; } catch { return [name, null]; }
}));
async function openPath(workspace, path) {
  await workspace.getByLabel('Find file or open path').fill(path);
  await workspace.getByLabel('Find file or open path').press('Enter');
  await expect(workspace.getByRole('tab', { name: path.split('/').pop(), exact: true })).toBeVisible();
  await expect(workspace.getByRole('tab', { name: path.split('/').pop(), exact: true })).toHaveAttribute('aria-selected', 'true');
}
async function overlay(workspace, id) {
  await workspace.getByRole('button', { name: 'Add ticket overlay' }).click();
  await workspace.getByLabel('Find ticket overlay').fill(id);
  await workspace.locator('.overlay-picker').getByRole('button', { name: new RegExp(`^${id} `) }).click();
  await expect(workspace.getByLabel('Active ticket session')).toHaveValue(id);
}
const modelCount = () => page.evaluate(() => globalThis.monaco?.editor.getModels().length ?? null);
async function sample() {
  const observed = await page.evaluate(() => ({ models: globalThis.monaco?.editor.getModels().length ?? null, editors: document.querySelectorAll('.monaco-editor').length, elements: document.querySelectorAll('*').length, file_tabs: document.querySelectorAll('.workspace-file-tabs [role="tab"]').length }));
  let metrics = {};
  let processes = [];
  try { metrics = Object.fromEntries((await performance.send('Performance.getMetrics')).metrics.filter(row => ['JSHeapUsedSize', 'JSHeapTotalSize', 'Nodes', 'Documents', 'JSEventListeners', 'LayoutCount', 'RecalcStyleCount'].includes(row.name)).map(row => [row.name, row.value])); } catch {}
  try { processes = (await browserInfo.send('SystemInfo.getProcessInfo')).processInfo.map(row => ({ type: row.type, pid: row.id, cpu_seconds: row.cpuTime, rss_bytes: rss(row.id) })); } catch {}
  const row = { at: new Date().toISOString(), cycle: result.cycles, ...observed, metrics, storage_bytes: storage(), worker_rss_bytes: rss(lab.worker_pid), browser_processes: processes };
  result.samples.push(row);
  writeFileSync(join(output, 'progress.json'), JSON.stringify(result, null, 2));
  return row;
}
try {
  performance = await lab.context.newCDPSession(page);
  await performance.send('Performance.enable');
  browserInfo = await lab.browser.newBrowserCDPSession();
  await enterProject(page, lab.url, true);
  for (const name of Object.keys(paths)) {
    const workspace = await selectRepo(page, name);
    await openPath(workspace, 'src/grid.py');
    expect(await editorText(page, workspace)).toBe(readFileSync(join(paths[name], 'src/grid.py'), 'utf8'));
    await openPath(workspace, 'docs/design.md');
    expect(await editorText(page, workspace)).toBe(readFileSync(join(paths[name], 'docs/design.md'), 'utf8'));
    await overlay(workspace, tickets[name]);
    await workspace.getByRole('button', { name: 'Session', exact: true }).click();
    await expect(workspace.getByRole('textbox', { name: 'Message to ticket coworker' })).toBeVisible();
  }
  await expect.poll(modelCount).not.toBeNull();
  result.baseline = await sample();
  expect(result.baseline.editors).toBe(2);
  expect(result.baseline.file_tabs).toBe(4);
  const loopStart = Date.now();
  result.loop_started_at = new Date(loopStart).toISOString();
  while (Date.now() - loopStart < duration && result.cycles < maxCycles) {
    const cycle = result.cycles + 1;
    const name = cycle % 2 ? 'Garden engine' : 'Garden renderer';
    let workspace = await selectRepo(page, name);
    await openPath(workspace, 'README.md');
    const draft = `${originals[name]}\nPersistent-session cycle ${cycle} for ${name}.\n`;
    await replaceEditor(page, workspace, draft);
    await expect(workspace.getByRole('tab', { name: 'README.md •', exact: true })).toBeVisible();
    if (cycle % 3 === 0) {
      await selectRepo(page, name === 'Garden engine' ? 'Garden renderer' : 'Garden engine');
      workspace = await selectRepo(page, name);
      expect(await editorText(page, workspace)).toBe(draft);
    }
    // The UI intentionally disables saving while an earlier operation settles.
    // Await its visible readiness contract before sending the shortcut.
    const saveButton = workspace.getByRole('button', { name: 'Save', exact: true });
    result.save_readiness = { cycle, initially_disabled: await saveButton.isDisabled() };
    if (result.save_readiness.initially_disabled) {
      result.deferred_save_readiness_count = (result.deferred_save_readiness_count || 0) + 1;
      result.last_deferred_save_cycle = cycle;
    }
    await expect(saveButton).toBeEnabled();
    result.save_readiness.editor_focused = await page.evaluate(() => !!document.activeElement?.closest('.monaco-editor'));
    expect(result.save_readiness.editor_focused).toBe(true);
    const saved = page.waitForResponse(response => { try { return response.request().postDataJSON()?.action === 'workspace.file.save'; } catch { return false; } });
    if (options['save-button'] === '1') await workspace.getByRole('button', { name: 'Save', exact: true }).click();
    else await page.keyboard.press('Control+s');
    result.save_keydown = await page.evaluate(() => globalThis.__simulationSaveKey ?? null);
    const receipt = await saved;
    result.last_save = { request: receipt.request().postDataJSON(), response: await receipt.json() };
    expect(result.last_save.request.payload.expected_root).toBe(paths[name]);
    expect(result.last_save.request.payload.path).toBe('README.md');
    expect(result.last_save.request.payload.content).toBe(draft);
    expect(result.last_save.response.ok).toBe(true);
    expect(readFileSync(join(paths[name], 'README.md'), 'utf8')).toBe(draft);
    await expect(workspace.getByRole('tab', { name: 'README.md', exact: true })).toBeVisible();
    await workspace.getByRole('button', { name: 'Close README.md' }).click();
    await expect.poll(modelCount).toBe(result.baseline.models);
    await expect(workspace.getByRole('tab')).toHaveCount(2);
    await workspace.getByRole('button', { name: 'Session', exact: true }).click();
    const message = workspace.getByRole('textbox', { name: 'Message to ticket coworker' });
    await message.fill(`Unsent ${name} file question for cycle ${cycle}`);
    await workspace.getByRole('button', { name: 'Comments', exact: true }).click();
    await workspace.getByRole('button', { name: 'Session', exact: true }).click();
    await expect(message).toHaveValue(`Unsent ${name} file question for cycle ${cycle}`);
    if (cycle % 4 === 0) {
      await workspace.getByRole('button', { name: 'Hide explorer' }).click();
      await workspace.getByRole('button', { name: 'Show explorer' }).click();
      await workspace.getByRole('button', { name: 'Hide ticket context' }).click();
      await workspace.getByRole('button', { name: 'Show ticket context' }).click();
    }
    if (cycle % 5 === 0) {
      await overlay(workspace, 'LAB-3');
      await workspace.getByLabel('Active ticket session').selectOption(tickets[name]);
      await workspace.getByRole('button', { name: 'Hide LAB-3 overlay' }).click();
      await expect(message).toHaveValue(`Unsent ${name} file question for cycle ${cycle}`);
    }
    if (cycle % 7 === 0) await workspace.getByRole('button', { name: 'Refresh workspace' }).click();
    await expect(workspace.getByRole('alert')).toHaveCount(0);
    expect(result.errors).toEqual([]);
    result.cycles = cycle;
    if (cycle % 10 === 0) {
      const current = await sample();
      if (process.platform === 'linux') expect(current.worker_rss_bytes).toBeGreaterThan(0);
      expect(current.models).toBe(result.baseline.models);
      expect(current.editors).toBe(result.baseline.editors);
      expect(current.file_tabs).toBe(result.baseline.file_tabs);
      console.log(`PASS cycle ${cycle}; ${Date.now() - loopStart}ms; models ${current.models}; editor DOM ${current.editors}`);
    }
  }
  result.loop_elapsed_ms = Date.now() - loopStart;
  await sample();
  await capture(page, join(output, 'steady-finished.png'));
  result.passed = true;
} catch (error) {
  result.failure = String(error);
  result.failure_stack = error.stack;
  result.failure_focus = await page.evaluate(() => ({ tag: document.activeElement?.tagName, label: document.activeElement?.getAttribute('aria-label'), className: document.activeElement?.className })).catch(() => null);
  await page.screenshot({ path: join(output, 'failure.png') }).catch(() => {});
} finally {
  if (result.loop_started_at) result.loop_elapsed_ms = Date.now() - Date.parse(result.loop_started_at);
  result.finished_at = new Date().toISOString();
  result.artifact_bytes = bytes(output);
  writeFileSync(join(output, 'results.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ passed: result.passed, cycles: result.cycles, loop_elapsed_ms: result.loop_elapsed_ms, failure: result.failure, output }, null, 2));
  await lab.stop();
}
process.exitCode = result.passed ? 0 : 1;
