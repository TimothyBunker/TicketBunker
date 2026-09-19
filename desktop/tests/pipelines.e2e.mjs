// Real UI and read-only backend projection in the disposable Grid Garden lab.
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { expect } from '@playwright/test';
import { startLab, enterProject, capture } from './workspace-simulation-support.mjs';

const output = process.env.TB_SIMULATION_OUTPUT || join(tmpdir(), 'ticketbunker-pipelines');
const lab = await startLab(output);
try {
  const page = await lab.context.newPage();
  const actions = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.url().endsWith('/api/action')) actions.push(request.postDataJSON().action);
  });
  await enterProject(page, lab.url, true);
  const opener = page.getByRole('button', { name: 'Project transfer status' });
  await opener.click();
  const panel = page.getByRole('complementary', { name: 'Bunker pipelines' });
  await expect(panel.getByText('No board linked', { exact: true })).toBeVisible();
  await expect(panel.getByText('Local authority', { exact: true }).first()).toBeVisible();
  await expect(panel.locator('details[open] summary')).toContainText('Tracker import');
  await capture(page, join(output, 'project-pipelines.png'));
  await panel.locator('summary').filter({ hasText: 'Private workspace' }).click();
  await expect(panel.getByText(/configured model provider/)).toBeVisible();
  await panel.getByRole('button', { name: 'Refresh pipeline status' }).click();
  await expect(panel.getByText(/configured model provider/)).toBeVisible();
  await panel.getByRole('button', { name: 'Close pipelines' }).click();
  await expect(opener).toBeFocused();
  await page.locator('.ticket-row').filter({ hasText: 'LAB-1' }).click();
  await page.getByRole('button', { name: 'Ticket report transfers' }).click();
  await expect(panel.locator('details[open] summary')).toContainText('Report publication');
  await expect(panel.getByRole('button', { name: 'Sync Bunker' })).toHaveCount(0);
  await capture(page, join(output, 'ticket-pipelines.png'));
  await page.setViewportSize({ width: 480, height: 800 });
  await expect(panel).toBeVisible();
  expect(await panel.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await capture(page, join(output, 'narrow-pipelines.png'));
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
  expect(actions.filter(action => action === 'application.bunker.pipelines').length).toBeGreaterThanOrEqual(3);
  expect(actions).not.toContain('application.bunker.sync');
  expect(actions).not.toContain('application.project.sync');
  expect(errors).toEqual([]);
  console.log('Pipeline browser flow passed: local authority, scoped disclosures, read-only refresh, focus return, narrow layout.');
} finally { await lab.stop(); }

process.env.TB_PIPELINE_SHARED_LAB = '1';
const shared = await startLab(join(output, 'shared'));
try {
  const page = await shared.context.newPage();
  await enterProject(page, shared.url, true);
  await page.getByRole('button', { name: 'Project transfer status' }).click();
  const panel = page.getByRole('complementary', { name: 'Bunker pipelines' });
  await expect(panel.getByText('Shared authority → local copy', { exact: true })).toBeVisible();
  await panel.locator('summary').filter({ hasText: 'Shared saves' }).click();
  await expect(panel.getByText('Review required', { exact: true })).toBeVisible();
  await panel.getByRole('button', { name: 'Sync Bunker', exact: true }).click();
  await expect(panel.getByRole('alert')).toContainText('conflicting changes');
  await expect(panel.getByRole('button', { name: 'Sync Bunker', exact: true })).toBeEnabled();
  await expect(panel.getByText('Review required', { exact: true })).toBeVisible();
  await capture(page, join(output, 'shared', 'conflict-after-sync.png'));
  await panel.getByRole('button', { name: 'Review', exact: true }).click();
  await expect(page.locator('.ticket-heading')).toContainText('LAB-1');
  await page.getByRole('button', { name: 'Ticket report transfers' }).click();
  await expect(panel.getByText('LAB-1 saves', { exact: true })).toBeVisible();
  await expect(panel.getByText('Review required', { exact: true })).toBeVisible();
  console.log('Shared pipeline flow passed: real service conflict survives snapshot sync; Review opens its ticket.');
} finally { delete process.env.TB_PIPELINE_SHARED_LAB; await shared.stop(); }
