import { FormEvent, MouseEvent, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import FloatingActions from "./FloatingActions";
import Markdown, { ASPECT_LABELS, ReportPlaceContext, SourceActionsContext, TRUST_LABELS, VERDICT_LABELS, verdictOf, type ReportPlace } from "./Markdown";
import { briefPart, CHAT_TOPICS, defectClaims, planSteps, provenanceLine, reportTopic, relativeTime, sourceNotes, TOPICS, verifiedStamp, type ChatTopic, type SourceNote, type Topic } from "./report";
import { command } from "./transport";
import { trackersSummary, type Bunker, type Project, type SourceDraft, type TicketDetail, type TicketReference } from "./types";

type Props = {
  bunker: Bunker;
  reference: TicketReference;
  onBack: () => void;
  notify: (message: string, error?: boolean) => void;
};

type Quote = {
  kind?: "synthesis" | "source";
  category: ChatTopic;
  scope: string;
  branch: string;
  text: string;
  path?: string;
  line_start?: number;
  line_end?: number;
  unsaved?: boolean;
};

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string" || typeof value === "number") return String(value);
  return JSON.stringify(value, null, 2);
}

function personName(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const key of ["displayName", "name", "login", "username", "email"]) {
      if (typeof record[key] === "string" && record[key]) return String(record[key]);
    }
  }
  return "";
}

