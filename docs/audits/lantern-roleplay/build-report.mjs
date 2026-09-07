// Package the already-captured audit as a portable, offline document.
// Run with: node docs/audits/lantern-roleplay/build-report.mjs
import {readFileSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const root = path.dirname(fileURLToPath(import.meta.url));
const escape = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const plain = value => escape(value.replaceAll('**', '').replaceAll('`', ''));
const gallery = readFileSync(path.join(root, 'README.md'), 'utf8');
const captures = [...gallery.matchAll(/## (\d+)\. ([^\n]+)\n\n([^\n]+)\n\n!\[[^\n]*\]\(([^)]+)\)/g)];
if (captures.length !== 26) throw new Error('Expected all 26 audit captures. Check the gallery format.');
const report = readFileSync(path.join(root, '../../roleplay-audit-2026-09-06.md'), 'utf8');
const cases = report.split('\n').filter(line => /^\| \d+ \|/.test(line)).map(line => line.split('|').slice(1, -1).map(cell => cell.trim()));
if (cases.length !== 22) throw new Error('Expected all 22 audit scenarios.');
const findings = [
  ['Fix first', 'Unsaved edits disappear', 'Edit a file, return to the ticket, then reopen it. The code draft is gone without a warning. Reproduced twice.', 'Preserve the draft or ask before discarding it.', 16],
  ['Fix first', 'Default ticket type fails after checkout', 'A ticket with no explicit type creates a Git branch, then fails intake. The user gets a diagnostic path, not a recovery action.', 'Validate and normalize the ticket before changing the checkout.', 25],
  ['Recovery', 'A real branch disappears from the workflow', 'After Bob’s branch creation fails to complete, the screen shows no branches. Retry says to choose the branch from that empty list.', 'Refresh local branch state on failure and offer a way to finish linking it.', 14],
  ['Visibility', 'Checks run, but their result is hard to see', 'The real test failed, then passed after the fix. The ticket shows the configured command, but not a separate pass/fail result.', 'Show the observed result independently of the generated report.', 13],
  ['Browser', 'Connect can silently do nothing', 'Repository connection depends on a native folder picker. The browser version gives no explanation when that picker is unavailable.', 'Provide a browser-supported path input or clearly explain the requirement.', 3],
];
const findingHTML = findings.map(([priority, title, evidence, recommendation, capture]) => `<article class="finding"><p class="tag">${priority}</p><h3>${title}</h3><p>${evidence}</p><p><strong>Next:</strong> ${recommendation}</p><a href="#capture-${capture}">See screenshot ${capture} →</a></article>`).join('\n');
const caseHTML = cases.map(([number, scenario, result, files]) => {
  const capture = Number(files.match(/\d+/)[0]);
  return `<tr><td>${number}</td><td>${plain(scenario)}</td><td>${plain(result)}</td><td><a href="#capture-${capture}">Evidence</a></td></tr>`;
}).join('\n');
const captureHTML = captures.map(([, number, title, note, file]) => {
  const data = readFileSync(path.join(root, file)).toString('base64');
  const caption = title.charAt(0).toUpperCase() + title.slice(1);
  return `<details class="capture" id="capture-${Number(number)}"><summary><span>${number.padStart(2, '0')}</span> ${escape(caption)}</summary><p>${escape(note)}</p><img loading="lazy" src="data:image/png;base64,${data}" alt="${escape(note)}"><a class="back" href="#findings">Back to findings ↑</a></details>`;
}).join('\n');
const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>TicketBunker · two-user audit</title>
<style>
:root{color-scheme:light;--ink:#243145;--muted:#526174;--line:#d7dee7;--paper:#fbfcfe;--blue:#235d9e}
*{box-sizing:border-box}html{scroll-padding-top:24px}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 system-ui,sans-serif}main{max-width:1140px;margin:auto;padding:48px 32px 80px}a{color:var(--blue);text-underline-offset:3px}a:hover{text-decoration-thickness:2px}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #347bc3;outline-offset:4px}h1,h2,h3{line-height:1.2}h1{font-size:clamp(32px,5vw,48px);letter-spacing:-1.4px;margin:12px 0 22px}h2{font-size:28px;margin:0 0 24px}h3{font-size:21px;margin:4px 0 12px}p{max-width:76ch;margin:10px 0}.kicker,.tag{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:700}.intro{font-size:21px;max-width:65ch}.context{color:var(--muted);font-size:14px;margin-top:20px}nav{display:flex;gap:20px;flex-wrap:wrap;margin:28px 0}section{border-top:1px solid var(--line);padding-top:30px;margin-top:40px}.finding{padding:22px 0;border-bottom:1px solid var(--line)}.finding:last-child{border:0}.finding .tag{color:#984127}.finding a{font-size:14px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}th,td{vertical-align:top;padding:12px 10px;border-bottom:1px solid var(--line)}th{color:var(--muted)}td:nth-child(2){min-width:190px}td:nth-child(3){min-width:280px}.tools{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}button{font:inherit;font-size:14px;color:var(--ink);background:var(--paper);border:1px solid var(--line);border-radius:4px;padding:9px 14px;cursor:pointer}.capture{border-top:1px solid var(--line);padding:16px 0;scroll-margin-top:20px}.capture summary{cursor:pointer;font-weight:650}.capture summary span{font-variant-numeric:tabular-nums;color:var(--muted);margin:0 12px 0 6px}.capture p{color:var(--muted);font-size:14px;margin:16px 0}.capture img{display:block;max-width:100%;height:auto;margin:20px auto;border:1px solid var(--line)}.back{font-size:14px}.limits{font-size:14px;color:var(--muted)}footer{color:var(--muted);font-size:13px;margin-top:40px}
@media(max-width:650px){main{padding:28px 18px}h1{letter-spacing:-.7px}.intro{font-size:18px}nav{gap:14px}}
@media print{@page{size:A4;margin:15mm}body{font-size:11px;background:white}main{max-width:none;padding:0}h1{font-size:30px}h2{font-size:22px}h3{font-size:17px}.intro{font-size:15px}.context,.limits{font-size:11px}nav,.tools,.back,footer{display:none}section{margin-top:24px;padding-top:20px}.finding{break-inside:avoid;padding:12px 0}.finding p{margin:6px 0}.finding a{font-size:11px}table{font-size:9px}td,th{padding:7px 5px}td:nth-child(2),td:nth-child(3){min-width:0}tr{break-inside:avoid}.capture{break-before:page;break-inside:avoid;border:0;padding:0}.capture summary{font-size:17px;list-style:none}.capture p{font-size:11px}.capture img{max-height:222mm;max-width:100%;width:auto;margin:12px auto;object-fit:contain}#captures>h2{break-after:avoid}}
</style></head><body><main>
<header><p class="kicker">TicketBunker / product exercise / 6 September 2026</p><h1>Two users. One Bunker.</h1><p class="intro">The work loop holds together. Protecting unsaved work and recovering from partial failures need attention.</p><p class="context">Alice and Bob · separate clones and databases · 30 dummy tickets · 22 scenarios · 26 screenshots</p><nav aria-label="Report sections"><a href="#findings">Priority fixes</a><a href="#scenarios">All scenarios</a><a href="#captures">Screenshots</a><a href="#limits">Test limits</a></nav></header>
<section id="findings"><h2>Fix these first</h2>${findingHTML}</section>
<section><h2>What worked</h2><p>Creating and sharing a Bunker, inviting a second user, read-only member configuration, filters and pagination, saving code, protecting dirty worktrees, detecting newer commits, and reading another user’s report.</p><p>The configured test ran and failed on the deliberate defect. After a fix, commit and resynthesis, it passed. Cached tickets remained readable during a service outage, and synchronization recovered after restart.</p></section>
<section id="scenarios"><h2>All 22 scenarios</h2><div class="table-wrap"><table><thead><tr><th scope="col">#</th><th scope="col">Scenario</th><th scope="col">Result and caveats</th><th scope="col">Capture</th></tr></thead><tbody>${caseHTML}</tbody></table></div></section>
<section id="captures"><h2>Screenshot walkthrough</h2><p>Expand a step to see the original capture. Images are embedded in this file.</p><div class="tools"><button type="button" id="expand">Expand all</button><button type="button" id="collapse">Collapse all</button><button type="button" id="print">Print / save PDF</button></div>${captureHTML}</section>
<section id="limits" class="limits"><h2>What this does not prove</h2><p>This is the captured September 6 source-build audit, not a new run or a statement that the findings remain unfixed. The real application interface, workers, database sharing and Git operations were exercised. Initial tickets were seeded through typed commands.</p><p>Agent reports used a deterministic stub. The native folder picker’s returned path was explicitly substituted after documenting its browser failure. Model reasoning, provider login, installed Windows/macOS builds, real network SSH authentication, Jira and full accessibility compliance were not tested.</p><p>Git commit and push were performed outside TicketBunker. All projects and people were dummy data. Coal was untouched. The lab services were stopped after capture. No production fixes were part of this audit.</p></section><footer>Offline report. No external fonts, scripts, trackers or network requests.</footer>
</main><script>
const captures = [...document.querySelectorAll('.capture')];
function revealHash(){const target=document.getElementById(location.hash.slice(1));if(target?.matches('.capture')){target.open=true;requestAnimationFrame(()=>target.scrollIntoView());}}
document.addEventListener('click',event=>{const link=event.target.closest('a[href^="#capture-"]');if(link){const target=document.getElementById(link.hash.slice(1));if(target)target.open=true;}});
document.getElementById('expand').onclick=()=>captures.forEach(item=>item.open=true);
document.getElementById('collapse').onclick=()=>captures.forEach(item=>item.open=false);
document.getElementById('print').onclick=()=>window.print();
let previousOpen=[];window.addEventListener('beforeprint',()=>{previousOpen=captures.map(item=>item.open);captures.forEach(item=>item.open=true);});
window.addEventListener('afterprint',()=>captures.forEach((item,index)=>item.open=previousOpen[index]));
window.addEventListener('hashchange',revealHash);revealHash();
</script></body></html>`;
writeFileSync(path.join(root, 'TicketBunker-audit.html'), html);
console.log(`Built offline report: ${cases.length} scenarios, ${captures.length} embedded screenshots.`);
