// Bounded repeated role-play with separate evidence per run and clean shutdown.
import { spawn } from 'node:child_process';
import { createWriteStream, mkdirSync, readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { once } from 'node:events';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';

const options = Object.fromEntries(process.argv.slice(2).map(value => value.replace(/^--/, '').split('=')));
const runs = Number(options.runs || (options.duration ? 100 : 3));
const timeoutMs = Number(options.timeout || 180000);
const durationMs = options.duration ? Number(options.duration) : null;
const suites = {
  roleplay: 'workspace-simulation.e2e.mjs',
  keyboard: 'workspace-keyboard-simulation.e2e.mjs',
  multiwindow: 'workspace-multiwindow-simulation.e2e.mjs',
  failures: 'workspace-failure-simulation.e2e.mjs',
};
const journeys = (options.journeys || 'roleplay,keyboard,multiwindow').split(',');
if (!Number.isInteger(runs) || runs < 1 || runs > 100 || !Number.isFinite(timeoutMs) || timeoutMs < 1000 || (durationMs !== null && (!Number.isFinite(durationMs) || durationMs < 1000 || durationMs > 86400000)) || journeys.some(name => !suites[name])) {
  throw new Error('Use --runs=1..100 --journeys=roleplay,keyboard,multiwindow,failures --timeout=180000 --duration=1200000 --fail-fast=1 --output=/path');
}
const output = options.output || join(tmpdir(), `ticketbunker-qa-${new Date().toISOString().replace(/[:.]/g, '-')}`);
mkdirSync(output, { recursive: true });
const root = fileURLToPath(new URL('../../', import.meta.url));
const startedAt = Date.now();
const report = { started_at: new Date(startedAt).toISOString(), requested_duration_ms: durationMs, passed: true, runs: [] };
const deadline = durationMs === null ? Infinity : startedAt + durationMs;
const bytes = path => readdirSync(path, { withFileTypes: true }).reduce((total, item) => total + (item.isDirectory() ? bytes(join(path, item.name)) : item.isFile() ? statSync(join(path, item.name)).size : 0), 0);
let current;
let interrupted = false;
let halted = false;
process.once('SIGINT', () => { interrupted = true; current?.kill('SIGTERM'); });
for (let run = 1; run <= runs && !interrupted && !halted && Date.now() < deadline; run++) {
  for (const journey of journeys) {
    if (interrupted || halted) break;
    const directory = join(output, `${String(run).padStart(3, '0')}-${journey}`);
    mkdirSync(directory, { recursive: true });
    const log = createWriteStream(join(directory, 'run.log'));
    const started = Date.now();
    current = spawn(process.execPath, [join('desktop', 'tests', suites[journey])], {
      cwd: root, env: { ...process.env, TB_SIMULATION_OUTPUT: directory }, stdio: ['ignore', 'pipe', 'pipe'], detached: process.platform !== 'win32',
    });
    current.stdout.pipe(log, { end: false });
    current.stderr.pipe(log, { end: false });
    let timedOut = false;
    let forceTimer;
    const timeout = setTimeout(() => {
      timedOut = true;
      current.kill('SIGTERM');
      forceTimer = setTimeout(() => {
        try {
          if (process.platform === 'win32') current.kill('SIGKILL');
          else process.kill(-current.pid, 'SIGKILL');
        } catch {}
      }, 15000);
    }, timeoutMs);
    const exit = await new Promise(resolve => {
      current.once('error', error => resolve({ code: null, error: String(error) }));
      current.once('close', (code, signal) => resolve({ code, signal }));
    });
    clearTimeout(timeout);
    clearTimeout(forceTimer);
    log.end();
    await once(log, 'finish');
    let detail;
    try { detail = JSON.parse(readFileSync(join(directory, 'results.json'), 'utf8')); } catch {}
    const result = { run, journey, directory, elapsed_ms: Date.now() - started, artifact_bytes: bytes(directory), timed_out: timedOut, ...exit, passed: exit.code === 0 && detail?.passed === true, detail };
    report.runs.push(result);
    report.passed &&= result.passed;
    halted = options['fail-fast'] === '1' && !result.passed;
    console.log(`${result.passed ? 'PASS' : 'FAIL'} ${run}/${runs} ${journey} ${result.elapsed_ms}ms ${directory}`);
    writeFileSync(join(output, 'campaign.json'), JSON.stringify(report, null, 2));
  }
}
report.finished_at = new Date().toISOString();
report.elapsed_ms = Date.now() - startedAt;
report.artifact_bytes = bytes(output);
report.interrupted = interrupted;
report.passed &&= !interrupted;
writeFileSync(join(output, 'campaign.json'), JSON.stringify(report, null, 2));
console.log(`Campaign ${report.passed ? 'passed' : 'has failures'}: ${output}`);
process.exitCode = report.passed ? 0 : 1;