function stringField(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

// One mirrored comment or activity entry, whatever provider shaped it:
// GitHub comments carry author/text/when/source, legacy comments carry
// author/body/created, history entries carry field/from/to.
function StreamEntry({ entry, fallbackKind }: { entry: Record<string, unknown>; fallbackKind: string }) {
  const author = personName(entry.author ?? entry.user ?? entry.actor);
  const when = stringField(entry, ["when", "created", "created_at", "timestamp", "time"]);
  const kind = stringField(entry, ["kind", "event", "action"]) || fallbackKind;
  const body = stringField(entry, ["text", "body", "detail", "message", "note"]);
  const source = stringField(entry, ["source", "url", "html_url"]);
  const field = stringField(entry, ["field"]);
  const change = field ? `${field}: ${text(entry.from ?? entry.previous)} → ${text(entry.to ?? entry.value)}` : "";
  return (
    <article className={`stream-entry kind-${kind.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}>
      <header>
        <strong>{author || "Unknown"}</strong>
        {kind.toLowerCase() !== fallbackKind && <span className="stream-kind">{capitalize(kind)}</span>}
        {when && <time dateTime={when} title={absoluteTime(when)}>{relativeTime(when)}</time>}
        {/^https?:/.test(source) && <a href={source} target="_blank" rel="noreferrer">Open ↗</a>}
      </header>
      {body ? <Markdown compact>{body}</Markdown> : change ? <p className="stream-change">{change}</p> : <p className="fact-empty">No text</p>}
    </article>
  );
}

function agentProject(detail: TicketDetail): string {
  return detail.ticket.source_kind === "legacy-embedded" ? "" : detail.project.key;
}

function flattenProjects(projects: Project[]): Project[] {
  return projects.flatMap((project) => [project, ...flattenProjects(project.children || [])]);
}

const SOURCE_LABELS: Record<string, string> = {
  github: "GitHub",
  gitlab: "GitLab",
  jira: "Jira",
  manual: "Entered here",
  remote: "Shared Bunker",
  "legacy-embedded": "Imported from a .tb folder",
  "legacy-companion": "Imported from a companion repository",
};

function absoluteTime(value: unknown): string {
  const raw = String(value || "");
  const timestamp = Date.parse(raw);
  if (!Number.isFinite(timestamp)) return raw || "—";
  return `${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(timestamp)} · ${relativeTime(raw)}`;
}

function capitalize(value: unknown): string {
  const raw = String(value || "");
  return raw ? raw[0].toUpperCase() + raw.slice(1) : "—";
}

function sourceLink(detail: TicketDetail): { href: string; label: string } | null {
  const reference = String(detail.ticket.source_reference || "");
  if (!/^https:\/\//.test(reference)) return null;
  const host = new URL(reference).host;
  const label = host.includes("github.com") ? "Open in GitHub" : host.includes("gitlab") ? "Open in GitLab" : host.includes("atlassian") ? "Open in Jira" : `Open on ${host}`;
  return { href: reference, label };
}

function branchState(branch: TicketDetail["branches"][number]): string {
  if (branch.current) return branch.remote ? "Checked out · on origin" : "Checked out · local only";
  if (branch.local && branch.remote) return "Local and origin";
  if (branch.local) return "Local only · not pushed";
  if (branch.remote) return "On origin · not checked out";
  return "Report only · branch not found";
}

function branchCompare(branch: TicketDetail["branches"][number]): string {
  if (branch.ahead === null || branch.ahead === undefined) return "";
  const parts = [`${branch.ahead} ahead`];
  if (branch.behind) parts.push(`${branch.behind} behind`);
  return `${parts.join(", ")} of ${branch.base}`;
}

function shortTime(value: unknown): ReactNode {
  const raw = String(value || "");
  const timestamp = Date.parse(raw);
  if (!Number.isFinite(timestamp)) return raw || <span className="fact-empty">Unknown</span>;
  return <>{new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(timestamp)}<small> · {relativeTime(raw)}</small></>;
}

function TicketTab({
  detail,
  trackers,
  openSource,
  copySource,
}: {
  detail: TicketDetail;
  trackers?: Project["trackers"];
  openSource: (path: string, line: number) => void;
  copySource: (path: string, line: number) => void;
}) {
  const ticket = detail.ticket;
  const link = sourceLink(detail);
  const known = new Set([
    "id", "key", "title", "summary", "description", "status", "state", "type", "issue_type",
    "priority", "assignee", "reporter", "author", "labels", "tags", "created", "created_at",
    "updated", "updated_at", "project", "source_kind", "source_reference", "comments", "activity",
    "acceptance_criteria", "acceptance", "notes", "note",
  ]);
  const acceptance = stringField(ticket, ["acceptance_criteria", "acceptance"]);
  const notes = stringField(ticket, ["notes", "note"]);
  const metadata = Object.entries(ticket).filter(([key, value]) => !known.has(key) && value !== "" && value !== null && value !== undefined);
  return (
    <article className="ticket-body">
      <section id="details" className="ticket-details">
        <h2>Details</h2>
        <dl className="facts-grid">
          <div className="fact"><dt>Assignee</dt><dd>{ticket.assignee ? String(ticket.assignee) : <span className="fact-empty">Unassigned</span>}</dd></div>
          <div className="fact"><dt>Reporter</dt><dd>{ticket.reporter ? String(ticket.reporter) : <span className="fact-empty">Unknown</span>}</dd></div>
          <div className="fact"><dt>Type</dt><dd>{capitalize(ticket.type)}</dd></div>
          <div className="fact"><dt>Priority</dt><dd>{ticket.priority ? capitalize(ticket.priority) : <span className="fact-empty">Not set</span>}</dd></div>
          <div className="fact"><dt>Created</dt><dd>{shortTime(ticket.created || ticket.created_at)}</dd></div>
          <div className="fact"><dt>Updated</dt><dd>{shortTime(ticket.updated || ticket.updated_at)}</dd></div>
          <div className="fact"><dt>Labels</dt><dd>{ticket.labels.length ? <span className="ticket-labels inline">{ticket.labels.map((value) => <span key={value}>{value}</span>)}</span> : <span className="fact-empty">None</span>}</dd></div>
          <div className="fact"><dt>Source</dt><dd>{SOURCE_LABELS[String(ticket.source_kind)] || text(ticket.source_kind)}{link && <> · <a href={link.href} target="_blank" rel="noreferrer">{link.label} ↗</a></>}</dd></div>
          <div className="fact"><dt>{detail.repositories.length === 1 ? "Repository" : "Repositories"}</dt><dd>{detail.repositories.length ? detail.repositories.map((value) => value.name).join(", ") : <span className="fact-empty">None attached</span>}</dd></div>
          <div className="fact"><dt>{(trackers || []).length === 1 ? "Board" : "Boards"}</dt><dd>{(trackers || []).length ? trackersSummary(trackers) : <span className="fact-empty">None linked</span>}</dd></div>
        </dl>
      </section>
      <section id="description" className="description">
        <h2>Description</h2>
        {ticket.description
          ? <Markdown onOpenSource={openSource} onCopySource={copySource}>{String(ticket.description)}</Markdown>
          : <p className="fact-empty">No description was provided.</p>}
        {acceptance && <><h2>Acceptance criteria</h2><Markdown onOpenSource={openSource} onCopySource={copySource}>{acceptance}</Markdown></>}
        {notes && <><h2>Notes</h2><Markdown onOpenSource={openSource} onCopySource={copySource}>{notes}</Markdown></>}
      </section>
      {detail.comments.length > 0 && (
        <section id="comments" className="ticket-stream"><h2>Comments <small>{detail.comments.length}</small></h2>{detail.comments.map((comment, index) => <StreamEntry key={index} entry={comment} fallbackKind="comment" />)}</section>
      )}
      {detail.activity.length > 0 && (
        <section id="activity" className="ticket-stream"><h2>Activity <small>{detail.activity.length}</small></h2>{detail.activity.map((event, index) => <StreamEntry key={index} entry={event} fallbackKind="update" />)}</section>
      )}
      {metadata.length > 0 && (
        <details className="metadata"><summary>Metadata</summary><dl>{metadata.map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{text(value)}</dd></div>)}</dl></details>
      )}
    </article>
  );
}

function SynthesisCard({
  topic,
  report,
  scope,
  onQuote,
  openSource,
  copySource,
}: {
  topic: Topic;
  report: { scope: string; content: string };
  scope: string;
  onQuote: (quote: Quote) => void;
  openSource: (path: string, line: number) => void;
  copySource: (path: string, line: number) => void;
}) {
  const value = reportTopic(report.content, topic);
  const synthesisRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLElement>(null);
  const [menu, setMenu] = useState<{ top: number; left: number; text: string } | null>(null);
  const [trailOpen, setTrailOpen] = useState(false);
  // Selecting text offers actions beside the selection; it never opens the
  // chat by itself, so copying a phrase stays a plain copy.
  function selected(event: MouseEvent) {
    // A press on the toolbar itself must not tear the toolbar down before
    // its click lands.
    if ((event.target as HTMLElement).closest?.(".floating-actions")) return;
    const selection = window.getSelection();
    const selectedText = selection?.toString().trim() || "";
    if (!selection || !selectedText || selection.rangeCount === 0 || !synthesisRef.current?.contains(event.target as Node)) {
      setMenu(null);
      return;
    }
    const rect = selection.getRangeAt(0).getBoundingClientRect();
    const card = cardRef.current?.getBoundingClientRect();
    if (!card) return;
    setMenu({ top: rect.top - card.top, left: Math.max(60, rect.left - card.left + rect.width / 2), text: selectedText.slice(0, 4000) });
  }
  function quote(selectedText: string) {
    onQuote({
      kind: "synthesis",
      category: topic,
      scope,
      branch: report.scope.startsWith("branch:") ? report.scope.slice(7) : "",
      text: selectedText,
    });
  }
  const place = useMemo<ReportPlace>(() => ({ category: topic, scope, branch: report.scope.startsWith("branch:") ? report.scope.slice(7) : "" }), [topic, scope, report.scope]);
  return (
    <ReportPlaceContext.Provider value={place}>
    <section ref={cardRef} className="synthesis-card" onMouseUp={selected}>
      {menu && (
        <FloatingActions
          label="Selection actions"
          style={{ top: menu.top, left: menu.left }}
          anchor={synthesisRef}
          onClose={() => setMenu(null)}
          actions={[
            { label: "Send to chat", onSelect: () => quote(menu.text) },
            { label: "Copy", onSelect: () => { navigator.clipboard?.writeText(menu.text).catch(() => undefined); } },
          ]}
        />
      )}
      <header><h3>{topic[0].toUpperCase() + topic.slice(1)}</h3></header>
      <div className="synthesis-body">
        <div ref={synthesisRef} className="synthesis-copy">
          {value.synthesis
            ? <Markdown compact onOpenSource={openSource} onCopySource={copySource}>{value.synthesis}</Markdown>
            : value.trail
              ? <><p className="missing-copy">No synthesis brief; this is the evidence trail.</p><Markdown compact onOpenSource={openSource} onCopySource={copySource}>{value.trail}</Markdown></>
              : <p className="missing-copy">Nothing was published for {topic}.</p>}
        </div>
        {value.synthesis && trailOpen && <div className="synthesis-trail">{value.trail ? <Markdown compact onOpenSource={openSource} onCopySource={copySource}>{value.trail}</Markdown> : <p>No evidence trail was published.</p>}</div>}
      </div>
      {value.synthesis && <button type="button" className="trail-toggle" aria-expanded={trailOpen} onClick={() => setTrailOpen((open) => !open)}>{trailOpen ? "Hide trail" : "Full trail"}</button>}
    </section>
    </ReportPlaceContext.Provider>
  );
}

// The reader's first answers, above the four evidence questions: the verdict
// and its reason, the first thing to do, whom to ask, the plan to finish,
// and the ticket's own items judged against the code. A section with light
// highlighting, not a panel: the verdict pill is the only strong colour.
function Standing({ report, scope }: { report: TicketDetail["reports"][number]; scope: string }) {
  const standing = briefPart(report.content, "Standing");
  const checklist = briefPart(report.content, "Checklist");
  const plan = planSteps(report.content);
  const defects = defectClaims(report.content);
  if (!standing && !checklist && plan.length === 0 && defects.length === 0) return null;
  const claims = standing.split("\n").filter((line) => /^\s*[-*]\s/.test(line));
  const verdictLine = claims.find((line) => verdictOf(line.replace(/^\s*[-*]\s*/, "")));
  const verdict = verdictLine ? verdictOf(verdictLine.replace(/^\s*[-*]\s*/, "")) : null;
  const others = claims.filter((line) => line !== verdictLine).join("\n");
  const place = useMemo<ReportPlace>(() => ({ category: "standing", scope, branch: report.scope.startsWith("branch:") ? report.scope.slice(7) : "" }), [scope, report.scope]);
  const verdictText = verdictLine && verdict ? verdictLine.replace(/^\s*[-*]\s*/, "").replace(/Verdict:\s*(not started|in progress|blocked|looks done)\s*(?:[—–:-]\s*)?/i, "") : "";
  return (
    <ReportPlaceContext.Provider value={place}>
      <section className="standing" aria-label="Standing">
        <h3 className="standing-eyebrow">Standing</h3>
        {verdict && (
          <div className={`verdict-row verdict-${verdict.verdict.replace(/\s+/g, "-")}`}>
            <span className="verdict-pill">{VERDICT_LABELS[verdict.verdict] || verdict.verdict}</span>
            <Markdown compact>{`- ${verdictText}`}</Markdown>
          </div>
        )}
        {others && <div className="standing-lines"><Markdown compact>{others}</Markdown></div>}
        {defects.length > 0 && (
          <div className="defects">
            <h3 className="standing-eyebrow">{defects.length === 1 ? "Defect" : `Defects · ${defects.length}`}</h3>
            <Markdown compact>{defects.map((claim) => `- ${claim}`).join("\n")}</Markdown>
          </div>
        )}
        {plan.length > 0 && (
          <div className="plan">
            <h3 className="standing-eyebrow">{plan.derived ? "Plan · from the findings" : "Plan"}</h3>
            <ol>{plan.map((step, index) => <li key={index}><Markdown compact>{`- ${step}`}</Markdown></li>)}</ol>
          </div>
        )}
        {checklist && (
          <div className="checklist">
            <h3 className="standing-eyebrow">Checklist</h3>
            <Markdown compact>{checklist}</Markdown>
          </div>
        )}
      </section>
    </ReportPlaceContext.Provider>
  );
}

type UpdateDraft = { text: string; branch: string; target: { label: string; url: string } | null; can_post: boolean };

// The menial follow-through: the Standing block as a tracker comment the
// person edits, copies, or posts. Posting is a deliberate second click.
function UpdatePanel({ bunkerId, detail, scope, notify, onClose }: { bunkerId: string; detail: TicketDetail; scope: string; notify: (message: string, error?: boolean) => void; onClose: () => void }) {
  const [draft, setDraft] = useState<UpdateDraft | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [posted, setPosted] = useState("");
  useEffect(() => {
    let active = true;
    setError("");
    command<UpdateDraft>("application.ticket.update.draft", { bunker_id: bunkerId, ticket_id: detail.ticket.id, scope: scope === "all" ? "" : scope })
      .then((value) => { if (!active) return; setDraft(value); setText(value.text); })
      .catch((reason) => active && setError(String(reason)));
    return () => { active = false; };
  }, [bunkerId, detail.ticket.id, scope]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      notify("Update copied");
    } catch {
      notify("Select the text and copy it; the clipboard was not available.", true);
    }
  }
  async function post() {
    if (!confirming) return setConfirming(true);
    setBusy(true);
    setError("");
    try {
      const value = await command<{ url: string }>("application.ticket.update.post", { bunker_id: bunkerId, ticket_id: detail.ticket.id, text });
      setPosted(value.url || "posted");
      notify("Update posted");
    } catch (reason) {
      setError(String(reason));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="update-panel" aria-label="Draft update">
      <header><div><h3>Update for the tracker</h3><p>{draft?.target ? `Drafted from ${draft.branch}; posts to ${draft.target.label}.` : draft ? `Drafted from ${draft.branch}. This ticket has no GitHub issue to post to, so copy it.` : "Drafting from the Standing block."}</p></div><button className="icon-control" aria-label="Close update" onClick={onClose}>×</button></header>
      {error && <p className="inline-error">{error}</p>}
      {draft && <textarea aria-label="Update text" value={text} rows={Math.min(14, Math.max(5, text.split("\n").length + 1))} onChange={(event) => { setText(event.target.value); setConfirming(false); }} />}
      {draft && <div className="update-actions">
        <button onClick={copy} disabled={!text}>Copy</button>
        {draft.can_post && !posted && <button className={confirming ? "danger-control" : "primary"} disabled={busy || !text} onClick={post}>{confirming ? `Confirm post to ${draft.target?.label}` : "Post to GitHub"}</button>}
        {confirming && !posted && <button onClick={() => setConfirming(false)}>Cancel</button>}
        {posted && (/^https?:/.test(posted) ? <a href={posted} target="_blank" rel="noreferrer">Posted · open comment ↗</a> : <span>Posted</span>)}
      </div>}
    </section>
  );
}

function AgentTab({
  detail,
  bunkerId,
  scope,
  onScope,
  onQuote,
  openSource,
  copySource,
  notify,
}: {
  detail: TicketDetail;
  bunkerId: string;
  scope: string;
  onScope: (value: string) => void;
  onQuote: (quote: Quote) => void;
  openSource: (path: string, line: number) => void;
  copySource: (path: string, line: number) => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [drafting, setDrafting] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  function toggleTag(key: string) {
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }
  const branchReports = detail.reports.filter((value) => value.scope.startsWith("branch:"));
  const legacyMaster = detail.reports.filter((value) => value.scope === "combined");
  const reports = scope === "all"
    ? (branchReports.length ? branchReports : legacyMaster)
    : detail.reports.filter((value) => value.scope === `branch:${scope}`);
  const selectable = branchReports.length > 0;
  return (
    <article className={["agent-work", ...[...hidden].map((key) => `hide-${key}`)].join(" ")}>
      <div className="agent-toolbar">
        <div><h2>Agent work</h2></div>
        <div className="agent-toolbar-actions">
          {branchReports.length > 0 && <button onClick={() => setDrafting((value) => !value)} aria-expanded={drafting}>{drafting ? "Hide update" : "Draft update"}</button>}
          {selectable && <label><span className="sr-only">Report scope</span><select value={scope} onChange={(event) => onScope(event.target.value)}>{branchReports.map((report) => <option key={report.scope} value={report.scope.slice(7)}>{report.scope.slice(7)}</option>)}{branchReports.length > 1 && <option value="all">All branches</option>}</select></label>}
        </div>
      </div>
      {drafting && <UpdatePanel bunkerId={bunkerId} detail={detail} scope={scope} notify={notify} onClose={() => setDrafting(false)} />}
      {reports.length > 0 && <div className="claim-filter" role="group" aria-label="Show claims">
        {["observation", "real", "speculation:grounded", "speculation:adjacent", "speculation:ungrounded"].map((key) => {
          const tone = key.startsWith("speculation") ? key.split(":")[1] : key;
          return <button key={key} type="button" className={`trust-chip ${tone}${hidden.has(tone) ? " off" : ""}`} aria-pressed={!hidden.has(tone)} title={TRUST_LABELS[key].title} onClick={() => toggleTag(tone)}>{TRUST_LABELS[key].label}</button>;
        })}
        <span className="filter-gap" aria-hidden="true" />
        {["defect", "risk", "next"].map((key) => <button key={key} type="button" className={`aspect-chip ${key}${hidden.has(key) ? " off" : ""}`} aria-pressed={!hidden.has(key)} title={ASPECT_LABELS[key].title} onClick={() => toggleTag(key)}>{ASPECT_LABELS[key].label}</button>)}
        {hidden.size > 0 && <button type="button" className="filter-reset" onClick={() => setHidden(new Set())}>Show all</button>}
      </div>}
      {reports.length === 0 && scope !== "all" && detail.reports.length > 0 && <div className="quiet-empty"><h2>No report for {scope} yet</h2><p>Check it out and press Synthesize, or pick a branch that has a report.</p></div>}
      {detail.reports.length === 0 && <div className="quiet-empty"><h2>No synthesis yet</h2><p>Check out a ticket branch and press Synthesize when you want a report.</p></div>}
      {reports.map((report) => (
        <section className="branch-report" key={report.scope}>
          {scope === "all" && <div className="branch-divider"><span>{report.scope === "combined" ? "Combined report" : report.scope.slice(7)}</span></div>}
          {report.scope !== "combined" && <p className="report-provenance">{provenanceLine(verifiedStamp(report.content), report.updated_at) || "Provenance unknown"}</p>}
          <Standing report={report} scope={scope} />
          <div className="synthesis-grid">
            {TOPICS.map((topic) => <SynthesisCard key={topic} topic={topic} report={report} scope={scope} onQuote={onQuote} openSource={openSource} copySource={copySource} />)}
          </div>
        </section>
      ))}
    </article>
  );
}

function Elapsed({ since }: { since: string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const start = Date.parse(since);
  if (!Number.isFinite(start)) return null;
  const seconds = Math.max(0, Math.round((now - start) / 1000));
  const label = seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return <small className="elapsed"> · {label}</small>;
}

function TicketRail({
  detail,
  bunkerId,
  repoRoot,
  scope,
  onScope,
  onReload,
  notify,
}: {
  detail: TicketDetail;
  bunkerId: string;
  repoRoot: string;
  scope: string;
  onScope: (value: string) => void;
  onReload: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [candidate, setCandidate] = useState<{ name: string; base: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const synthesis = (detail.truth?.synthesis || {}) as Record<string, unknown>;
  const ticket = detail.ticket;
  async function synthesize() {
    if (!repoRoot) return notify("Attach a local repository before synthesizing.", true);
    setBusy(true);
    try {
      await command("truth.synthesize", { project: agentProject(detail), ticket: ticket.id, bunker_id: bunkerId }, repoRoot);
      notify("Synthesis started");
      onReload();
    } catch (reason) {
      notify(String(reason), true);
    } finally {
      setBusy(false);
    }
  }
  async function createBranch() {
    if (!candidate || !repoRoot) return;
    setBusy(true);
    try {
      const result = await command<{ branch: string }>("workflow.start", {
        ticket: ticket.id,
        name: candidate.name,
        base: candidate.base,
      }, repoRoot);
      notify(`Created ${result.branch}`);
      setCandidate(null);
      setCreating(false);
      onReload();
    } catch (reason) {
      notify(String(reason), true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <aside className="ticket-rail">
      <section className="state-hub">
        <header><h2>Synthesis</h2></header>
        <div className="state-line"><i className={`state-dot ${synthesis.status === "current" ? "ready" : synthesis.status === "updating" ? "loading" : ""}`} /><span><strong>{String(synthesis.compact || synthesis.label || "Not synthesized")}{synthesis.status === "updating" && <Elapsed since={String((synthesis.progress as Record<string, unknown> | undefined)?.started_at || "")} />}</strong><small>{String(synthesis.detail || "Use Synthesize from a checked-out ticket branch.")}</small></span></div>
        {Boolean(synthesis.log) && <details className="diagnostic-path"><summary>Diagnostic location</summary><code>{String(synthesis.log)}</code><button onClick={() => navigator.clipboard.writeText(String(synthesis.log)).then(() => notify("Diagnostic path copied")).catch(() => notify("Copy was unavailable; the full path is shown above.", true))}>Copy path</button></details>}
        {repoRoot && synthesis.status !== "updating" && <button className="wide-action" disabled={busy} onClick={synthesize}>{synthesis.status === "current" ? "Synthesize again" : String(synthesis.action || "Synthesize")}</button>}
      </section>
      <section className="branches-panel">
        <header><h2>Branches</h2><button className="mini-plus" aria-label="Create branch" onClick={() => setCreating(!creating)}>+</button></header>
        {detail.branches.length === 0 && <p className="rail-empty">No branch carries {detail.ticket.id} yet. Use + to start one.</p>}
        {detail.branches.map((branch) => {
          const compare = branchCompare(branch);
          const when = branch.last_commit_at ? `${branch.last_author ? `${branch.last_author} · ` : ""}${relativeTime(branch.last_commit_at)}` : "";
          const hasReport = detail.reports.some((report) => report.scope === `branch:${branch.name}`);
          return (
            <button key={branch.name} className={scope === branch.name ? "branch-item selected" : "branch-item"} onClick={() => onScope(branch.name)} title={branch.upstream ? `Tracks ${branch.upstream}` : undefined}>
              <span className="branch-name">{branch.current && <i className="state-dot ready" />}{branch.name}</span>
              <small>{[branchState(branch), compare, hasReport ? "report" : "no report"].filter(Boolean).join(" · ")}</small>
              {when && <small>{when}</small>}
            </button>
          );
        })}
        {creating && !candidate && <form className="branch-form" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); setCandidate({ name: String(data.get("name") || ""), base: String(data.get("base") || "") }); }}><label>Short description<input name="name" placeholder="position-system" required /></label><label>Branch from<select name="base">{detail.repositories.map((repo) => <option key={repo.id} value={repo.default_branch || "main"}>{repo.default_branch || "main"}</option>)}</select></label><button className="primary">Review branch</button></form>}
        {candidate && <div className="confirmation"><strong>Create this branch?</strong><p>TicketBunker will require a clean worktree, switch to <code>{candidate.base}</code>, fast-forward it when possible, then create the ticket branch.</p><div><button onClick={() => setCandidate(null)}>Back</button><button className="primary" disabled={busy} onClick={createBranch}>Create branch</button></div></div>}
      </section>
    </aside>
  );
}

function CoworkerDrawer({
  open,
  onOpen,
  detail,
  bunkerId,
  repoRoot,
  scope,
  quote,
  onQuote,
  onReload,
  notify,
}: {
  open: boolean;
  onOpen: (value: boolean) => void;
  detail: TicketDetail;
  bunkerId: string;
  repoRoot: string;
  scope: string;
  quote: Quote | null;
  onQuote: (value: Quote | null) => void;
  onReload: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [topic, setTopic] = useState<ChatTopic>(quote?.category || "standing");
  const [coworker, setCoworker] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (quote) {
      setTopic(quote.category);
      onOpen(true);
    }
  }, [quote, onOpen]);
  useEffect(() => {
    if (!open || !repoRoot) return;
    command<Record<string, unknown>>("coworker.state", { project: agentProject(detail), ticket: detail.ticket.id, bunker_id: bunkerId, category: topic, scope: scope === "all" ? "combined" : scope }, repoRoot)
      .then(setCoworker)
      .catch((reason) => setError(String(reason)));
  }, [open, repoRoot, detail.project.key, detail.ticket.id, topic, scope]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const message = String(new FormData(form).get("message") || "");
    if (!message.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await command<Record<string, unknown>>("coworker.chat", { project: agentProject(detail), ticket: detail.ticket.id, bunker_id: bunkerId, category: topic, scope: scope === "all" ? "combined" : scope, message, reference: quote }, repoRoot);
      setCoworker(result);
      onQuote(null);
      form.reset();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function proposal(action: "accept" | "dismiss", id: string) {
    setBusy(true);
    try {
      await command(`coworker.proposal.${action}`, { project: agentProject(detail), ticket: detail.ticket.id, bunker_id: bunkerId, proposal: id }, repoRoot);
      notify(action === "accept" ? "Report update accepted" : "Proposal dismissed");
      onReload();
      const refreshed = await command<Record<string, unknown>>("coworker.state", { project: agentProject(detail), ticket: detail.ticket.id, bunker_id: bunkerId, category: topic, scope: scope === "all" ? "combined" : scope }, repoRoot);
      setCoworker(refreshed);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  if (!repoRoot) return null;
  if (!open) return (
    <button className="coworker-bubble" aria-label="Ask ticket coworker" title="Ask the ticket coworker" onClick={() => onOpen(true)}>
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.4 3.6c-.6.5-1.6.1-1.6-.8V5.5Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /><circle cx="9" cy="9.5" r="1.1" fill="currentColor" /><circle cx="12.5" cy="9.5" r="1.1" fill="currentColor" /><circle cx="16" cy="9.5" r="1.1" fill="currentColor" /></svg>
    </button>
  );
  const messages = (coworker?.messages || []) as Array<Record<string, unknown>>;
  const proposals = (coworker?.proposals || []) as Array<Record<string, unknown>>;
  return (
    <aside className="coworker-drawer">
      <header><div><strong>{String(coworker?.title || `${detail.ticket.id} coworker`)}</strong><small>{scope === "all" ? "All branches" : scope}</small></div><button aria-label="Collapse coworker" onClick={() => onOpen(false)}>→</button></header>
      <nav>{CHAT_TOPICS.map((value) => <button key={value} className={topic === value ? "selected" : ""} onClick={() => { setTopic(value); onQuote(null); }}>{value[0].toUpperCase() + value.slice(1)}</button>)}</nav>
      <div className="chat-messages">{messages.length === 0 && <p className="chat-empty">Ask for context, challenge an assumption, or request a report correction.</p>}{messages.map((message, index) => <article key={index} className={String(message.role) === "user" ? "user-message" : "agent-message"}>{Boolean(message.reference) && <small>Quoted context</small>}<Markdown compact>{String(message.text || "")}</Markdown></article>)}{proposals.filter((item) => item.status === "pending").map((item) => <article className="proposal" key={String(item.id)}><strong>Proposed report update</strong><p>Reviewing this accepts changes only through TicketBunker’s report gates.</p><div><button disabled={busy} onClick={() => proposal("dismiss", String(item.id))}>Dismiss</button><button className="primary" disabled={busy} onClick={() => proposal("accept", String(item.id))}>Accept</button></div></article>)}</div>
      <form className="chat-input" onSubmit={send}>{quote && <div className="quote-chip"><span>{quote.text}</span><button type="button" onClick={() => onQuote(null)}>×</button></div>}<textarea name="message" rows={2} placeholder={`Ask about ${topic}`} required /><button className="primary" disabled={busy}>Send</button>{error && <p className="inline-error">{error}</p>}</form>
    </aside>
  );
}

// The editor is a textarea over a mirror of the same text, one element per
// line, so lines can be highlighted and the report's claims about this file
// can sit in a margin beside the lines they cite.
function SourceEditor({
  draft,
  projectRoot,
  notes,
  onBack,
  onDraft,
  onQuote,
  notify,
}: {
  draft: SourceDraft;
  projectRoot: string;
  notes: SourceNote[];
  onBack: () => void;
  onDraft: (draft: SourceDraft) => void;
  onQuote: (quote: Quote) => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [content, setContent] = useState(draft.content);
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState<number>(0);
  const [active, setActive] = useState<number | null>(null);
  const [noteTops, setNoteTops] = useState<number[]>([]);
  const editor = useRef<HTMLTextAreaElement>(null);
  const mirror = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const noteRefs = useRef<Array<HTMLElement | null>>([]);
  const lines = useMemo(() => content.split("\n"), [content]);
  const notedLines = useMemo(() => {
    const map = new Map<number, string>();
    for (const note of notes) for (let line = note.line; line <= note.endLine; line += 1) if (!map.has(line) || note.tone === "defect") map.set(line, note.tone);
    return map;
  }, [notes]);

  function lineTop(line: number): number {
    const element = mirror.current?.children[line - 1] as HTMLElement | undefined;
    return element ? element.offsetTop : 0;
  }
  function goTo(line: number, flash = true) {
    const top = lineTop(line);
    scroller.current?.scrollTo({ top: Math.max(0, top - 140), behavior: "smooth" });
    if (flash) {
      setTarget(line);
      window.setTimeout(() => setTarget((value) => (value === line ? 0 : value)), 1800);
    }
    const input = editor.current;
    if (input) {
      const offset = lines.slice(0, Math.max(0, line - 1)).join("\n").length + (line > 1 ? 1 : 0);
      input.setSelectionRange(offset, offset);
    }
  }

  useEffect(() => {
    setContent(draft.content);
    // Wait one frame so the mirror has laid out the new text.
    requestAnimationFrame(() => requestAnimationFrame(() => { editor.current?.focus({ preventScroll: true }); goTo(draft.line); }));
  }, [draft]);

  // Notes sit beside their lines; when two would overlap, the later one
  // slides down. Measured after render, so long notes push their neighbours.
  useLayoutEffect(() => {
    const tops: number[] = [];
    let floor = 0;
    notes.forEach((note, index) => {
      const wanted = lineTop(note.line);
      const top = Math.max(wanted, floor);
      tops.push(top);
      floor = top + (noteRefs.current[index]?.offsetHeight || 0) + 6;
    });
    setNoteTops(tops);
  }, [notes, lines, draft.settings.line_wrap]);

  async function save() {
    setBusy(true);
    try {
      const result = await command<{ content: string; digest: string; saved: boolean }>("editor.save", { path: draft.path, content, expected_digest: draft.digest }, projectRoot);
      onDraft({ ...draft, content: result.content, digest: result.digest });
      setContent(result.content);
      notify(result.saved ? `Saved ${draft.path}` : `${draft.path} is unchanged`);
    } catch (reason) {
      notify(String(reason), true);
    } finally {
      setBusy(false);
    }
  }
  function quoteSelection() {
    const input = editor.current;
    if (!input || input.selectionStart === input.selectionEnd) return notify("Select source text first.", true);
    const before = content.slice(0, input.selectionStart);
    const selected = content.slice(input.selectionStart, input.selectionEnd);
    const start = before.split("\n").length;
    const end = start + selected.split("\n").length - 1;
    onQuote({ kind: "source", category: "what", scope: draft.branch || "all", branch: draft.branch, path: draft.path, line_start: start, line_end: end, unsaved: content !== draft.content, text: selected.slice(0, 4000) });
  }
  function askAboutNote(note: SourceNote) {
    const text = lines.slice(note.line - 1, note.endLine).join("\n");
    onQuote({ kind: "source", category: note.tone === "defect" || note.section === "What" ? "what" : "standing", scope: draft.branch || "all", branch: draft.branch, path: draft.path, line_start: note.line, line_end: note.endLine, unsaved: content !== draft.content, text: text.slice(0, 4000) || note.text });
  }
  const wrap = Boolean(draft.settings.line_wrap);
  return (
    <section className={notes.length ? "source-editor annotated" : "source-editor"}>
      <header><button className="back-control" onClick={onBack}>← Ticket</button><div><strong>{draft.path}</strong><small>{draft.branch || "Detached"}{notes.length > 0 && ` · ${notes.length} note${notes.length === 1 ? "" : "s"} from the report`}</small></div><div><button onClick={quoteSelection}>Ask about selection</button><button className="primary" disabled={busy || content === draft.content} onClick={save}>Save</button></div></header>
      <div ref={scroller} className="editor-scroll">
        <div className={wrap ? "editor-surface wrap" : "editor-surface"} style={{ tabSize: draft.settings.tab_size || 4 }}>
          <div className="editor-gutter" aria-hidden="true">{lines.map((_, index) => <div key={index} className={notedLines.has(index + 1) ? `gutter-line noted ${notedLines.get(index + 1)}` : "gutter-line"} style={{ height: lineTop(index + 2) - lineTop(index + 1) || undefined }}>{index + 1}</div>)}</div>
          <div className="editor-stack">
            <div ref={mirror} className="editor-mirror" aria-hidden="true">
              {lines.map((line, index) => {
                const number = index + 1;
                const classes = ["mirror-line"];
                if (notedLines.has(number)) classes.push("noted", notedLines.get(number) || "");
                if (target === number) classes.push("target");
                if (active !== null && notes[active] && number >= notes[active].line && number <= notes[active].endLine) classes.push("active");
                return <div key={index} className={classes.join(" ")}>{line || " "}</div>;
              })}
            </div>
            <textarea ref={editor} value={content} onChange={(event) => setContent(event.target.value)} spellCheck={false} wrap={wrap ? "soft" : "off"} rows={lines.length} />
          </div>
          {notes.length > 0 && (
            <aside className="editor-notes" aria-label="Report notes for this file">
              {notes.map((note, index) => (
                <article
                  key={`${note.line}-${index}`}
                  ref={(element) => { noteRefs.current[index] = element; }}
                  className={`editor-note ${note.tone}${active === index ? " active" : ""}`}
                  style={{ top: noteTops[index] ?? lineTop(note.line) }}
                  onMouseEnter={() => setActive(index)}
                  onMouseLeave={() => setActive((value) => (value === index ? null : value))}
                >
                  <header><span className={`note-label ${note.tone}`}>{note.label}</span><button type="button" onClick={() => goTo(note.line)}>{note.endLine > note.line ? `Lines ${note.line}–${note.endLine}` : `Line ${note.line}`}</button></header>
                  <Markdown compact>{note.text}</Markdown>
                  <footer><button type="button" onClick={() => askAboutNote(note)}>Ask about this</button></footer>
                </article>
              ))}
            </aside>
          )}
        </div>
      </div>
    </section>
  );
}

export default function TicketWorkspace({ bunker, reference, onBack, notify }: Props) {
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [error, setError] = useState("");
  const [scope, setScope] = useState("");
  const [section, setSection] = useState("details");
  const [chatOpen, setChatOpen] = useState(false);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [editor, setEditor] = useState<SourceDraft | null>(null);
  const repoRoot = useMemo(() => detail?.repositories.find((value) => value.local_path)?.local_path || "", [detail]);

  function load() {
    setError("");
    command<TicketDetail>("application.ticket.get", { bunker_id: bunker.id, ticket_id: reference.id })
      .then(setDetail)
      .catch((reason) => setError(String(reason)));
  }
  useEffect(load, [bunker.id, reference.id]);
  // While a synthesis runs, the page follows it: poll the ticket every few
  // seconds and take one more read when it finishes, so the report appears
  // without a manual refresh. A hidden tab stays quiet; returning to it
  // reads once immediately.
  const synthesisStatus = String((detail?.truth?.synthesis as Record<string, unknown> | undefined)?.status || "");
  const wasUpdating = useRef(false);
  useEffect(() => {
    const updating = synthesisStatus === "updating";
    if (!updating && wasUpdating.current) load();
    wasUpdating.current = updating;
    if (!updating) return;
    const tick = () => { if (!document.hidden) load(); };
    const timer = window.setInterval(tick, 3000);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [synthesisStatus, bunker.id, reference.id]);
  useEffect(() => {
    if (!detail || editor) return;
    const targets = ["details", "description", "agent-work"].map((id) => document.getElementById(id)).filter((value): value is HTMLElement => Boolean(value));
    if (targets.length === 0 || typeof IntersectionObserver === "undefined") return;
    const visible = new Map<string, number>();
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) visible.set(entry.target.id, entry.isIntersecting ? entry.intersectionRatio : 0);
      const ordered = targets.map((target) => target.id);
      const best = ordered.filter((id) => (visible.get(id) || 0) > 0)[0];
      if (best) setSection(best);
    }, { rootMargin: "-140px 0px -55% 0px", threshold: [0, 0.1, 0.5] });
    targets.forEach((target) => observer.observe(target));
    return () => observer.disconnect();
  }, [detail, editor]);

  function jumpTo(id: string) {
    setEditor(null);
    window.requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  useEffect(() => {
    if (!detail) return;
    const withReports = detail.reports.filter((value) => value.scope.startsWith("branch:")).map((value) => value.scope.slice(7));
    const valid = scope === "all" ? withReports.length > 1 : scope !== "" && (withReports.includes(scope) || detail.branches.some((branch) => branch.name === scope));
    if (valid) return;
    const current = detail.branches.find((branch) => branch.current)?.name;
    setScope(current && withReports.includes(current) ? current : withReports[0] || current || detail.branches[0]?.name || "");
  }, [detail]);

  async function openSource(path: string, line: number) {
    if (!repoRoot) return notify("Attach this ticket’s local repository before opening source.", true);
    try {
      const value = await command<SourceDraft>("editor.read", { path, line }, repoRoot);
      setEditor(value);
    } catch (reason) {
      notify(String(reason), true);
    }
  }
  async function copySource(path: string, line: number) {
    if (!repoRoot) return;
    try {
      const value = await command<{ reference: string }>("editor.resolve", { path, line }, repoRoot);
      await navigator.clipboard.writeText(value.reference);
      notify("Absolute path copied");
    } catch (reason) {
      notify(String(reason), true);
    }
  }
  function quoteReport(value: Quote) {
    setQuote(value);
    setChatOpen(true);
  }
  function quoteSource(value: Quote) {
    setQuote({ ...value, scope, category: "what" });
    setChatOpen(true);
  }
  function askSource(path: string, line: number, place: ReportPlace | null) {
    const category = (place?.category || "what") as ChatTopic;
    setQuote({ kind: "source", category, scope: place?.scope || (scope === "all" ? "combined" : scope), branch: place?.branch || "", text: `${path}:${line}`, path, line_start: line, line_end: line });
    setChatOpen(true);
  }
  const sourceActions = useMemo(() => ({ open: openSource, copy: copySource, ask: askSource }), [repoRoot, scope]);

  const editorNotes = useMemo<SourceNote[]>(() => {
    if (!editor || !detail) return [];
    const branchReports = detail.reports.filter((value) => value.scope.startsWith("branch:"));
    const chosen = scope !== "all" ? branchReports.filter((value) => value.scope === `branch:${scope}`) : branchReports;
    const source = chosen.length ? chosen : branchReports;
    return source.flatMap((report) => sourceNotes(report.content, editor.path));
  }, [editor?.path, detail, scope]);
  if (error) return <div className="load-error"><button onClick={onBack}>← Tickets</button><h1>Ticket could not open</h1><p>{error}</p><button onClick={load}>Retry</button></div>;
  if (!detail) return <div className="ticket-loading full">Opening {reference.id}</div>;
  const ticket = detail.ticket;
  const link = sourceLink(detail);
  const trackers = flattenProjects(bunker.projects).find((value) => value.id === detail.project.id)?.trackers;
  return (
    <div className="ticket-shell">
      <header className="ticket-header">
        <button className="back-control" onClick={onBack}>← Tickets</button>
        <div className="ticket-heading"><p>{detail.project.name}</p><h1><span>{ticket.id}</span> {ticket.title}</h1><div className="ticket-chips"><span className="status-chip">{ticket.status || "Open"}</span>{ticket.labels.map((value) => <span key={value} className="label-chip">{value}</span>)}<small>{ticket.assignee ? `${ticket.assignee} · ` : ""}Updated {relativeTime(String(ticket.updated || ""))}</small>{link && <a className="source-link" href={link.href} target="_blank" rel="noreferrer">{link.label} ↗</a>}</div></div>
        {!editor && <nav className="section-jumps" aria-label="Sections">
          {[["details", "Details"], ["description", "Description"], ["agent-work", "Agent work"]].map(([id, label]) => <button key={id} className={section === id ? "selected" : ""} aria-current={section === id ? "true" : undefined} onClick={() => jumpTo(id)}>{label}</button>)}
        </nav>}
      </header>
      <div className="ticket-layout">
        <main className="ticket-main">
          {editor ? <SourceEditor draft={editor} projectRoot={repoRoot} notes={editorNotes} onBack={() => setEditor(null)} onDraft={setEditor} onQuote={quoteSource} notify={notify} /> : <SourceActionsContext.Provider value={sourceActions}>
            <TicketTab detail={detail} trackers={trackers} openSource={openSource} copySource={copySource} />
            <div id="agent-work"><AgentTab detail={detail} bunkerId={bunker.id} scope={scope} onScope={setScope} onQuote={quoteReport} openSource={openSource} copySource={copySource} notify={notify} /></div>
          </SourceActionsContext.Provider>}
        </main>
        <TicketRail detail={detail} bunkerId={bunker.id} repoRoot={repoRoot} scope={scope} onScope={(value) => { setScope(value); setEditor(null); window.requestAnimationFrame(() => document.getElementById("agent-work")?.scrollIntoView({ behavior: "smooth", block: "start" })); }} onReload={load} notify={notify} />
      </div>
      <CoworkerDrawer open={chatOpen} onOpen={setChatOpen} detail={detail} bunkerId={bunker.id} repoRoot={repoRoot} scope={scope} quote={quote} onQuote={setQuote} onReload={load} notify={notify} />
    </div>
  );
}
