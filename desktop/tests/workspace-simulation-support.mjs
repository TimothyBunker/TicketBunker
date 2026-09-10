import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { existsSync, mkdirSync } from 'node:fs';
import { delimiter, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';

export const root = fileURLToPath(new URL('../../', import.meta.url));

export async function startLab(output) {
  mkdirSync(output, { recursive: true });
  const fixture = spawn(process.env.PYTHON || 'python', ['tests/workspace_simulation_fixture.py'], {
    cwd: root, env: { ...process.env, PYTHONPATH: `${root}${delimiter}${join(root, 'tests')}` }, stdio: ['pipe', 'pipe', 'inherit'],
  });
  // A failed/terminated fixture can close stdin while teardown is signaling it.
  fixture.stdin.on('error', () => {});
  const lines = createInterface({ input: fixture.stdout });
  let timer;
  let browser;
  let stopping;
  const interrupted = () => { void stop().finally(() => process.exit(143)); };
  process.once('SIGTERM', interrupted);
  async function stop() {
    if (stopping) return stopping;
    stopping = cleanup();
    return stopping;
  }
  async function cleanup() {
    process.removeListener('SIGTERM', interrupted);
    await browser?.close();
    fixture.stdin.end('\n');
    lines.close();
    if (fixture.exitCode !== null) return;
    await new Promise(resolve => {
      const kill = setTimeout(() => { fixture.kill('SIGTERM'); resolve(); }, 10000);
      fixture.once('exit', () => { clearTimeout(kill); resolve(); });
    });
  }
  try {
    const lab = await new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new Error('Simulation fixture did not start within 30 seconds')), 30000);
      lines.once('line', line => { try { resolve(JSON.parse(line)); } catch (error) { reject(error); } });
      fixture.once('error', reject);
      fixture.once('exit', code => reject(new Error(`Fixture exited ${code}`)));
    });
    clearTimeout(timer);
    browser = await chromium.launch({ executablePath: process.env.TB_E2E_CHROME || (existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : chromium.executablePath()), headless: process.env.TB_SIMULATION_HEADED !== '1' });
    const context = await browser.newContext({ viewport: { width: 1500, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'] });
    return { ...lab, browser, context, stop };
  } catch (error) {
    clearTimeout(timer);
    await stop();
    throw error;
  }
}

export async function enterProject(page, url, first = false) {
  await page.goto(url);
  if (first) {
    await page.getByRole('button', { name: 'Use without agent', exact: true }).click();
    const closeSettings = page.getByRole('button', { name: 'Close settings' });
    if (await closeSettings.isVisible()) await closeSettings.click();
  }
  await page.locator('.nav-project').filter({ hasText: 'Grid Garden' }).click();
}

export async function selectRepo(page, name = 'Garden engine') {
  await page.locator('.project-repository-nav').getByRole('button', { name: new RegExp(name) }).click();
  const workspace = page.getByRole('region', { name: `${name} workspace` });
  await expect(workspace).toBeVisible();
  return workspace;
}

// Tab through real document focus order instead of programmatic focus/click.
export async function tabTo(page, target, limit = 100) {
  await expect(target).toBeVisible();
  for (let step = 0; step < limit; step++) {
    if (await target.evaluate(element => element === document.activeElement)) return;
    await page.keyboard.press('Tab');
  }
  throw new Error(`Keyboard could not reach ${await target.getAttribute('aria-label') || await target.textContent()} within ${limit} tabs`);
}

export async function activate(page, target) {
  await tabTo(page, target);
  await page.keyboard.press('Enter');
}

export async function replaceEditor(page, workspace, content) {
  await workspace.locator('.monaco-editor .view-lines').click({ position: { x: 20, y: 10 } });
  await page.keyboard.press('Control+a');
  await page.evaluate(text => navigator.clipboard.writeText(text), content);
  await page.keyboard.press('Control+v');
}

export async function editorText(page, workspace) {
  await workspace.locator('.monaco-editor .view-lines').click({ position: { x: 20, y: 10 } });
  await page.keyboard.press('Control+a');
  await page.keyboard.press('Control+c');
  return page.evaluate(() => navigator.clipboard.readText());
}
