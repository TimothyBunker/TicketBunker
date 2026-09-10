// Read-only optional observer for an already-running disposable lab. The steady
// harness now samples these itself; this also supports attaching to older runs.
import { statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
const [labRoot, output, durationText = '1800000'] = process.argv.slice(2);
const duration = Number(durationText);
if (!labRoot || !output || !Number.isFinite(duration) || duration < 1000 || duration > 3600000) throw new Error('Usage: node workspace-simulation-storage-observer.mjs LAB_ROOT OUTPUT_JSON DURATION_MS');
const started = Date.now();
const samples = [];
while (Date.now() - started < duration) {
  const values = {};
  let present = false;
  for (const name of ['workspace-git.jsonl', 'workspaces.sqlite3', 'workspaces.sqlite3-wal', 'workspaces.sqlite3-shm']) {
    try { values[name] = statSync(join(labRoot, 'data', name)).size; present = true; } catch { values[name] = null; }
  }
  if (!present) break;
  const sample = { at: new Date().toISOString(), storage_bytes: values };
  samples.push(sample);
  writeFileSync(output, JSON.stringify({ started_at: new Date(started).toISOString(), samples }, null, 2));
  console.log(JSON.stringify(sample));
  await new Promise(resolve => setTimeout(resolve, 60000));
}
