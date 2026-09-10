import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { expect } from '@playwright/test';
import { startLab, enterProject, tabTo, activate, capture } from './workspace-simulation-support.mjs';

const output = process.env.TB_SIMULATION_OUTPUT || join(tmpdir(), 'ticketbunker-keyboard-simulation');
const lab = await startLab(output);
const page = await lab.context.newPage();
const results = { passed: false, checks: [], focus: {}, errors: [] };
page.on('pageerror', error => results.errors.push(error.message));
try {
  await enterProject(page, lab.url, true);
  // Initial onboarding uses pointer helpers; everything below is keyboard-only.
  await activate(page, page.getByRole('button', { name: 'Configure', exact: true }));
  await activate(page, page.getByRole('button', { name: 'Repositories', exact: true }));
  await activate(page, page.getByRole('row').filter({ hasText: 'Garden engine' }).getByRole('button', { name: 'Change folder' }));
  await activate(page, page.getByRole('button', { name: 'Browse folders' }));
  const picker = page.getByRole('region', { name: 'Choose repository folder' });
  await activate(page, picker.getByRole('button', { name: 'src', exact: true }));
  await expect(picker).toContainText(join(lab.repositories.engine, 'src'));
  await activate(page, picker.getByRole('button', { name: 'Parent folder' }));
  await activate(page, picker.getByRole('button', { name: 'Cancel', exact: true }));
  await expect(page.getByLabel('Local repository path')).toHaveValue(lab.repositories.engine);
  await activate(page, page.locator('form.branch-form').getByRole('button', { name: 'Cancel', exact: true }));
  await activate(page, page.getByRole('button', { name: 'Close configuration' }));
  results.checks.push('Keyboard folder navigation and cancellation leave attachment unchanged');
  await activate(page, page.locator('.project-repository-nav').getByRole('button', { name: /Garden engine/ }));
  const workspace = page.getByRole('region', { name: 'Garden engine workspace' });
  await activate(page, workspace.getByRole('button', { name: 'Hide explorer' }));
  await expect(workspace.getByRole('button', { name: 'Show explorer' })).toBeVisible();
  results.focus.after_hiding_explorer = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') || document.activeElement?.tagName);
  await activate(page, workspace.getByRole('button', { name: 'Show explorer' }));
  await expect(workspace.getByRole('button', { name: 'Hide explorer' })).toBeVisible();
  results.focus.after_showing_explorer = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') || document.activeElement?.tagName);
  await activate(page, workspace.getByRole('button', { name: 'Hide ticket context' }));
  await expect(workspace.getByRole('button', { name: 'Show ticket context' })).toBeVisible();
  results.focus.after_hiding_context = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') || document.activeElement?.tagName);
  await activate(page, workspace.getByRole('button', { name: 'Show ticket context' }));
  const path = workspace.getByLabel('Find file or open path');
  await tabTo(page, path);
  await page.keyboard.type('README.md');
  await page.keyboard.press('Enter');
  const editor = workspace.getByRole('textbox', { name: /Repository file editor/ });
  await tabTo(page, editor);
  await page.keyboard.press('Control+End');
  await page.keyboard.type('\nKeyboard-only draft');
  await expect(workspace.getByRole('tab', { name: 'README.md •', exact: true })).toBeVisible();
  // Monaco advertises Ctrl+M as the keyboard escape from indentation/tab capture.
  await page.keyboard.press('Control+m');
  await page.keyboard.press('Tab');
  await expect(editor).not.toBeFocused();
  await activate(page, workspace.getByRole('button', { name: 'Close README.md' }));
  const dialog = page.getByRole('dialog', { name: 'Close file' });
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(workspace.getByRole('button', { name: 'Close README.md' })).toBeFocused();
  const disk = `${readFileSync(join(lab.repositories.engine, 'README.md'), 'utf8')}\nExternal edit\n`;
  writeFileSync(join(lab.repositories.engine, 'README.md'), disk);
  await activate(page, workspace.getByRole('button', { name: 'Save', exact: true }));
  await expect(workspace.getByRole('alert')).toContainText(/changed/);
  expect(readFileSync(join(lab.repositories.engine, 'README.md'), 'utf8')).toBe(disk);
  await activate(page, workspace.getByRole('button', { name: 'Dismiss workspace error' }));
  await activate(page, workspace.getByRole('button', { name: 'Close README.md' }));
  await activate(page, dialog.getByRole('button', { name: 'Do not save', exact: true }));
  await expect(workspace.getByRole('tab')).toHaveCount(0);
  results.checks.push('Editor keyboard escape, close dialog focus, failed-save recovery');
  await capture(page, join(output, 'keyboard-finished.png'));
  expect(results.errors).toEqual([]);
  // Losing focus to BODY after hiding/removing a drawer control is a regression,
  // even when a determined user can Tab through the whole window to recover it.
  expect(results.focus.after_hiding_explorer).toBe('Show explorer');
  expect(results.focus.after_showing_explorer).toBe('Hide explorer');
  expect(results.focus.after_hiding_context).toBe('Show ticket context');
  results.passed = true;
} catch (error) {
  results.failure = String(error);
  await page.screenshot({ path: join(output, 'failure.png') }).catch(() => {});
  throw error;
} finally {
  writeFileSync(join(output, 'results.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
  await lab.stop();
}
