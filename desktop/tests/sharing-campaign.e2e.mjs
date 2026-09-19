// Two isolated people, real app workers/HTTP service/SQLite/Git; no real providers.
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { existsSync, readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { chromium, expect } from '@playwright/test';
import { root, replaceEditor } from './workspace-simulation-support.mjs';

const output = process.env.TB_SIMULATION_OUTPUT || join(tmpdir(), 'ticketbunker-sharing-campaign');
mkdirSync(output, { recursive: true });
const worker = spawn(process.env.PYTHON || 'python3', ['tools/roleplay_lab.py'], {
  cwd: root, env: { ...process.env, PYTHONPATH: root }, stdio: ['pipe', 'pipe', 'pipe'],
});
let stderr = '';
worker.stderr.on('data', value => { stderr += value.toString(); });
worker.stdin.on('error', () => {});
const lines = createInterface({ input: worker.stdout });
function response() {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { lines.off('line', receive); reject(new Error('Lab response timeout')); }, 30000);
    const receive = line => { clearTimeout(timeout); try { const value = JSON.parse(line); if (value.error) reject(new Error(value.error)); else resolve(value); } catch (error) { reject(error); } };
    lines.once('line', receive);
  });
}
async function control(op, user = 'alice', extra = {}) {
  const reply = response(); worker.stdin.write(JSON.stringify({ op, user, ...extra }) + '\n'); return reply;
}
let browser;
const passed = [];
const errors = [];
let alice, bob;
try {
  const lab = await response();
  const manifest = JSON.parse(readFileSync(join(lab.root, 'lab.json'), 'utf8'));
  browser = await chromium.launch({ headless: true, executablePath: process.env.TB_E2E_CHROME || (existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : chromium.executablePath()) });
  const contexts = await Promise.all([browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'], viewport: { width: 1500, height: 1000 } }), browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'], viewport: { width: 1500, height: 1000 } })]);
  [alice, bob] = await Promise.all(contexts.map(context => context.newPage()));
  for (const [name, page] of [['alice', alice], ['bob', bob]]) {
    page.on('pageerror', error => errors.push(`${name}: ${error.message}`));
    await page.goto(lab.apps[name]);
    const identity = page.getByRole('heading', { name: 'Your local identity' });
    await expect(identity).toBeVisible();
    if (await identity.isVisible()) {
      await page.getByLabel('Name', { exact: true }).fill(name);
      await page.getByLabel('Email', { exact: true }).fill(`${name}@example.test`);
      await page.getByLabel('Branch name', { exact: true }).fill(name);
      await page.getByRole('button', { name: 'Continue', exact: true }).click();
    }
    await page.getByRole('button', { name: 'Use without agent', exact: true }).click();
    if (await page.getByRole('button', { name: 'Close settings' }).isVisible()) await page.getByRole('button', { name: 'Close settings' }).click();
  }
  async function capture(name, page = alice) { await page.screenshot({ path: join(output, `${name}.png`) }); }
  async function project(page) { await page.locator('.nav-project').filter({ hasText: 'NAV' }).click(); }
  async function bunker(page) { await page.locator('.bunker-nav > .nav-item').click(); }
  async function configuration(page, tab) {
    await project(page); await page.getByRole('button', { name: 'Configure', exact: true }).click();
    await page.getByRole('button', { name: tab, exact: true }).click();
  }
  async function closeConfiguration(page, discard = false) {
    await page.getByRole('button', { name: 'Close configuration', exact: true }).click();
    if (discard) await page.getByRole('button', { name: 'Do not save', exact: true }).click();
  }
  async function sync(page) {
    await bunker(page);
    await page.getByRole('button', { name: 'Sync now', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Sync now', exact: true })).toBeEnabled();
  }
  await alice.getByRole('button', { name: 'Create Bunker', exact: true }).click();
  const create = alice.getByRole('complementary', { name: 'Create Bunker' });
  await create.getByLabel('Bunker name').fill('Lantern team');
  await create.getByLabel('Allowed Git remotes').fill('ssh://git@git.example.test/team/dummy.git');
  await create.getByLabel('First project key').fill('NAV');
  await create.getByLabel('First project name').fill('Navigation');
  await create.getByRole('button', { name: 'Create Bunker', exact: true }).click();
  await expect(create).toHaveCount(0);
  await control('seed');
  await alice.reload();
  await bunker(alice);
  await alice.getByLabel('Bunker settings', { exact: true }).click();
  await alice.getByRole('button', { name: 'Repositories', exact: true }).click();
  await alice.getByLabel(/Projects for/).click();
  await alice.getByLabel(/NAV.*availability/).click();
  await expect(alice.getByLabel(/NAV.*availability/)).toBeChecked();
  await expect(alice.getByLabel(/NAV.*availability/)).toBeEnabled();
  await closeConfiguration(alice);
  await configuration(alice, 'Repositories');
  await alice.getByRole('button', { name: 'Add local repository' }).click();
  await alice.getByLabel('Local repository path').fill(manifest.people.alice.repo);
  await alice.getByRole('button', { name: 'Connect repository', exact: true }).click();
  await expect(alice.getByRole('button', { name: 'Change folder', exact: true })).toBeVisible();
  await closeConfiguration(alice);
  await bunker(alice);
  await alice.getByLabel('Bunker settings', { exact: true }).click();
  await alice.getByRole('button', { name: 'Share Bunker', exact: true }).click();
  const share = alice.getByRole('complementary', { name: 'Share Bunker' });
  await share.getByLabel('Server address').fill(lab.service);
  await share.getByLabel('Setup token').fill(manifest.setup_token);
  await share.getByRole('button', { name: 'Share Bunker', exact: true }).click();
  await expect(share).toHaveCount(0);
  await alice.getByLabel('Bunker settings', { exact: true }).click();
  await alice.getByRole('button', { name: 'Members', exact: true }).click();
  await alice.getByRole('button', { name: 'Create invitation', exact: true }).click();
  const invitation = await alice.locator('.invitation-link code').innerText();
  await alice.getByRole('complementary', { name: 'Members' }).getByRole('button', { name: 'Close', exact: true }).click();
  await bob.getByRole('button', { name: 'Join', exact: true }).click();
  const joinPanel = bob.getByRole('complementary', { name: 'Join Bunker' });
  await joinPanel.getByLabel('Invitation link').fill(invitation);
  await joinPanel.getByRole('button', { name: 'Join Bunker', exact: true }).click();
  await expect(joinPanel).toHaveCount(0);
  await configuration(bob, 'Repositories');
  await expect(bob.getByText('Not connected', { exact: true })).toBeVisible();
  await bob.getByRole('button', { name: 'Add local repository' }).click();
  await bob.getByLabel('Local repository path').fill(manifest.people.bob.repo);
  await bob.getByRole('button', { name: 'Connect repository', exact: true }).click();
  await expect(bob.getByRole('button', { name: 'Change folder', exact: true })).toBeVisible();
  await closeConfiguration(bob);
  passed.push('Create/share/invite/join and independent checkout attachment through the UI');

  await configuration(alice, 'Checks');
  await alice.getByLabel('Build command', { exact: true }).fill('python check.py');
  await alice.getByRole('button', { name: 'Save checks' }).click();
  await expect(alice.getByRole('button', { name: 'Save checks' })).toBeEnabled();
  await closeConfiguration(alice);
  await sync(bob);
  await configuration(bob, 'Checks');
  await expect(bob.getByLabel('Build command', { exact: true })).toHaveValue('python check.py');
  await expect(bob.getByRole('button', { name: 'Save checks' })).toBeDisabled();
  await capture('01-member-read-only-config', bob);
  await closeConfiguration(bob);
  passed.push('Owner configuration reaches member; member cannot edit shared checks');

  // Lose the acknowledgement after the real service commits a UI save.
  await configuration(alice, 'Checks');
  await control('drop-next-receipt');
  await alice.getByLabel('Build command', { exact: true }).fill('python check.py --receipt');
  await alice.getByRole('button', { name: 'Save checks' }).click();
  await expect(alice.locator('.configuration-drawer .inline-error')).toContainText('may already be saved');
  const lost = await control('inspect');
  expect(lost.pending).toHaveLength(1);
  expect(lost.pending[0].state).toBe('unknown');
  await closeConfiguration(alice, true);
  await sync(alice);
  const recovered = await control('inspect');
  expect(recovered.pending).toHaveLength(0);
  expect(recovered.service_changes).toBe(lost.service_changes);
  expect(recovered.dropped_receipts).toBe(1);
  passed.push('Lost save receipt reconciles without duplicate server writes');

  await configuration(alice, 'Checks');
  await control('stop-service');
  await alice.getByLabel('Build command', { exact: true }).fill('python check.py --offline');
  await alice.getByRole('button', { name: 'Save checks' }).click();
  await expect(alice.locator('.configuration-drawer .inline-error')).toContainText('pending');
  await capture('02-offline-save-kept');
  await closeConfiguration(alice, true);
  await control('start-service');
  await sync(alice);
  await sync(bob);
  await configuration(bob, 'Checks');
  await expect(bob.getByLabel('Build command', { exact: true })).toHaveValue('python check.py --offline');
  await closeConfiguration(bob);
  passed.push('Offline save survives closing its form and arrives after explicit reconnect/sync');

  // Two owner windows retain the same old revision; only one may win.
  const second = await contexts[0].newPage();
  await second.goto(lab.apps.alice);
  await configuration(alice, 'Checks');
  await configuration(second, 'Checks');
  await alice.getByLabel('Build command', { exact: true }).fill('python check.py --winner');
  await alice.getByRole('button', { name: 'Save checks' }).click();
  await expect(alice.getByRole('button', { name: 'Save checks' })).toBeEnabled();
  await second.getByLabel('Build command', { exact: true }).fill('python check.py --conflicting');
  await second.getByRole('button', { name: 'Save checks' }).click();
  await expect(second.locator('.configuration-drawer .inline-error')).toContainText('Configuration changed after this editor opened');
  await closeConfiguration(second, true);
  await sync(second);
  await configuration(second, 'Checks');
  await expect(second.getByLabel('Build command', { exact: true })).toHaveValue('python check.py --winner');
  await second.getByLabel('Build command', { exact: true }).fill('python check.py --reviewed');
  await second.getByRole('button', { name: 'Save checks' }).click();
  await expect(second.getByRole('button', { name: 'Save checks' })).toBeEnabled();
  expect((await control('inspect')).pending).toHaveLength(0);
  await second.close();
  await closeConfiguration(alice);
  passed.push('Outdated owner-window edit is rejected locally; the winning configuration is preserved');

  // Publish a real gated report through Synthesize, with only the model stubbed.
  await control('stub-enable');
  await control('git', 'alice', { args: ['switch', '-c', 'alice/NAV-1-obstacle'] });
  await control('git', 'bob', { args: ['switch', '-c', 'bob/NAV-1-obstacle'] });
  await alice.reload(); await project(alice);
  await alice.getByRole('button', { name: /^NAV-1\s/ }).click();
  const headingBox = await alice.locator('.ticket-heading').boundingBox();
  const headerBox = await alice.locator('.ticket-header').boundingBox();
  expect(headingBox.width).toBeGreaterThan(600);
  expect(headingBox.y + headingBox.height).toBeLessThanOrEqual(headerBox.y + headerBox.height);
  await project(bob);
  await bob.getByRole('button', { name: /^NAV-1\s/ }).click();
  await expect(bob.getByRole('region', { name: 'Current ticket branch' }).or(bob.locator('.current-ticket-branch'))).toBeVisible();
  await alice.getByRole('button', { name: 'Synthesize', exact: true }).click();
  await expect.poll(async () => (await control('inspect')).snapshot.reports.length, { timeout: 60000 }).toBeGreaterThan(0);
  await bob.getByRole('button', { name: 'Create branch', exact: true }).click();
  await bob.getByLabel(/^Existing branch/).selectOption('bob/NAV-1-obstacle');
  await bob.getByRole('button', { name: 'Attach branch', exact: true }).click();
  await expect.poll(async () => (await control('inspect', 'bob')).pending.map(row => row.state)).toContain('conflict');
  await bob.getByRole('button', { name: 'Ticket report transfers' }).click();
  await expect(bob.getByText('Review required', { exact: true })).toBeVisible();
  await bob.getByLabel('Bunker pipelines', { exact: true }).getByRole('button', { name: 'Sync Bunker', exact: true }).click();
  await expect(bob.getByLabel('Bunker pipelines', { exact: true }).getByRole('button', { name: 'Sync Bunker', exact: true })).toBeEnabled();
  await expect(bob.getByText('Review required', { exact: true })).toBeVisible();
  await capture('03-remote-conflict-not-overwritten', bob);
  await bob.getByRole('button', { name: 'Close pipelines' }).click();
  if (!await bob.getByLabel(/^Existing branch/).isVisible()) await bob.getByRole('button', { name: 'Create branch', exact: true }).click();
  await bob.getByLabel(/^Existing branch/).selectOption('bob/NAV-1-obstacle');
  await bob.getByRole('button', { name: 'Attach branch', exact: true }).click();
  await expect.poll(async () => (await control('inspect', 'bob')).pending.length).toBe(0);
  const reconciled = await control('inspect', 'bob');
  const assignments = reconciled.snapshot.tickets.find(row => row.id === 'NAV-1').payload.branch_assignments;
  expect(assignments[reconciled.bunker.repositories[0].id].slice().sort()).toEqual(['alice/NAV-1-obstacle', 'bob/NAV-1-obstacle']);
  passed.push('Cross-device branch-link conflict stays visible after pull; reviewed retry preserves both links');
  await bob.getByRole('button', { name: 'Back to tickets', exact: true }).click();
  await bob.getByRole('button', { name: /^NAV-1\s/ }).click();
  await bob.getByRole('button', { name: 'Agent work', exact: true }).click();
  await bob.getByLabel(/^Report scope/).selectOption('alice/NAV-1-obstacle');
  await expect(bob.locator('#agent-work')).toContainText('clearest ticket-related path');
  await capture('04-member-shared-report', bob);
  passed.push('Synthesize traverses normal gates; another participant reads the published report');

  // User-local drafts and local source edits must not become shared Bunker data.
  await alice.getByRole('button', { name: 'Ask ticket coworker' }).click();
  await alice.getByRole('textbox', { name: 'Message to ticket coworker' }).fill('PRIVATE-ALICE-SENT-CONVERSATION');
  await alice.locator('.coworker-drawer').getByRole('button', { name: 'Send', exact: true }).click();
  await expect(alice.locator('.coworker-drawer .user-message')).toContainText('PRIVATE-ALICE-SENT-CONVERSATION');
  await alice.getByRole('textbox', { name: 'Message to ticket coworker' }).fill('PRIVATE-ALICE-CHAT-ONLY');
  await alice.getByRole('button', { name: 'Collapse coworker' }).click();
  await alice.getByRole('button', { name: 'Workspace', exact: true }).click();
  const workspace = alice.getByRole('region', { name: /workspace$/ });
  await workspace.getByLabel('Find file or open path').fill('navigation.py');
  await workspace.getByRole('navigation', { name: 'Repository files' }).getByRole('button', { name: 'navigation.py', exact: true }).click();
  await replaceEditor(alice, workspace, '# PRIVATE-ALICE-DRAFT-ONLY\n');
  await expect(workspace.getByRole('tab', { name: 'navigation.py •', exact: true })).toBeVisible();
  const owner = await control('inspect');
  const member = await control('inspect', 'bob');
  const sharedText = JSON.stringify(owner.snapshot);
  for (const privateValue of ['PRIVATE-ALICE', manifest.people.alice.repo, manifest.people.bob.repo]) expect(sharedText).not.toContain(privateValue);
  expect(owner.bunker.repositories[0].local_path).toBe(manifest.people.alice.repo);
  expect(member.bunker.repositories[0].local_path).toBe(manifest.people.bob.repo);
  await bob.getByRole('button', { name: 'Ask ticket coworker' }).click();
  await expect(bob.getByRole('textbox', { name: 'Message to ticket coworker' })).toHaveValue('');
  await expect(bob.locator('.coworker-drawer')).not.toContainText('PRIVATE-ALICE-SENT-CONVERSATION');
  expect(readFileSync(join(manifest.people.bob.repo, 'navigation.py'), 'utf8')).not.toContain('PRIVATE-ALICE');
  await capture('05-private-workspace', alice);
  passed.push('Editor/chat drafts and absolute checkout paths remain private across both replicas');
  expect(errors).toEqual([]);
  writeFileSync(join(output, 'results.json'), JSON.stringify({ passed, errors }, null, 2));
  console.log(JSON.stringify({ passed, evidence: output }, null, 2));
} catch (error) {
  for (const [name, page] of [['alice', alice], ['bob', bob]]) if (page && !page.isClosed()) await page.screenshot({ path: join(output, `${name}-failure.png`) }).catch(() => {});
  writeFileSync(join(output, 'failure.txt'), `${error.stack}\n${stderr}`);
  throw error;
} finally {
  await browser?.close();
  worker.stdin.end(JSON.stringify({ op: 'quit' }) + '\n');
  await new Promise(resolve => {
    if (worker.exitCode !== null) return resolve();
    const timer = setTimeout(() => { worker.kill('SIGTERM'); resolve(); }, 10000);
    worker.once('exit', () => { clearTimeout(timer); resolve(); });
  });
  lines.close();
}
