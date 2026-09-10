// Repeatable role-play: fix Grid Garden's edge clamp while comparing its renderer.
// Real UI/worker/Git/SQLite; isolated temporary data and local-only Git remotes.
import { spawn, spawnSync } from 'node:child_process';
import { createInterface } from 'node:readline';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { delimiter, join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';

const root = fileURLToPath(new URL('../../', import.meta.url));
const output = process.env.TB_SIMULATION_OUTPUT || join(tmpdir(), 'ticketbunker-workspace-simulation');
mkdirSync(output, { recursive: true });
const fixture = spawn(process.env.PYTHON || 'python', ['tests/workspace_simulation_fixture.py'], {
  cwd: root, env: { ...process.env, PYTHONPATH: `${root}${delimiter}${join(root, 'tests')}` }, stdio: ['pipe', 'pipe', 'inherit'],
});
const lines = createInterface({ input: fixture.stdout });
const lab = await new Promise((resolve, reject) => {
  lines.once('line', line => { try { resolve(JSON.parse(line)); } catch (error) { reject(error); } });
  fixture.once('exit', code => reject(new Error(`Fixture exited ${code}`)));
});
let browser;
let page;
const errors = [];
const metrics = { scenarios: [], typing: {} };
let passed = false;
try {
  for (const repo of Object.values(lab.repositories)) {
    const baseline = spawnSync(process.env.PYTHON || 'python', ['-m', 'unittest', 'test_grid', '-q'], { cwd: repo, encoding: 'utf8' });
    expect(baseline.status, baseline.stderr).toBe(1);
    expect(baseline.stderr).toContain('AssertionError');
  }
  browser = await chromium.launch({ executablePath: process.env.TB_E2E_CHROME || (existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : chromium.executablePath()), headless: process.env.TB_SIMULATION_HEADED !== '1' });
  page = await browser.newPage({ viewport: { width: 1500, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'] });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(lab.url);
  await page.getByRole('button', { name: 'Use without agent', exact: true }).click();
  const closeSettings = page.getByRole('button', { name: 'Close settings' });
  if (await closeSettings.isVisible()) await closeSettings.click();
  await page.locator('.nav-project').filter({ hasText: 'Grid Garden' }).click();
  await expect(page.locator('.ticket-row')).toHaveCount(3);
  await page.getByRole('button', { name: 'Configure', exact: true }).click();
  await page.getByRole('button', { name: 'Repositories', exact: true }).click();
  const row = page.getByRole('row').filter({ hasText: 'Garden engine' });
  await row.getByRole('button', { name: 'Change folder', exact: true }).click();
  await expect(page.getByLabel('Local repository path')).toHaveValue(lab.repositories.engine);
  await page.getByRole('button', { name: 'Browse folders' }).click();
  const picker = page.getByRole('region', { name: 'Choose repository folder' });
  await expect(picker).toContainText(lab.repositories.engine);
  await picker.getByRole('button', { name: 'src', exact: true }).click();
  await expect(picker).toContainText(join(lab.repositories.engine, 'src'));
  await picker.getByRole('button', { name: 'Parent folder', exact: true }).click();
  await picker.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.getByLabel('Local repository path')).toHaveValue(lab.repositories.engine);
  await page.locator('form.branch-form').getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(row).toContainText(lab.repositories.engine);
  await page.getByRole('button', { name: 'Close configuration', exact: true }).click();
  metrics.scenarios.push('Repository folder browsing and cancellation preserve the existing attachment');
  const selectRepo = async name => {
    await page.locator('.project-repository-nav').getByRole('button', { name: new RegExp(name) }).click();
    const workspace = page.getByRole('region', { name: `${name} workspace` });
    await expect(workspace).toBeVisible();
    await expect(workspace.getByLabel('Find file or open path')).toBeVisible();
    return workspace;
  };
  const openPath = async (workspace, path) => {
    const input = workspace.getByLabel('Find file or open path');
    await input.fill(path);
    await workspace.getByRole('navigation', { name: 'Repository files' }).getByRole('button', { name: path, exact: true }).click();
    await expect(workspace.getByRole('tab', { name: path.split('/').pop(), exact: true })).toBeVisible();
  };
  const replace = async (workspace, text) => {
    await workspace.locator('.monaco-editor .view-lines').click({ position: { x: 20, y: 10 } });
    await page.keyboard.press('Control+a');
    await page.evaluate(value => navigator.clipboard.writeText(value), text);
    await page.keyboard.press('Control+v');
  };
  const addOverlay = async (workspace, id) => {
    await workspace.getByRole('button', { name: 'Add ticket overlay' }).click();
    await workspace.getByLabel('Find ticket overlay').fill(id);
    await workspace.locator('.overlay-picker').getByRole('button', { name: new RegExp(`^${id} `) }).click();
    await expect(workspace.getByLabel('Active ticket session')).toHaveValue(id);
  };
  let engine = await selectRepo('Garden engine');
  await expect(engine.locator('.repository-workspace-header')).toContainText('qa/LAB-1-grid');
  await engine.getByLabel('Find file or open path').fill('ignored');
  await expect(engine.getByRole('button', { name: 'cache/ignored.txt', exact: true })).toHaveCount(0);
  await openPath(engine, 'src/grid.py');
  const original = readFileSync(join(lab.repositories.engine, 'src/grid.py'), 'utf8');
  const fix = original.replace('min(width,', 'min(width - 1,');
  await replace(engine, fix);
  await expect(engine.getByRole('tab', { name: 'grid.py •', exact: true })).toBeVisible();
  await openPath(engine, 'docs/design.md');
  await expect(engine.getByRole('tab')).toHaveCount(2);
  await engine.getByRole('button', { name: 'Hide explorer', exact: true }).click();
  await expect(engine.getByRole('navigation', { name: 'Repository files' })).not.toBeVisible();
  await engine.getByRole('button', { name: 'Show explorer', exact: true }).click();
  await engine.getByRole('button', { name: 'Hide ticket context', exact: true }).click();
  await engine.getByRole('button', { name: 'Show ticket context', exact: true }).click();
  metrics.scenarios.push('File search, independent open tabs, explorer/context drawers');
  await addOverlay(engine, 'LAB-1');
  await addOverlay(engine, 'LAB-3');
  await engine.getByLabel('Active ticket session').selectOption('LAB-1');
  await engine.getByRole('button', { name: 'Session', exact: true }).click();
  const message = engine.getByRole('textbox', { name: 'Message to ticket coworker' });
  await message.fill('Should this coordinate contract mention an inclusive lower bound?');
  await engine.getByRole('tab', { name: 'grid.py •', exact: true }).click();
  await expect(message).toHaveValue('');
  await engine.getByRole('tab', { name: 'design.md', exact: true }).click();
  await expect(message).toHaveValue('Should this coordinate contract mention an inclusive lower bound?');
  await engine.getByLabel('Active ticket session').selectOption('LAB-3');
  await expect(message).toHaveValue('');
  await engine.getByLabel('Active ticket session').selectOption('LAB-1');
  await expect(message).toHaveValue('Should this coordinate contract mention an inclusive lower bound?');
  await expect(engine.locator('.workspace-session:not([hidden])').getByText('Loading…', { exact: true })).not.toBeVisible();
  await expect(engine.locator('.workspace-session:not([hidden]) .inline-error')).toHaveCount(0);
  await engine.getByRole('tab', { name: 'grid.py •', exact: true }).click();
  await engine.locator('.monaco-editor .view-lines').click({ position: { x: 20, y: 10 } });
  await page.keyboard.press('Control+Home');
  await page.keyboard.press('Shift+End');
  await engine.getByRole('button', { name: 'Discuss selection', exact: true }).click();
  await expect(engine.locator('.workspace-session:not([hidden]) .quote-chip')).toContainText('src/grid.py');
  await expect(engine.locator('.workspace-session:not([hidden]) .quote-chip')).toContainText('Grid Garden');
  await engine.getByRole('button', { name: 'Remove quoted context', exact: true }).click();
  metrics.scenarios.push('Multiple ticket overlays; unsent questions scoped by ticket and file');
  await page.screenshot({ path: join(output, '01-engine-context.png') });
  const renderer = await selectRepo('Garden renderer');
  await expect(renderer.locator('.repository-workspace-header')).toContainText('qa/LAB-2-grid');
  await openPath(renderer, 'src/grid.py');
  const rendererOriginal = readFileSync(join(lab.repositories.renderer, 'src/grid.py'), 'utf8');
  await replace(renderer, rendererOriginal.replace('{position}', '{position + 1}'));
  engine = await selectRepo('Garden engine');
  await expect(engine.getByRole('tab', { name: 'grid.py •', exact: true })).toBeVisible();
  await engine.getByRole('tab', { name: 'grid.py •', exact: true }).click();
  await engine.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(() => readFileSync(join(lab.repositories.engine, 'src/grid.py'), 'utf8')).toBe(fix);
  const check = spawnSync(process.env.PYTHON || 'python', ['-m', 'unittest', 'test_grid', '-q'], { cwd: lab.repositories.engine, encoding: 'utf8' });
  expect(check.status, check.stderr).toBe(0);
  expect(readFileSync(join(lab.repositories.renderer, 'src/grid.py'), 'utf8')).toBe(rendererOriginal);
  metrics.scenarios.push('Two repositories keep separate drafts; saving only changes the selected checkout');
  await replace(engine, `${fix}\n# external-edit conflict demonstration\n`);
  const external = `${fix}\n# Simulated teammate edit outside TicketBunker\n`;
  writeFileSync(join(lab.repositories.engine, 'src/grid.py'), external);
  await engine.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(engine.getByRole('alert')).toContainText(/changed/);
  expect(readFileSync(join(lab.repositories.engine, 'src/grid.py'), 'utf8')).toBe(external);
  await page.screenshot({ path: join(output, '02-external-edit-conflict.png') });
  await engine.getByRole('button', { name: 'Close src/grid.py', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Close file' });
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(engine.getByRole('tab', { name: 'grid.py •', exact: true })).toBeVisible();
  await engine.getByRole('button', { name: 'Close src/grid.py', exact: true }).click();
  await dialog.getByRole('button', { name: 'Do not save', exact: true }).click();
  await engine.getByRole('button', { name: 'Dismiss workspace error' }).click();
  await openPath(engine, 'src/grid.py');
  metrics.scenarios.push('External edit conflicts never overwrite disk; close/cancel/discard work');
  // Diagnostic input-to-next-paint samples, not a hardware-sensitive pass/fail SLA.
  await engine.locator('.monaco-editor .view-lines').click({ position: { x: 20, y: 10 } });
  await page.keyboard.press('Control+End');
  await page.evaluate(() => {
    window.__typingPaints = [];
    document.addEventListener('keydown', event => {
      if (event.key.length !== 1 || !event.target.closest('.monaco-editor')) return;
      const start = performance.now();
      requestAnimationFrame(() => window.__typingPaints.push(performance.now() - start));
    });
  });
  await page.keyboard.type('# Simulation typing responsiveness sample', { delay: 20 });
  const samples = await page.evaluate(() => window.__typingPaints);
  const sorted = [...samples].sort((a, b) => a - b);
  metrics.typing = { samples: samples.length, median_keydown_to_frame_ms: sorted[Math.floor(sorted.length / 2)], p95_keydown_to_frame_ms: sorted[Math.floor(sorted.length * .95)] };
  await page.screenshot({ path: join(output, '03-two-repository-workspace.png') });
  // Wait for the production draft recovery write; a reload remounts all editors.
  await page.waitForResponse(response => {
    try { return response.request().postDataJSON()?.action === 'workspace.save' && response.ok(); }
    catch { return false; }
  });
  page.once('dialog', dialog => dialog.accept());
  await page.reload();
  await page.locator('.nav-project').filter({ hasText: 'Grid Garden' }).click();
  engine = await selectRepo('Garden engine');
  await expect(engine.getByRole('tab', { name: 'grid.py •', exact: true })).toBeVisible();
  const restoredRenderer = await selectRepo('Garden renderer');
  await expect(restoredRenderer.getByRole('tab', { name: 'grid.py •', exact: true })).toBeVisible();
  await restoredRenderer.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(() => readFileSync(join(lab.repositories.renderer, 'src/grid.py'), 'utf8')).toBe(rendererOriginal.replace('{position}', '{position + 1}'));
  const rendererCheck = spawnSync(process.env.PYTHON || 'python', ['-m', 'unittest', 'test_grid', '-q'], { cwd: lab.repositories.renderer, encoding: 'utf8' });
  expect(rendererCheck.status, rendererCheck.stderr).toBe(0);
  metrics.scenarios.push('Browser reload restores both repository drafts; both mock bug fixes pass their tests');
  expect(errors).toEqual([]);
  passed = true;
  console.log(JSON.stringify({ passed: true, output, ...metrics }, null, 2));
} catch (error) {
  metrics.failure = String(error);
  await page?.screenshot({ path: join(output, 'failure.png') }).catch(() => {});
  throw error;
} finally {
  writeFileSync(join(output, 'results.json'), JSON.stringify({ passed, ...metrics, browser_errors: errors }, null, 2));
  await browser?.close();
  fixture.stdin.end('\n');
  lines.close();
}
