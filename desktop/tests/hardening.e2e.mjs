// A real local worker, SQLite database and disposable Git checkout. DOM-only
// product interactions; no real tracker, model account or user repository.
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { basename, delimiter, join } from 'node:path';
import { tmpdir } from 'node:os';
import { chromium, expect } from '@playwright/test';
const root = fileURLToPath(new URL('../../', import.meta.url));
const viewportMatch = /^(\d+)x(\d+)$/.exec(process.env.TB_HARDENING_VIEWPORT || '1440x940');
if (!viewportMatch || Number(viewportMatch[1]) < 320 || Number(viewportMatch[2]) < 240) throw new Error('TB_HARDENING_VIEWPORT must be WIDTHxHEIGHT (at least 320x240).');
const viewport = { width: Number(viewportMatch[1]), height: Number(viewportMatch[2]) };
const output = process.env.TB_HARDENING_OUTPUT || mkdtempSync(join(tmpdir(), 'tb-hardening-'));
mkdirSync(output, { recursive: true });
const evidence = { passed: false, started_at: new Date().toISOString(), output, viewport, requests: [], request_failures: [], fixture_stderr: '', fixture_events: [] };
const lifecycleDeadlineMs = Number(process.env.TB_HARDENING_DEADLINE_MS || 10000);
if (!Number.isInteger(lifecycleDeadlineMs) || lifecycleDeadlineMs < 100 || lifecycleDeadlineMs > 60000) throw new Error('TB_HARDENING_DEADLINE_MS must be an integer between 100 and 60000.');
const bounded = async (promise, label, milliseconds = lifecycleDeadlineMs) => {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} exceeded ${milliseconds}ms`)), milliseconds); })]);
  } finally { clearTimeout(timer); }
};
let phase = 'setup';
const fixture = spawn(process.env.PYTHON || 'python', ['tests/ui_hardening_fixture.py'], {
  cwd: root, env: { ...process.env, PYTHONPATH: `${root}${delimiter}${root}/tests`, TB_AGENT_DISABLE: '1' }, stdio: ['pipe', 'pipe', 'pipe'],
});
fixture.stderr.on('data', chunk => { evidence.fixture_stderr = (evidence.fixture_stderr + chunk.toString()).slice(-64000); process.stderr.write(chunk); });
fixture.stdin.on('error', error => evidence.fixture_events.push({ phase, at: new Date().toISOString(), error: error.message }));
let fixtureClosed = false;
const fixtureExited = new Promise(resolve => fixture.once('close', (code, signal) => { fixtureClosed = true; evidence.fixture_events.push({ phase, at: new Date().toISOString(), code, signal }); resolve(); }));
const lines = createInterface({ input: fixture.stdout });
const fixtureReady = new Promise((resolve, reject) => {
  lines.once('line', (line) => { try { resolve(JSON.parse(line)); } catch (error) { reject(error); } });
  fixture.once('exit', (code) => reject(new Error(`Fixture exited ${code}`)));
  fixture.once('error', error => { evidence.fixture_events.push({ phase, at: new Date().toISOString(), error: error.message }); reject(error); });
});
let browser, page;
const responseReads = [];
const errors = [];
evidence.fixture_pid = fixture.pid;
try {
const lab = await bounded(fixtureReady, 'Fixture startup');
browser = await chromium.launch({ executablePath: process.env.TB_E2E_CHROME || (existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : chromium.executablePath()), headless: true, timeout: lifecycleDeadlineMs });
page = await bounded(browser.newPage({ viewport }), 'Browser page startup');
page.setDefaultTimeout(15000);
evidence.lab = lab;
const requestIds = new WeakMap();
let requestNumber = 0;
const requestInfo = request => {
  let input = {};
  try { input = request.postDataJSON() || {}; } catch {}
  return { id: requestIds.get(request), method: request.method(), url: request.url(), action: input.action,
    project_id: input.payload?.project_id, ticket_id: input.payload?.ticket_id };
};
page.on('request', request => {
  requestIds.set(request, ++requestNumber);
  evidence.requests.push({ event: 'request', at: new Date().toISOString(), phase, ...requestInfo(request) });
});
page.on('requestfailed', request => {
  const failure = { event: 'requestfailed', at: new Date().toISOString(), phase, ...requestInfo(request), failure: request.failure(), timing: request.timing() };
  evidence.requests.push(failure); evidence.request_failures.push(failure);
});
page.on('response', response => {
  const entry = { event: 'response', at: new Date().toISOString(), phase, ...requestInfo(response.request()), status: response.status() };
  evidence.requests.push(entry);
  if (response.url().includes('/api/')) responseReads.push(response.json().then(value => {
    entry.body = { ok: value.ok, code: value.code, message: value.message, tickets: value.tickets?.map(ticket => ({ id: ticket.id, project_id: ticket.project_id })), bunker_count: value.bunkers?.length };
  }).catch(error => { entry.body_error = error.message; }));
});
// Existing stage captures remain available, and each run also keeps a unique copy.
const screenshot = page.screenshot.bind(page);
page.screenshot = async options => {
  const result = await screenshot(options);
  if (options?.path) writeFileSync(join(output, basename(options.path)), result);
  return result;
};
let boardLoads = 0;
page.on('request', (request) => { try { if (request.postDataJSON()?.action === 'application.tracker.boards') boardLoads++; } catch {} });
page.on('pageerror', (error) => errors.push(error.message));
  phase = 'flow';
  await page.goto(lab.url);
  const without = page.getByRole('button', { name: 'Use without agent', exact: true });
  await without.click();
  // An undecided agent can appear in settings over the Bunker page.
  const closeSettings = page.getByRole('button', { name: 'Close settings' });
  if (await closeSettings.isVisible()) await closeSettings.click();
  await page.getByRole('button', { name: 'All Bunkers', exact: true }).click();
  await page.locator('.bunker-overview > button').first().click();
  // Bunker owners toggle availability; Projects only attach local folders.
  await page.getByLabel('Bunker settings').click();
  await page.getByRole('button', { name: 'Repositories', exact: true }).click();
  await expect(page.getByRole('table', { name: 'Allowed repositories' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Connect folder' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Add repository', exact: true }).click();
  await page.getByLabel('Repository 2 name', { exact: true }).fill('Navigation');
  await page.getByLabel('Repository 2 allowed remote', { exact: true }).fill('github.com/example/navigation');
  await page.getByRole('button', { name: 'Save repositories', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save repositories', exact: true })).toBeEnabled();
  await page.screenshot({ path: '/tmp/ticketbunker-bunker-repositories.png' });
  await page.getByRole('button', { name: 'Close configuration', exact: true }).click();
  // Context actions operate on a disposable, empty Project through the real backend.
  await page.getByRole('button', { name: 'Add project', exact: true }).click();
  await page.getByLabel('Project key', { exact: true }).fill('SCR');
  await page.getByLabel('Project name', { exact: true }).fill('Scratch');
  await page.getByRole('complementary', { name: 'Add project' }).getByRole('button', { name: 'Add project', exact: true }).click();
  await page.locator('.project-row').filter({ hasText: 'Scratch' }).click({ button: 'right' });
  await expect(page.getByRole('menuitem')).toHaveText(['Edit', 'Rename', 'Delete']);
  await page.screenshot({ path: '/tmp/ticketbunker-project-context-menu.png' });
  await page.getByRole('menuitem', { name: 'Rename' }).click();
  const renameDialog = page.getByRole('dialog', { name: 'Rename Project' });
  await renameDialog.getByLabel('Project name').fill('Renamed scratch');
  await renameDialog.getByRole('button', { name: 'Rename', exact: true }).click();
  await expect(page.locator('.project-row').filter({ hasText: 'Renamed scratch' })).toBeVisible();
  await page.locator('.project-row').filter({ hasText: 'Renamed scratch' }).click({ button: 'right' });
  await page.getByRole('menuitem', { name: 'Delete' }).click();
  const deleteDialog = page.getByRole('dialog', { name: 'Delete Project' });
  await deleteDialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.locator('.project-row').filter({ hasText: 'Renamed scratch' })).toBeVisible();
  await page.locator('.project-row').filter({ hasText: 'Renamed scratch' }).focus();
  await page.keyboard.press('Shift+F10');
  await page.getByRole('menuitem', { name: 'Delete' }).click();
  await deleteDialog.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.locator('.project-row').filter({ hasText: 'Renamed scratch' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Add project', exact: true }).click();
  const projectDrawer = page.getByRole('complementary', { name: 'Add project', exact: true });
  await projectDrawer.getByLabel('Project key', { exact: true }).fill('TOOLS');
  await projectDrawer.getByLabel('Project name', { exact: true }).fill('Navigation tools');
  await expect(projectDrawer.getByRole('checkbox')).toHaveCount(0);
  await projectDrawer.getByRole('button', { name: 'Add project', exact: true }).click();
  await page.getByLabel('Bunker settings').click();
  await page.getByRole('button', { name: 'Repositories', exact: true }).click();
  await page.getByLabel('Projects for Navigation', { exact: true }).click();
  const ecsAvailability = page.getByRole('checkbox', { name: 'ECS · Entity Component System availability for Navigation', exact: true });
  const toolsAvailability = page.getByRole('checkbox', { name: 'TOOLS · Navigation tools availability for Navigation', exact: true });
  await ecsAvailability.click();
  await expect(ecsAvailability).toBeChecked();
  await toolsAvailability.click();
  await expect(toolsAvailability).toBeChecked();
  await expect(ecsAvailability).toBeChecked();
  await ecsAvailability.click();
  await expect(ecsAvailability).not.toBeChecked();
  await expect(toolsAvailability).toBeChecked();
  await page.getByLabel('Projects for Coal', { exact: true }).click();
  const coalTools = page.getByRole('checkbox', { name: 'TOOLS · Navigation tools availability for Coal', exact: true });
  await coalTools.click();
  await expect(coalTools).toBeChecked();
  await expect(coalTools).toHaveCSS('appearance', 'auto');
  await page.screenshot({ path: '/tmp/ticketbunker-repository-availability.png' });
  await page.getByRole('button', { name: 'Close configuration', exact: true }).click();
  await page.locator('.project-row').filter({ hasText: 'Navigation tools' }).click();
  await page.getByRole('button', { name: 'Configure', exact: true }).click();
  await page.getByRole('button', { name: 'Repositories', exact: true }).click();
  await expect(page.getByRole('table', { name: 'Local repositories' })).toContainText('github.com/example/navigation');
  await expect(page.getByRole('button', { name: 'Save repositories' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Add local repository', exact: true }).click();
  await page.getByLabel('Local repository path').fill(lab.repo);
  await page.getByRole('button', { name: 'Connect repository', exact: true }).click();
  await expect(page.locator('.repository-row').filter({ hasText: 'github.com/example/coal' })).toContainText(lab.repo);
  await expect(page.locator('.repository-row').filter({ hasText: 'github.com/example/navigation' })).toContainText('Not connected');
  await page.screenshot({ path: '/tmp/ticketbunker-project-repository-selection.png' });
  await page.getByRole('button', { name: 'Boards', exact: true }).click();
  await expect(page.getByRole('list', { name: 'Boards', exact: true })).toBeVisible();
  const loadedBoards = boardLoads;
  await page.getByRole('button', { name: 'Repositories', exact: true }).click();
  await page.getByRole('button', { name: 'Boards', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Refresh boards', exact: true })).toBeEnabled();
  if (boardLoads !== loadedBoards) throw new Error('Switching tabs unnecessarily reloaded boards');
  await page.getByRole('button', { name: 'Refresh boards', exact: true }).click();
  await expect.poll(() => boardLoads).toBe(loadedBoards + 1);
  await page.getByRole('list', { name: 'Boards', exact: true }).getByRole('button', { name: /Navigation board/ }).click();
  await page.getByRole('button', { name: 'Link board', exact: true }).click();
  await expect(page.getByText(/Board connected. Ticket sync failed:/)).toBeVisible();
  await expect(page.getByRole('list', { name: 'Linked boards' })).toContainText('Navigation board');
  await expect(page.getByRole('complementary', { name: 'Configuration' }).getByRole('button', { name: 'Sync tickets', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Close configuration', exact: true }).click();
  await page.getByRole('button', { name: 'Sync tickets', exact: true }).click();
  evidence.sync_clicked_at = new Date().toISOString();
  await expect(page.locator('.ticket-row')).toContainText('TOOLS-7');
  await expect(page.getByRole('button', { name: 'Sync tickets', exact: true })).toHaveAttribute('title', /Last successful ticket sync on this device/);
  // Reload drops the in-memory UI cache; the receipt must come from SQLite.
  evidence.reload_started_at = new Date().toISOString();
  await page.reload();
  evidence.reload_finished_at = new Date().toISOString();
  await page.getByRole('button', { name: 'All Bunkers', exact: true }).click();
  await page.locator('.bunker-overview > button').first().click();
  await page.locator('.project-row').filter({ hasText: 'Navigation tools' }).click();
  await expect(page.getByRole('button', { name: 'Sync tickets', exact: true })).toHaveAttribute('title', /Last successful ticket sync on this device/);
  await expect(page.locator('.ticket-row')).toContainText('TOOLS-7');
  await page.screenshot({ path: '/tmp/ticketbunker-board-sync-recovered.png' });
  await page.locator('.breadcrumbs .breadcrumb').first().click();
  await page.locator('.project-row').first().click();
  await page.getByRole('button', { name: 'Configure', exact: true }).click();
  await page.getByRole('button', { name: 'Checks', exact: true }).click();
  await expect(page.getByText('Discover automatically', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: '/tmp/ticketbunker-checks-cleanup.png' });
  const buildCommand = page.getByLabel('Build command', { exact: true });
  await buildCommand.fill('make navigation');
  await page.getByRole('button', { name: 'Repositories', exact: true }).click();
  await page.getByRole('button', { name: 'Add local repository', exact: true }).click();
  await page.getByLabel('Local repository path').fill(lab.repo);
  await page.getByRole('button', { name: 'Connect repository', exact: true }).click();
  await expect(page.getByLabel('Local repository path')).toHaveCount(0);
  await page.getByRole('button', { name: 'Checks', exact: true }).click();
  await expect(buildCommand).toHaveValue('make navigation');
  // Wide windows leave the ticket route exposed; smaller windows still expose
  // the sidebar. Exercise an actually reachable route without forcing a click
  // through the configuration drawer.
  const ticketRouteExposed = await page.locator('.ticket-row').first().evaluate(row => {
    const box = row.getBoundingClientRect();
    return row.contains(document.elementFromPoint(box.x + 20, box.y + 15));
  });
  evidence.unsaved_navigation_route = ticketRouteExposed ? 'ticket' : 'sidebar-project';
  if (ticketRouteExposed) await page.locator('.ticket-row').first().click({ position: { x: 20, y: 15 } });
  else await page.getByRole('button', { name: 'TOOLS Navigation tools', exact: true }).click({ position: { x: 30, y: 15 } });
  await page.getByRole('dialog', { name: 'You have unsaved changes' }).getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(buildCommand).toHaveValue('make navigation');
  await page.getByRole('button', { name: 'Close configuration', exact: true }).click();
  const configDialog = page.getByRole('dialog', { name: 'You have unsaved changes' });
  await expect(configDialog).toContainText('Save applies these settings');
  await page.screenshot({ path: '/tmp/ticketbunker-unsaved-configuration.png' });
  await configDialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(buildCommand).toHaveValue('make navigation');
  await page.getByRole('button', { name: 'Close configuration', exact: true }).click();
  await configDialog.getByRole('button', { name: 'Do not save', exact: true }).click();
  await page.getByRole('button', { name: 'Configure', exact: true }).click();
  await page.getByRole('button', { name: 'Checks', exact: true }).click();
  await expect(buildCommand).toHaveValue('');
  await page.getByLabel('Test command', { exact: true }).fill('python -m unittest');
  await page.getByRole('button', { name: 'Close configuration', exact: true }).click();
  await configDialog.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(configDialog).not.toBeVisible();
  await page.getByRole('button', { name: 'Configure', exact: true }).click();
  await page.getByRole('button', { name: 'Checks', exact: true }).click();
  await expect(page.getByLabel('Test command', { exact: true })).toHaveValue('python -m unittest');
  await page.getByRole('button', { name: 'Close configuration', exact: true }).click();
  await page.getByLabel('Search tickets').fill('absent');
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await page.locator('.ticket-row').first().click();
  const chapters = page.getByRole('navigation', { name: 'Ticket sections' });
  const moreDetails = page.locator('#details details.more-details');
  await expect(moreDetails).not.toHaveAttribute('open');
  await expect(page.locator('.facts-grid')).toContainText('Unassigned');
  await expect(page.locator('.facts-grid dt').filter({ hasText: /^Updated$|^Labels$|^Priority$/ })).toHaveCount(0);
  await moreDetails.getByText('More details', { exact: true }).click();
  await expect(moreDetails.getByText('Priority', { exact: true })).toBeVisible();
  await expect(moreDetails.getByText('Boards', { exact: true })).toBeVisible();
  await moreDetails.getByText('More details', { exact: true }).click();
  await page.screenshot({ path: '/tmp/ticketbunker-compact-details.png' });
  await expect(chapters).toBeVisible();
  await expect(page.getByRole('button', { name: 'Fetch branches', exact: true })).toHaveCount(0);
  await expect(page.locator('.branches-panel > header').getByRole('button', { name: 'Refresh branches' })).toBeVisible();
  await chapters.getByRole('button', { name: 'Agent work', exact: true }).click();
  await expect(chapters).toBeInViewport();
  await page.screenshot({ path: '/tmp/ticketbunker-ticket-chapters.png' });
  await page.setViewportSize({ width: 640, height: 900 });
  await chapters.getByRole('button', { name: 'Description', exact: true }).click();
  await expect(chapters).toBeInViewport();
  const chapterBoxes = await chapters.getByRole('button').evaluateAll((buttons) => buttons.map((button) => { const rect = button.getBoundingClientRect(); return { x: rect.x, y: rect.y }; }));
  expect(chapterBoxes[1].x).toBe(chapterBoxes[0].x);
  expect(chapterBoxes[1].y).toBeGreaterThan(chapterBoxes[0].y);
  await page.screenshot({ path: '/tmp/ticketbunker-ticket-chapters-narrow.png' });
  await page.setViewportSize(viewport);
  await page.getByRole('button', { name: 'Create branch', exact: true }).click();
  await page.getByLabel('Short description').fill('default-type');
  await page.getByRole('button', { name: 'Review branch' }).click();
  await page.locator('.confirmation').getByRole('button', { name: 'Create branch', exact: true }).click();
  await expect(page.locator('.branch-item')).toContainText('qa/ECS-4-default-type');
  const currentBranch = page.getByLabel('Current ticket branch', { exact: true });
  await expect(currentBranch).toContainText('qa/ECS-4-default-type');
  await expect(currentBranch).toContainText('Linked to ECS-4');
  await expect(currentBranch.getByRole('button', { name: 'Synthesize', exact: true })).toBeEnabled();
  await page.screenshot({ path: '/tmp/ticketbunker-current-branch.png' });
  await page.getByRole('button', { name: 'README.md line 1', exact: true }).click();
  await page.getByRole('button', { name: 'Open in editor', exact: true }).click();
  const workspace = page.getByRole('region', { name: 'Coal workspace' });
  await expect(workspace).toBeVisible();
  await expect(workspace.getByRole('tab', { name: 'README.md' })).toBeVisible();
  await expect(workspace.getByLabel('Active ticket session')).toHaveValue('ECS-4');
  const editor = workspace.getByRole('textbox', { name: /Repository file editor/ });
  await expect(editor).toBeVisible();
  const replace = async (text) => { await workspace.locator('.monaco-editor .view-lines').click({ position: { x: 20, y: 10 } }); await expect(editor).toBeFocused(); await page.keyboard.press('Control+a'); await page.keyboard.insertText(text); };
  const original = readFileSync(`${lab.repo}/README.md`, 'utf8');
  await replace('discard this draft\n');
  await workspace.getByRole('button', { name: 'Close README.md', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Close file' });
  await expect(dialog).toContainText('You have unsaved changes');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  await expect(workspace.getByRole('tab')).toContainText('•');
  // Ticket navigation retains the editor and its draft without a discard prompt.
  await workspace.getByRole('button', { name: 'ECS-4', exact: true }).click();
  await expect(chapters).toBeVisible();
  await chapters.getByRole('button', { name: 'Workspace', exact: true }).click();
  await expect(workspace.getByRole('tab')).toContainText('•');
  await workspace.getByRole('button', { name: 'Close README.md', exact: true }).click();
  await dialog.getByRole('button', { name: 'Do not save' }).click();
  if (readFileSync(`${lab.repo}/README.md`, 'utf8') !== original) throw new Error('Discard changed the file');
  await workspace.getByRole('navigation', { name: 'Repository files' }).getByRole('button', { name: 'README.md', exact: true }).click();
  await replace('retain this conflicting draft\n');
  writeFileSync(`${lab.repo}/README.md`, 'changed by another editor\n');
  await workspace.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(workspace.getByRole('alert')).toContainText('changed');
  if (readFileSync(`${lab.repo}/README.md`, 'utf8') !== 'changed by another editor\n') throw new Error('Conflicting save overwrote newer work');
  await workspace.getByRole('button', { name: 'Close README.md', exact: true }).click();
  await dialog.getByRole('button', { name: 'Do not save' }).click();
  await workspace.getByRole('navigation', { name: 'Repository files' }).getByRole('button', { name: 'README.md', exact: true }).click();
  await replace('saved through workspace\n');
  await page.keyboard.press('Control+s');
  await expect.poll(() => readFileSync(`${lab.repo}/README.md`, 'utf8')).toBe('saved through workspace\n');
  await page.screenshot({ path: '/tmp/ticketbunker-repository-workspace.png' });
  if (errors.length) throw new Error(errors.join('\n'));
  evidence.passed = true;
} catch (error) {
  evidence.failure = String(error);
  evidence.failure_body = page ? await bounded(page.locator('body').innerText(), 'Failure page capture').catch(() => 'Page unavailable') : 'Browser not started';
  evidence.fixture_alive_at_failure = fixture.exitCode === null && fixture.signalCode === null;
  if (page) await bounded(page.screenshot({ path: '/tmp/ticketbunker-hardening-failure.png' }), 'Failure screenshot').catch(captureError => { evidence.capture_error = String(captureError); });
  console.error(evidence.failure_body);
  console.error(errors);
  console.error(error);
  process.exitCode = 1;
} finally {
  evidence.errors = errors;
  phase = 'teardown';
  const cleanupErrors = [];
  const cleanup = async (promise, label) => { try { await bounded(promise, label); } catch (error) { cleanupErrors.push(String(error)); } };
  if (browser) await cleanup(browser.close(), 'Browser cleanup');
  await cleanup(Promise.allSettled(responseReads), 'Response diagnostics cleanup');
  if (!fixtureClosed && !fixture.stdin.destroyed) fixture.stdin.end('\n');
  await cleanup(fixtureExited, 'Fixture cleanup');
  if (!fixtureClosed && fixture.pid) {
    // This exact child belongs to this run. Never terminate a process group or
    // another test's fixture to hide a deadline failure.
    evidence.fixture_events.push({ phase, at: new Date().toISOString(), action: 'SIGTERM', pid: fixture.pid });
    fixture.kill('SIGTERM');
    await cleanup(fixtureExited, 'Fixture termination');
    if (!fixtureClosed) {
      evidence.fixture_events.push({ phase, at: new Date().toISOString(), action: 'SIGKILL', pid: fixture.pid });
      fixture.kill('SIGKILL');
      await cleanup(fixtureExited, 'Fixture forced termination');
    }
  }
  lines.close();
  evidence.cleanup_errors = cleanupErrors;
  evidence.cleanup_complete = cleanupErrors.length === 0 && fixtureClosed;
  evidence.finished_at = new Date().toISOString();
  if (!evidence.cleanup_complete) { evidence.flow_passed = evidence.passed; evidence.passed = false; process.exitCode = 1; }
  writeFileSync(join(output, 'results.json'), JSON.stringify(evidence, null, 2));
  console.log(`Hardening evidence: ${output}`);
  if (evidence.passed) console.log('PASS real-browser configuration, repository, ticket, branch and workspace flows; cleanup complete');
  // A timed-out browser/body promise may retain handles even after the owned
  // fixture was stopped. Evidence explicitly marks this incomplete, never PASS.
  if (!evidence.cleanup_complete) process.exit(1);
}
