import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { expect } from '@playwright/test';
import { startLab, enterProject, selectRepo, replaceEditor, editorText, capture } from './workspace-simulation-support.mjs';

const output = process.env.TB_SIMULATION_OUTPUT || join(tmpdir(), 'ticketbunker-multiwindow-simulation');
const lab = await startLab(output);
const first = await lab.context.newPage();
const second = await lab.context.newPage();
const result = { passed: false, checks: [], errors: [] };
for (const page of [first, second]) page.on('pageerror', error => result.errors.push(error.message));
const saved = (page, marker) => page.waitForResponse(response => {
  try { const body = response.request().postDataJSON(); return body?.action === 'workspace.save' && body.payload?.state?.tabs?.some(tab => tab.content.includes(marker)); } catch { return false; }
});
async function openReadme(page, workspace) {
  const path = workspace.getByLabel('Find file or open path');
  await path.fill('README.md');
  await path.press('Enter');
  await expect(workspace.getByRole('textbox', { name: /Repository file editor/ })).toBeVisible();
}
try {
  await enterProject(first, lab.url, true);
  let a = await selectRepo(first);
  await openReadme(first, a);
  const original = readFileSync(join(lab.repositories.engine, 'README.md'), 'utf8');
  const draftA = `${original}\nFirst window: preserve the engine edge contract.\n`;
  const draftB = `${original}\nSecond window: compare renderer coordinates.\n`;
  let receipt = saved(first, 'First window');
  await replaceEditor(first, a, draftA);
  await receipt;
  await enterProject(second, lab.url);
  const b = await selectRepo(second);
  await expect(b.getByRole('tab', { name: 'README.md •', exact: true })).toBeVisible();
  receipt = saved(second, 'Second window');
  await replaceEditor(second, b, draftB);
  await receipt;
  await first.bringToFront();
  receipt = saved(first, 'additional independent');
  await replaceEditor(first, a, `${draftA}\nAn additional independent thought.\n`);
  await receipt;
  await expect(a.getByRole('alert')).toContainText(/another window|conflict|recovery/i);
  result.conflict = await a.getByRole('alert').innerText();
  const expectedA = await editorText(first, a);
  const secondSettled = saved(second, 'Second window');
  const expectedB = await editorText(second, b);
  await secondSettled;
  expect(expectedA).toContain('First window');
  expect(expectedB).toContain('Second window');
  expect(readFileSync(join(lab.repositories.engine, 'README.md'), 'utf8')).toBe(original);
  result.checks.push('Concurrent recovery revision conflict is reported; both live drafts and disk stay separate');
  await capture(first, join(output, '01-recovery-conflict.png'));
  await first.bringToFront();
  const reloadedRecovery = saved(first, 'additional independent');
  first.once('dialog', dialog => dialog.accept());
  await first.reload();
  await first.locator('.nav-project').filter({ hasText: 'Grid Garden' }).click();
  a = await selectRepo(first);
  await expect(a.getByRole('textbox', { name: /Repository file editor/ })).toBeVisible();
  result.after_reload = await editorText(first, a);
  result.expected_first_window = expectedA;
  await reloadedRecovery;
  // Observe durable recovery through the real Refresh UI, not a mocked command.
  const durableRead = first.waitForResponse(response => {
    try { return response.request().postDataJSON()?.action === 'workspace.open'; } catch { return false; }
  });
  await a.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  const durable = await (await durableRead).json();
  result.durable_after_first_reload = durable.state.tabs.find(tab => tab.path === 'README.md')?.content;
  expect(result.durable_after_first_reload, 'Restoring A locally must not silently overwrite B’s durable recovery snapshot').toBe(expectedB);
  await capture(first, join(output, '02-after-reload.png'));
  expect(result.after_reload, 'Reload must not silently replace this window’s independently edited draft with another window’s recovery state').toBe(expectedA);
  await second.bringToFront();
  second.once('dialog', dialog => dialog.accept());
  await second.reload();
  await second.locator('.nav-project').filter({ hasText: 'Grid Garden' }).click();
  const reopenedB = await selectRepo(second);
  await expect(reopenedB.getByRole('textbox', { name: /Repository file editor/ })).toBeVisible();
  result.second_after_reload = await editorText(second, reopenedB);
  expect(result.second_after_reload, 'Recovering the first window must not replace the second window’s draft').toBe(expectedB);
  expect(readFileSync(join(lab.repositories.engine, 'README.md'), 'utf8')).toBe(original);
  result.checks.push('Conflicting first-window draft remains recoverable after reload');
  expect(result.errors).toEqual([]);
  result.passed = true;
} catch (error) {
  result.failure = String(error);
  await first.screenshot({ path: join(output, 'failure.png') }).catch(() => {});
  throw error;
} finally {
  writeFileSync(join(output, 'results.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  await lab.stop();
}
