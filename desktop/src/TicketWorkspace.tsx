import { FormEvent, MouseEvent, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import FloatingActions from "./FloatingActions";
import BackButton from "./BackButton";
import IconButton from "./IconButton";
import { UnsavedChangesBoundary, useGuardedAction, useUnsavedEditor } from "./UnsavedChanges";
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
  category: ChatTopic | "file";
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
  return detail.project.key;
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
  const known = new Set([
    "id", "key", "title", "summary", "description", "status", "state", "type", "issue_type",
    "priority", "assignee", "reporter", "author", "labels", "tags", "created", "created_at",
    "updated", "updated_at", "project", "source_kind", "source_reference", "comments", "activity",
    "acceptance_criteria", "acceptance", "notes", "note",
  ]);
  const acceptance = stringField(ticket, ["acceptance_criteria", "acceptance"]);
  const notes = stringField(ticket, ["notes", "note"]);
  const metadata = Object.entries(ticket).filter(([key]) => !known.has(key));
  const created = ticket.created || ticket.created_at;
  const more: [string, ReactNode][] = [
    ["Source", SOURCE_LABELS[String(ticket.source_kind)] || text(ticket.source_kind)],
    ...(!ticket.reporter ? [["Reporter", "Unknown"] as [string, ReactNode]] : []),
    ...(!ticket.type ? [["Type", "Not set"] as [string, ReactNode]] : []),
    ...(!ticket.priority ? [["Priority", "Not set"] as [string, ReactNode]] : []),
    ...(!created ? [["Created", "Unknown"] as [string, ReactNode]] : []),
    ...(!detail.repositories.length ? [["Repositories", "None attached"] as [string, ReactNode]] : []),
    ...(!(trackers || []).length ? [["Boards", "None linked"] as [string, ReactNode]] : []),
    ...metadata.map(([key, value]): [string, ReactNode] => [capitalize(key.replaceAll("_", " ")), text(value)]),
  ];
  return (
    <article className="ticket-body">
      <section id="details" className="ticket-details">
        <h2>Details</h2>
        <div className="facts-grid">
          <dl className="facts-column">
          <div className="fact"><dt>Assignee</dt><dd>{ticket.assignee ? String(ticket.assignee) : <span className="fact-empty">Unassigned</span>}</dd></div>
          {ticket.reporter && <div className="fact"><dt>Reporter</dt><dd>{String(ticket.reporter)}</dd></div>}
          {detail.repositories.length > 0 && <div className="fact fact-context"><dt>{detail.repositories.length === 1 ? "Repository" : "Repositories"}</dt><dd>{detail.repositories.map((value) => value.name).join(", ")}</dd></div>}
          {(trackers || []).length > 0 && <div className="fact"><dt>{trackers!.length === 1 ? "Board" : "Boards"}</dt><dd>{trackersSummary(trackers)}</dd></div>}
          </dl>
          <dl className="facts-column">
          {ticket.type && <div className="fact"><dt>Type</dt><dd>{capitalize(ticket.type)}</dd></div>}
          {ticket.priority && <div className="fact"><dt>Priority</dt><dd>{capitalize(ticket.priority)}</dd></div>}
          {Boolean(created) && <div className="fact fact-context"><dt>Created</dt><dd>{shortTime(created)}</dd></div>}
          </dl>
        </div>
        <details className="metadata more-details"><summary>More details</summary><dl>{more.map(([label, value], index) => <div key={`${label}:${index}`}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></details>
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
    command<UpdateDraft>("application.ticket.update.draft", { bunker_id: bunkerId, ticket_id: detail.ticket.id, repository_id: detail.selected_repository_id, scope: scope === "all" ? "" : scope })
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
      {detail.reports.length === 0 && <div className="quiet-empty"><h2>No synthesis yet</h2><p>{detail.branches.some((branch) => branch.current && branch.local) ? "Press Synthesize under Current branch when you want a report." : "Check out a ticket branch to synthesize its work."}</p></div>}
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

const CHECK_SOURCES: Record<string, string> = { ticket: "This ticket", project: "Project", bunker: "Bunker default", discovered: "Discovered", none: "Not configured" };

// The build and test that will run before the next synthesis, where they come
// from, and a way to give this ticket its own without leaving the page.
function ChecksRow({ detail, bunkerId, onReload, notify }: { detail: TicketDetail; bunkerId: string; onReload: () => void; notify: (message: string, error?: boolean) => void }) {
  const checks = detail.checks;
  const [editing, setEditing] = useState(false);
  const [build, setBuild] = useState("");
  const [test, setTest] = useState("");
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  useEffect(() => { setBuild(checks?.source === "ticket" ? checks.build : ""); setTest(checks?.source === "ticket" ? checks.test : ""); }, [checks?.source, checks?.build, checks?.test, editing]);
  if (!checks) return null;
  async function save(clear = false) {
    setBusy(true);
    try {
      await command("application.ticket.checks.set", { bunker_id: bunkerId, ticket_id: detail.ticket.id, repository_id: detail.selected_repository_id, build: clear ? "" : build, test: clear ? "" : test });
      notify(clear ? "This ticket uses the project's checks again" : "Checks saved for this ticket");
      setEditing(false);
      onReload();
    } catch (error) {
      notify(String(error), true);
    } finally {
      setBusy(false);
    }
  }
  async function ask() {
    setAsking(true);
    setReason("");
    try {
      const value = await command<{ build: string; test: string; reason: string }>("application.checks.discover", { bunker_id: bunkerId, ticket_id: detail.ticket.id, repository_id: detail.selected_repository_id });
      setBuild(value.build);
      setTest(value.test);
      setReason(value.reason);
    } catch (error) {
      notify(String(error), true);
    } finally {
      setAsking(false);
    }
  }
  const summary = checks.build || checks.test ? [checks.build && `build: ${checks.build}`, checks.test && `test: ${checks.test}`].filter(Boolean).join(" · ") : "No build or test runs before synthesis.";
  return (
    <div className="checks-row">
      {detail.check_results?.results?.length ? <div className="check-results" aria-label="Check results">
        <strong>Last checks{detail.check_results.stale ? " · Out of date" : ""}</strong>
        <small>{detail.check_results.branch} · {detail.check_results.commit ? detail.check_results.commit.slice(0, 8) : "Commit not recorded"} · {detail.check_results.checked_at ? relativeTime(detail.check_results.checked_at) : "Time not recorded"}</small>
        {detail.check_results.results.map((result) => <details key={result.kind}><summary>{result.kind === "build" ? "Build" : "Test"}: {result.timed_out ? "Timed out" : result.passed ? "Passed" : "Failed"}</summary><code>{result.command}</code><p>Exit {result.exit ?? "unavailable"} · {result.seconds}s</p><pre>{result.log_text || result.first_error || "No output recorded."}</pre></details>)}
      </div> : <small>No check results on this computer yet.</small>}
      <div className="checks-summary"><span className={`checks-source ${checks.source}`}>{CHECK_SOURCES[checks.source] || checks.source}</span><small title={summary}>{summary}</small>{checks.detail && <small>{checks.detail}</small>}</div>
      {!editing && <div className="checks-actions"><button type="button" title="Configure build and test commands for this ticket" onClick={() => setEditing(true)}>Edit checks</button></div>}
      {editing && (
        <form className="checks-edit" onSubmit={(event) => { event.preventDefault(); save(); }}>
          <label>Build<input value={build} placeholder="cmake --build build --target position" onChange={(event) => setBuild(event.target.value)} /></label>
          <label>Test<input value={test} placeholder="ctest --test-dir build -R Position" onChange={(event) => setTest(event.target.value)} /></label>
          {reason && <p className="quiet-note">Agent: {reason}</p>}
          <div className="checks-actions">
            <button type="submit" className="primary" disabled={busy || !(build || test)}>Save</button>
            <button type="button" disabled={asking} onClick={ask}>{asking ? "Asking…" : "Ask the agent"}</button>
            {checks.source === "ticket" && <button type="button" disabled={busy} onClick={() => save(true)}>Use project's</button>}
            <button type="button" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </form>
      )}
    </div>
  );
}

function TicketRail({
  detail,
  bunkerId,
  repoRoot,
  repositoryId,
  onRepository,
  repositoryLocked,
  scope,
  onScope,
  onReload,
  onFetch,
  notify,
}: {
  detail: TicketDetail;
  bunkerId: string;
  repoRoot: string;
  repositoryId: string;
  onRepository: (value: string) => void;
  repositoryLocked: boolean;
  scope: string;
  onScope: (value: string) => void;
  onReload: () => void;
  onFetch: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [candidate, setCandidate] = useState<{ name: string; base: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [bases, setBases] = useState<string[]>([]);
  const [baseError, setBaseError] = useState("");
  const [workError, setWorkError] = useState("");
  const [existing, setExisting] = useState<string[]>([]);
  const [attachment, setAttachment] = useState("");
  useEffect(() => {
    if (!creating || !repoRoot) return;
    let cancelled = false;
    setBases([]);
    setBaseError("");
    command<{ base_branches: string[]; related_branches: string[]; current_branch: string }>("workflow.state", { ticket: detail.ticket.id, bunker_id: bunkerId }, repoRoot)
      .then((value) => { if (!cancelled) { setBases(value.base_branches); setExisting(value.related_branches || []); setAttachment(value.related_branches?.includes(value.current_branch) ? value.current_branch : ""); } })
      .catch((reason) => { if (!cancelled) setBaseError(String(reason)); });
    return () => { cancelled = true; };
  }, [creating, repoRoot, bunkerId, detail.ticket.id]);
  // Reset before the new repository becomes interactive, not after a user
  // has already opened its branch form on a fast first click.
  useLayoutEffect(() => { setCreating(false); setCandidate(null); setWorkError(""); setAttachment(""); }, [repositoryId]);
  const synthesis = (detail.truth?.synthesis || {}) as Record<string, unknown>;
  const gitState = (detail.truth?.git || {}) as Record<string, unknown>;
  const ticket = detail.ticket;
  const currentBranch = detail.branches.find((branch) => branch.current && branch.local);
  const assignments = ticket.branch_assignments as Record<string, string[]> | undefined;
  const assignedBranches = assignments?.[repositoryId] || (ticket.assigned_branches as string[] | undefined) || [];
  const linked = currentBranch && assignedBranches.includes(currentBranch.name);
  async function synthesize() {
    if (!repoRoot || !currentBranch) return;
    setBusy(true);
    try {
      await command("truth.synthesize", { project: agentProject(detail), ticket: ticket.id, bunker_id: bunkerId, branch: currentBranch.name }, repoRoot);
      notify("Synthesis started");
      onReload();
    } catch (reason) {
      notify(String(reason), true);
      onReload();
    } finally {
      setBusy(false);
    }
  }
  async function createBranch() {
    if (!candidate || !repoRoot) return;
    setBusy(true);
    setWorkError("");
    try {
      const result = await command<{ branch: string }>("workflow.start", {
        ticket: ticket.id,
        bunker_id: bunkerId,
        name: candidate.name,
        base: candidate.base,
      }, repoRoot);
      notify(`Created ${result.branch}`);
      setCandidate(null);
      setCreating(false);
      onReload();
    } catch (reason) {
      setWorkError(String(reason));
      setCandidate(null);
      setCreating(false);
      notify(String(reason), true);
    } finally {
      setBusy(false);
      onReload();
    }
  }
  async function attachBranch() {
    if (!attachment || !repoRoot) return;
    setBusy(true); setWorkError("");
    try {
      await command("workflow.attach", { ticket: ticket.id, bunker_id: bunkerId, branch: attachment }, repoRoot);
      notify(`Attached ${attachment}`); setAttachment(""); setCreating(false);
    } catch (reason) { setWorkError(String(reason)); notify(String(reason), true); }
    finally { setBusy(false); onReload(); }
  }
  async function syncBeforeRetry() {
    setBusy(true);
    try { await command("application.bunker.sync", { bunker_id: bunkerId }); setWorkError(""); notify("Bunker synced. Use + to attach the existing branch."); }
    catch (reason) { setWorkError(String(reason)); }
    finally { setBusy(false); onReload(); }
  }
  return (
    <aside className="ticket-rail">
      {detail.repositories.length > 1 && <label>Repository<select aria-label="Working repository" value={repositoryId} disabled={busy || repositoryLocked} title={repositoryLocked ? "Return to the ticket before changing repositories." : undefined} onChange={(event) => onRepository(event.target.value)}>{detail.repositories.map((repo) => <option key={repo.id} value={repo.id}>{repo.name}{repo.local_path ? "" : " (not connected)"}</option>)}</select></label>}
      <section className="state-hub">
        {repoRoot && currentBranch && <div className="current-ticket-branch" aria-label="Current ticket branch">
          <header><h2>Current branch</h2><i className="state-dot ready" title={`Checked out in ${repoRoot}`} /></header>
          <button className="current-branch-name" onClick={() => onScope(currentBranch.name)} title="View this branch's report">{currentBranch.name}</button>
          <small title={linked ? "This branch's ticket link is saved." : "The branch name matches this ticket. Synthesize saves the link automatically; it does not switch branches."}>{linked ? "Linked" : "Matched"} to {ticket.id}</small>
          <button className="wide-action" disabled={busy || synthesis.status === "updating"} onClick={synthesize} title={`Synthesize ${currentBranch.name}, regardless of the report being viewed`}>{synthesis.status === "updating" ? "Synthesizing…" : "Synthesize"}</button>
        </div>}
        <header><h2>Synthesis</h2></header>
        <div className="state-line"><i className={`state-dot ${synthesis.status === "current" ? "ready" : synthesis.status === "updating" ? "loading" : ""}`} /><span><strong>{String(synthesis.compact || synthesis.label || "Not synthesized")}{synthesis.status === "updating" && <Elapsed since={String((synthesis.progress as Record<string, unknown> | undefined)?.started_at || "")} />}</strong><small>{String(synthesis.detail || "Use Synthesize from a checked-out ticket branch.")}</small></span></div>
        {Boolean(synthesis.log) && <details className="diagnostic-path"><summary>Diagnostic location</summary><code>{String(synthesis.log)}</code><button onClick={() => navigator.clipboard.writeText(String(synthesis.log)).then(() => notify("Diagnostic path copied")).catch(() => notify("Copy was unavailable; the full path is shown above.", true))}>Copy path</button></details>}
        {repoRoot && !currentBranch && <p className="rail-empty">Check out a {ticket.id} branch to synthesize. Use + to create or link one.</p>}
        <ChecksRow detail={detail} bunkerId={bunkerId} onReload={onReload} notify={notify} />
      </section>
      <section className="branches-panel">
        <header><h2>Branches</h2><div className="branch-header-actions"><IconButton icon="sync" label="Refresh branches" hint={repoRoot ? "Fetch Git refs. Branches also refresh automatically when you open this ticket; your checkout and files stay unchanged." : "Connect a local repository in Project settings to refresh branches."} disabled={!repoRoot || gitState.status === "refreshing"} loading={gitState.status === "refreshing"} issue={gitState.status === "failed" ? String(gitState.detail || "Could not reach the Git remote. Retry here.") : undefined} onClick={onFetch} /><button className="mini-plus" aria-label="Create branch" disabled={!repoRoot || busy || repositoryLocked} title={repositoryLocked ? "Return to the ticket before creating a branch." : "Create or attach a ticket branch"} onClick={() => setCreating(!creating)}>+</button></div></header>
        {gitState.status === "cached" && <p className="rail-empty" role="status">Git freshness unknown. {String(gitState.detail || "Refresh branches to check the remote.")}</p>}
        {gitState.status === "failed" && <p className="inline-error" role="status">{String(gitState.label || "Git refresh failed")}. Showing saved branches. {String(gitState.detail || "Use the refresh icon to retry.")}</p>}
        {workError && <div className="inline-error" role="alert">{workError}{workError.includes("branch_assignment_pending") || workError.includes("Sync") || workError.includes("sync") ? <button disabled={busy} onClick={syncBeforeRetry}>Sync Bunker</button> : null}</div>}
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
        {creating && !candidate && <form className="branch-form" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); setCandidate({ name: String(data.get("name") || ""), base: String(data.get("base") || "") }); }}><label>Short description<input name="name" placeholder="position-system" required /></label><label>Branch from<select name="base" disabled={!bases.length}>{bases.map((base) => <option key={base} value={base}>{base}</option>)}</select></label>{baseError && <p role="alert">{baseError}</p>}<button className="primary" disabled={!bases.length}>Review branch</button></form>}
        {creating && !candidate && existing.length > 0 && <div className="branch-form"><label>Existing branch<select value={attachment} onChange={(event) => setAttachment(event.target.value)}><option value="">Choose branch</option>{existing.map((name) => <option key={name}>{name}</option>)}</select></label><p>Link this branch to the ticket. Your checkout will not change.</p><button disabled={busy || !attachment} onClick={attachBranch}>Attach branch</button></div>}
        {candidate && <div className="confirmation"><strong>Create this branch?</strong><p>TicketBunker will require a clean worktree, switch to <code>{candidate.base}</code>, fast-forward it when possible, then create the ticket branch.</p><div><BackButton label="Back to branch options" onClick={() => setCandidate(null)} /><button className="primary" disabled={busy} onClick={createBranch}>Create branch</button></div></div>}
      </section>
    </aside>
  );
}

function topicOf(quote: Quote): string {
  return quote.category === "file" && quote.path ? `file:${quote.path}` : String(quote.category);
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
  pending,
  onPendingSent,
  openFile,
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
  pending: { quote: Quote; message: string; id: number } | null;
  onPendingSent: () => void;
  openFile: string;
  onReload: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  // A topic is a report part, or "file:<path>" for a conversation about one file.
  const [topic, setTopic] = useState<string>(quote ? topicOf(quote) : "standing");
  const [coworker, setCoworker] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const thread = useMemo(() => ({ category: topic.startsWith("file:") ? "file" : topic === "files" ? "standing" : topic, path: topic.startsWith("file:") ? topic.slice(5) : "" }), [topic]);
  const scoped = scope === "all" ? "combined" : scope;
  useEffect(() => {
    if (quote) {
      setTopic(topicOf(quote));
      onOpen(true);
    }
  }, [quote, onOpen]);
  useEffect(() => {
    if (!open || !repoRoot) return;
    let active = true;
    setCoworker((current) => (current && current.path === thread.path && current.category === thread.category ? current : null));
    command<Record<string, unknown>>("coworker.state", { project: agentProject(detail), ticket: detail.ticket.id, bunker_id: bunkerId, category: thread.category, scope: scoped, path: thread.path }, repoRoot)
      .then((value) => { if (active) setCoworker(value); })
      .catch((reason) => active && setError(String(reason)));
    return () => { active = false; };
  }, [open, repoRoot, detail.project.key, detail.ticket.id, thread.category, thread.path, scoped]);

  async function deliver(message: string, reference: Quote | null, target: { category: string; path: string }) {
    setBusy(true);
    setError("");
    try {
      const result = await command<Record<string, unknown>>("coworker.chat", { project: agentProject(detail), ticket: detail.ticket.id, bunker_id: bunkerId, category: target.category, scope: scoped, path: target.path, message, reference }, repoRoot);
      setCoworker(result);
      onQuote(null);
      return true;
    } catch (reason) {
      setError(String(reason));
      return false;
    } finally {
      setBusy(false);
    }
  }
  // A reply typed under a note in the editor: open the file's tab and send.
  useEffect(() => {
    if (!pending) return;
    const target = { category: pending.quote.category === "file" ? "file" : pending.quote.category, path: pending.quote.path && pending.quote.category === "file" ? pending.quote.path : "" };
    setTopic(topicOf(pending.quote));
    onOpen(true);
    deliver(pending.message, pending.quote, target).finally(onPendingSent);
  }, [pending?.id]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const message = String(new FormData(form).get("message") || "");
    if (!message.trim()) return;
    if (await deliver(message, quote, thread)) form.reset();
  }

  async function proposal(action: "accept" | "dismiss", id: string) {
    setBusy(true);
    try {
      await command(`coworker.proposal.${action}`, { project: agentProject(detail), ticket: detail.ticket.id, bunker_id: bunkerId, proposal: id }, repoRoot);
      notify(action === "accept" ? "Report update accepted" : "Proposal dismissed");
      onReload();
      const refreshed = await command<Record<string, unknown>>("coworker.state", { project: agentProject(detail), ticket: detail.ticket.id, bunker_id: bunkerId, category: thread.category, scope: scoped, path: thread.path }, repoRoot);
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
  const threads = ((coworker?.file_threads as Array<{ path: string; messages: number; last_at: string; last_text: string }> | undefined) || []);
  const fileList = [...threads];
  if (openFile && !fileList.some((item) => item.path === openFile)) fileList.unshift({ path: openFile, messages: 0, last_at: "", last_text: "" });
  const inFiles = topic === "files" || thread.category === "file";
  const placeholder = thread.category === "file" ? `Ask about ${thread.path.split("/").pop()}` : `Ask about ${thread.category}`;
  async function forget(path: string) {
    setBusy(true);
    try {
      const value = await command<Record<string, unknown>>("coworker.file.forget", { project: agentProject(detail), ticket: detail.ticket.id, bunker_id: bunkerId, scope: scoped, path }, repoRoot);
      setCoworker(value);
      if (thread.path === path) setTopic("files");
      notify(`Forgot the conversation about ${path.split("/").pop()}`);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <aside className="coworker-drawer">
      <header><div><strong>{String(coworker?.title || `${detail.ticket.id} coworker`)}</strong><small>{scope === "all" ? "All branches" : scope}</small></div><button aria-label="Collapse coworker" onClick={() => onOpen(false)}>→</button></header>
      <nav>
        {CHAT_TOPICS.map((value) => <button key={value} className={topic === value ? "selected" : ""} onClick={() => { setTopic(value); onQuote(null); }}>{value[0].toUpperCase() + value.slice(1)}</button>)}
        <button className={inFiles ? "selected files-tab" : "files-tab"} onClick={() => { setTopic("files"); onQuote(null); }}>Files{fileList.length > 0 && <sup>{fileList.length}</sup>}</button>
      </nav>
      {thread.category === "file" && <div className="file-thread-bar"><button type="button" onClick={() => { setTopic("files"); onQuote(null); }}>‹ Files</button><strong title={thread.path}>{thread.path.split("/").pop()}</strong><small>{thread.path}</small><button type="button" className="danger-control" disabled={busy} onClick={() => forget(thread.path)}>Forget</button></div>}
      {topic === "files" && (
        <div className="file-thread-list">
          {!coworker && <p className="chat-empty">Loading conversations…</p>}
          {coworker && fileList.length === 0 && <p className="chat-empty">No file conversations yet. Reply under a note in the editor, or select code and send it to chat.</p>}
          {fileList.map((item) => (
            <div key={item.path} className="file-thread">
              <button type="button" className="file-thread-open" onClick={() => { setTopic(`file:${item.path}`); onQuote(null); }}>
                <strong>{item.path.split("/").pop()}</strong>
                <small>{item.path}</small>
                {item.messages > 0 ? <span>{item.messages} message{item.messages === 1 ? "" : "s"} · {relativeTime(item.last_at)}</span> : <span>{item.path === openFile ? "Open in the editor · no conversation yet" : "No conversation yet"}</span>}
              </button>
              {item.messages > 0 && <button type="button" className="file-thread-forget" aria-label={`Forget the conversation about ${item.path}`} disabled={busy} onClick={() => forget(item.path)}>×</button>}
            </div>
          ))}
        </div>
      )}
      {topic !== "files" && <div className="chat-messages">{!coworker && <p className="chat-empty">Loading…</p>}{coworker && messages.length === 0 && <p className="chat-empty">Ask for context, challenge an assumption, or request a report correction.</p>}{messages.map((message, index) => <article key={index} className={String(message.role) === "user" ? "user-message" : "agent-message"}>{Boolean(message.reference) && <small>Quoted context</small>}<Markdown compact>{String(message.text || "")}</Markdown></article>)}{proposals.filter((item) => item.status === "pending").map((item) => <article className="proposal" key={String(item.id)}><strong>Proposed report update</strong><p>Reviewing this accepts changes only through TicketBunker’s report gates.</p><div><button disabled={busy} onClick={() => proposal("dismiss", String(item.id))}>Dismiss</button><button className="primary" disabled={busy} onClick={() => proposal("accept", String(item.id))}>Accept</button></div></article>)}</div>}
      {topic !== "files" && <form className="chat-input" onSubmit={send}>{quote && <div className="quote-chip"><span>{quote.text}</span><button type="button" aria-label="Remove quoted context" onClick={() => onQuote(null)}>×</button></div>}<textarea name="message" aria-label="Message to ticket coworker" rows={2} placeholder={placeholder} required /><button className="primary" disabled={busy}>Send</button>{error && <p className="inline-error">{error}</p>}</form>}
      {topic === "files" && error && <p className="inline-error">{error}</p>}
    </aside>
  );
}

// A small reply box under every note: typing here starts (or continues) the
// conversation about this file with the note's lines quoted.
function NoteReply({ onSend }: { onSend: (message: string) => void }) {
  const [value, setValue] = useState("");
  function submit() {
    const message = value.trim();
    if (!message) return;
    onSend(message);
    setValue("");
  }
  return (
    <form className="note-reply" onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <textarea value={value} rows={1} placeholder="Reply…" aria-label="Reply to this note" onChange={(event) => setValue(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(); } }} />
      <button type="submit" disabled={!value.trim()}>Send</button>
    </form>
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
  onReply,
  notify,
}: {
  draft: SourceDraft;
  projectRoot: string;
  notes: SourceNote[];
  onBack: () => void;
  onDraft: (draft: SourceDraft) => void;
  onQuote: (quote: Quote) => void;
  onReply: (quote: Quote, message: string) => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [content, setContent] = useState(draft.content);
  const [selection, setSelection] = useState<{ top: number; left: number; start: number; end: number; text: string } | null>(null);
  const notesColumn = useRef<HTMLDivElement>(null);
  const syncing = useRef(false);
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState<number>(0);
  const [active, setActive] = useState<number | null>(null);
  const [noteTops, setNoteTops] = useState<number[]>([]);
  // Line filter: nothing selected means every note shows; selecting lines
  // narrows the notes to those lines and cycles the editor between them.
  const [selectedLines, setSelectedLines] = useState<number[]>([]);
  const [cursor, setCursor] = useState(0);
  const editor = useRef<HTMLTextAreaElement>(null);
  const mirror = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const noteRefs = useRef<Array<HTMLElement | null>>([]);
  const lines = useMemo(() => content.split("\n"), [content]);
  const noteLines = useMemo(() => [...new Set(notes.map((note) => note.line))].sort((a, b) => a - b), [notes]);
  const visibleNotes = useMemo(() => (selectedLines.length ? notes.filter((note) => selectedLines.includes(note.line)) : notes), [notes, selectedLines]);
  const cycle = selectedLines.length ? selectedLines : noteLines;
  const notedLines = useMemo(() => {
    const map = new Map<number, string>();
    for (const note of visibleNotes) for (let line = note.line; line <= note.endLine; line += 1) if (!map.has(line) || note.tone === "defect") map.set(line, note.tone);
    return map;
  }, [visibleNotes]);
  useEffect(() => { setSelectedLines([]); setCursor(0); }, [draft.path]);

  function toggleLine(line: number) {
    setSelectedLines((current) => {
      const next = current.includes(line) ? current.filter((value) => value !== line) : [...current, line].sort((a, b) => a - b);
      const position = next.indexOf(line);
      setCursor(position >= 0 ? position : 0);
      return next;
    });
    goTo(line);
  }
  function step(direction: 1 | -1) {
    if (cycle.length === 0) return;
    const next = (cursor + direction + cycle.length) % cycle.length;
    setCursor(next);
    goTo(cycle[next]);
  }

  function lineTop(line: number): number {
    const element = mirror.current?.children[line - 1] as HTMLElement | undefined;
    return element ? element.offsetTop : 0;
  }
  function goTo(line: number, flash = true) {
    const top = lineTop(line);
    scroller.current?.scrollTo({ top: Math.max(0, top - 140), behavior: "smooth" });
    notesColumn.current?.scrollTo({ top: Math.max(0, top - 140), behavior: "smooth" });
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
    visibleNotes.forEach((note, index) => {
      const wanted = lineTop(note.line);
      const top = Math.max(wanted, floor);
      tops.push(top);
      floor = top + (noteRefs.current[index]?.offsetHeight || 0) + 6;
    });
    setNoteTops(tops);
  }, [visibleNotes, lines, draft.settings.line_wrap]);

  async function save() {
    setBusy(true);
    try {
      const result = await command<{ content: string; digest: string; saved: boolean }>("editor.save", { path: draft.path, content, expected_digest: draft.digest }, projectRoot);
      onDraft({ ...draft, content: result.content, digest: result.digest });
      setContent(result.content);
      notify(result.saved ? `Saved ${draft.path}` : `${draft.path} is unchanged`);
      return true;
    } catch (reason) {
      notify(String(reason), true);
      return String(reason);
    } finally {
      setBusy(false);
    }
  }
  function quoteOf(start: number, end: number, text: string): Quote {
    return { kind: "source", category: "file", scope: draft.branch || "all", branch: draft.branch, path: draft.path, line_start: start, line_end: end, unsaved: content !== draft.content, text: text.slice(0, 4000) };
  }
  useUnsavedEditor({ dirty: content !== draft.content, busy, save, discard: () => setContent(draft.content) });
  // Selecting text in the file offers the same toolbar as selecting report
  // text: Send to chat, Copy. Nothing happens until a button is pressed.
  function selected() {
    const input = editor.current;
    if (!input || input.selectionStart === input.selectionEnd) return setSelection(null);
    const before = content.slice(0, input.selectionStart);
    const text = content.slice(input.selectionStart, input.selectionEnd);
    const start = before.split("\n").length;
    const end = start + text.split("\n").length - 1;
    setSelection({ top: lineTop(start), left: 60, start, end, text });
  }
  function noteQuote(note: SourceNote): Quote {
    const text = lines.slice(note.line - 1, note.endLine).join("\n");
    return quoteOf(note.line, note.endLine, text || note.text);
  }
  // The two columns scroll together, and the notes column may scroll past
  // the end of the file on its own when its notes overflow.
  function syncFrom(source: "file" | "notes") {
    if (syncing.current) return;
    const file = scroller.current;
    const column = notesColumn.current;
    if (!file || !column) return;
    syncing.current = true;
    if (source === "file") column.scrollTop = file.scrollTop;
    else file.scrollTop = Math.min(column.scrollTop, file.scrollHeight - file.clientHeight);
    requestAnimationFrame(() => { syncing.current = false; });
  }
  const wrap = Boolean(draft.settings.line_wrap);
  return (
    <section className={notes.length ? "source-editor annotated" : "source-editor"}>
      <header><BackButton label="Back to ticket" onClick={onBack} /><div><strong>{draft.path}</strong><small>{draft.branch || "Detached"}{notes.length > 0 && ` · ${notes.length} note${notes.length === 1 ? "" : "s"} from the report`}</small></div><div><button className="primary" disabled={busy || content === draft.content} onClick={save}>Save</button></div></header>
      {notes.length > 0 && (
        <div className="note-filter" role="toolbar" aria-label="Notes by line" onKeyDown={(event) => { if (event.key === "ArrowRight") { event.preventDefault(); step(1); } else if (event.key === "ArrowLeft") { event.preventDefault(); step(-1); } }}>
          <span className="note-filter-label">Lines</span>
          {noteLines.map((line) => {
            const count = notes.filter((note) => note.line === line).length;
            const on = selectedLines.includes(line);
            const tone = notes.find((note) => note.line === line && note.tone === "defect") ? "defect" : "";
            return <button key={line} type="button" className={`line-chip ${tone}${on ? " on" : ""}${cycle[cursor] === line ? " current" : ""}`} aria-pressed={on} title={`${count} note${count === 1 ? "" : "s"} on line ${line}`} onClick={() => toggleLine(line)}>{line}{count > 1 && <sup>{count}</sup>}</button>;
          })}
          <span className="note-filter-gap" />
          {selectedLines.length > 0 && <button type="button" className="line-chip-reset" onClick={() => { setSelectedLines([]); setCursor(0); }}>All</button>}
          <button type="button" className="line-step" aria-label="Previous noted line" onClick={() => step(-1)}>‹</button>
          <button type="button" className="line-step" aria-label="Next noted line" onClick={() => step(1)}>›</button>
        </div>
      )}
      <div className="editor-body">
        <div ref={scroller} className="editor-scroll" onScroll={() => syncFrom("file")}>
          <div className={wrap ? "editor-surface wrap" : "editor-surface"} style={{ tabSize: draft.settings.tab_size || 4 }}>
            <div className="editor-gutter" aria-hidden="true">{lines.map((_, index) => <div key={index} className={notedLines.has(index + 1) ? `gutter-line noted ${notedLines.get(index + 1)}` : "gutter-line"} style={{ height: lineTop(index + 2) - lineTop(index + 1) || undefined }}>{index + 1}</div>)}</div>
            <div className="editor-stack">
              <div ref={mirror} className="editor-mirror" aria-hidden="true">
                {lines.map((line, index) => {
                  const number = index + 1;
                  const classes = ["mirror-line"];
                  if (notedLines.has(number)) classes.push("noted", notedLines.get(number) || "");
                  if (target === number) classes.push("target");
                  if (active !== null && visibleNotes[active] && number >= visibleNotes[active].line && number <= visibleNotes[active].endLine) classes.push("active");
                  return <div key={index} className={classes.join(" ")}>{line || " "}</div>;
                })}
              </div>
              <textarea ref={editor} aria-label={`Edit ${draft.path}`} disabled={busy} value={content} onChange={(event) => { setContent(event.target.value); setSelection(null); }} onMouseUp={selected} onKeyUp={(event) => { if (event.shiftKey || event.key === "Shift") selected(); }} onBlur={(event) => { if (!(event.relatedTarget as HTMLElement | null)?.closest?.(".floating-actions")) setSelection(null); }} spellCheck={false} wrap={wrap ? "soft" : "off"} rows={lines.length} />
              {selection && (
                <FloatingActions
                  label="Selection actions"
                  style={{ top: selection.top, left: selection.left, transform: "translateY(calc(-100% - 6px))" }}
                  onClose={() => setSelection(null)}
                  actions={[
                    { label: "Send to chat", onSelect: () => onQuote(quoteOf(selection.start, selection.end, selection.text)) },
                    { label: "Copy", onSelect: () => { navigator.clipboard?.writeText(selection.text).catch(() => undefined); } },
                  ]}
                />
              )}
            </div>
          </div>
        </div>
        {notes.length > 0 && (
          <div ref={notesColumn} className="editor-notes-scroll" onScroll={() => syncFrom("notes")}>
            <aside className="editor-notes" aria-label="Report notes for this file" style={{ minHeight: lineTop(lines.length + 1) + 60 }}>
              {visibleNotes.map((note, index) => (
                <article
                  key={`${note.line}:${note.endLine}:${note.label}:${note.text}`}
                  ref={(element) => { noteRefs.current[index] = element; }}
                  className={`editor-note ${note.tone}${active === index ? " active" : ""}`}
                  style={{ top: noteTops[index] ?? lineTop(note.line) }}
                  tabIndex={0}
                  onMouseEnter={() => setActive(index)}
                  onMouseLeave={() => setActive((value) => (value === index ? null : value))}
                  onFocus={() => setActive(index)}
                  onClick={(event) => { if ((event.target as HTMLElement).closest("button, textarea, form, .source-reference")) return; goTo(note.line); }}
                  onKeyDown={(event) => { if (event.key === "Enter" && !(event.target as HTMLElement).closest("textarea")) goTo(note.line); }}
                >
                  <header><span className={`note-label ${note.tone}`}>{note.label}</span><span className="note-line">{note.endLine > note.line ? `Lines ${note.line}–${note.endLine}` : `Line ${note.line}`}</span></header>
                  <Markdown compact>{note.text}</Markdown>
                  <NoteReply onSend={(message) => onReply(noteQuote(note), message)} />
                </article>
              ))}
            </aside>
          </div>
        )}
      </div>
    </section>
  );
}

export default function TicketWorkspace(props: Props) {
  return <UnsavedChangesBoundary><Workspace {...props} /></UnsavedChangesBoundary>;
}

function Workspace({ bunker, reference, onBack, notify }: Props) {
  const protect = useGuardedAction();
  const [snapshot, setDetail] = useState<TicketDetail | null>(null);
  const [repositoryChoice, setRepositoryChoice] = useState("");
  const repositoryId = repositoryChoice || snapshot?.selected_repository_id || snapshot?.repositories[0]?.id || "";
  const detail = useMemo(() => snapshot ? ({ ...snapshot,
    selected_repository_id: repositoryId,
    branches: snapshot.branches.filter((branch) => branch.repository_id === repositoryId),
    reports: snapshot.reports.filter((report) => report.repository_id === repositoryId ||
      (report.scope === "combined" && snapshot.repositories.length <= 1)),
  }) : null, [snapshot, repositoryId]);
  const [error, setError] = useState("");
  const [scope, setScope] = useState("");
  const [section, setSection] = useState("details");
  const [chatOpen, setChatOpen] = useState(false);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [pending, setPending] = useState<{ quote: Quote; message: string; id: number } | null>(null);
  const [editor, setEditor] = useState<SourceDraft | null>(null);
  const repoRoot = detail?.repositories.find((value) => value.id === repositoryId)?.local_path || "";
  const readSequence = useRef(0);

  function load() {
    const sequence = ++readSequence.current;
    setError("");
    command<TicketDetail>("application.ticket.get", { bunker_id: bunker.id, ticket_id: reference.id, repository_id: repositoryChoice })
      .then((value) => { if (readSequence.current === sequence) setDetail(value); })
      .catch((reason) => { if (readSequence.current === sequence) setError(String(reason)); });
  }
  useEffect(load, [bunker.id, reference.id, repositoryChoice]);
  // Returning from Git should show the actual checkout, not the last report
  // scope. This is a local read, not a fetch or an automatic synthesis.
  useEffect(() => {
    const refreshLocal = () => { if (!document.hidden) load(); };
    window.addEventListener("focus", refreshLocal);
    document.addEventListener("visibilitychange", refreshLocal);
    return () => { window.removeEventListener("focus", refreshLocal); document.removeEventListener("visibilitychange", refreshLocal); };
  }, [bunker.id, reference.id, repositoryChoice]);
  useEffect(() => {
    if (!repoRoot) return;
    let active = true;
    command("truth.queue", { bunker_id: bunker.id, project: detail!.project.key, ticket: reference.id }, repoRoot)
      .then(() => { if (active) load(); })
      .catch((reason) => { if (active) notify(String(reason), true); });
    return () => { active = false; };
  }, [repoRoot, bunker.id, reference.id]);
  function fetchBranches() {
    command("truth.queue", { bunker_id: bunker.id, project: detail!.project.key, ticket: reference.id, force: true }, repoRoot)
      .then(load).catch((reason) => notify(String(reason), true));
  }
  // While a synthesis runs, the page follows it: poll the ticket every few
  // seconds and take one more read when it finishes, so the report appears
  // without a manual refresh. A hidden tab stays quiet; returning to it
  // reads once immediately.
  const synthesisStatus = String((detail?.truth?.synthesis as Record<string, unknown> | undefined)?.status || "");
  const wasUpdating = useRef(false);
  useEffect(() => {
    const updating = synthesisStatus === "updating" || detail?.truth?.refresh === "running";
    if (!updating && wasUpdating.current) load();
    wasUpdating.current = updating;
    if (!updating) return;
    const tick = () => { if (!document.hidden) load(); };
    const timer = window.setInterval(tick, 3000);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [synthesisStatus, detail?.truth?.refresh, bunker.id, reference.id]);
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
    protect(() => {
      setEditor(null);
      setSection(id);
      window.requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }));
    });
  }

  useEffect(() => {
    if (!detail) return;
    const withReports = detail.reports.filter((value) => value.scope.startsWith("branch:")).map((value) => value.scope.slice(7));
    const valid = scope === "all" ? withReports.length > 1 : scope !== "" && (withReports.includes(scope) || detail.branches.some((branch) => branch.name === scope));
    if (valid) return;
    const current = detail.branches.find((branch) => branch.current)?.name;
    setScope(current && withReports.includes(current) ? current : withReports[0] || current || detail.branches[0]?.name || "");
  }, [detail]);

  function openSource(path: string, line: number) {
    protect(() => { void readSource(path, line); });
  }
  async function readSource(path: string, line: number) {
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
    setQuote({ ...value, scope: scope === "all" ? "combined" : scope, category: "file" });
    setChatOpen(true);
  }
  function replyFromEditor(value: Quote, message: string) {
    setPending({ quote: { ...value, scope: scope === "all" ? "combined" : scope, category: "file" }, message, id: Date.now() });
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
  if (error && !detail) return <div className="load-error"><BackButton label="Back to tickets" onClick={onBack} /><h1>Ticket could not open</h1><p>{error}</p><button onClick={load}>Retry</button></div>;
  if (!detail) return <div className="ticket-loading full">Opening {reference.id}</div>;
  const ticket = detail.ticket;
  const link = sourceLink(detail);
  const trackers = flattenProjects(bunker.projects).find((value) => value.id === detail.project.id)?.trackers;
  return (
    <div className="ticket-shell">
      <header className="ticket-header">
        <BackButton label="Back to tickets" onClick={() => protect(onBack)} />
        <div className="ticket-heading"><p>{detail.project.name}</p><h1><span>{ticket.id}</span> {ticket.title}</h1><div className="ticket-chips"><span className="status-chip">{ticket.status || "Open"}</span>{ticket.labels.map((value) => <span key={value} className="label-chip">{value}</span>)}<small>{ticket.assignee ? `${ticket.assignee} · ` : ""}Updated {relativeTime(String(ticket.updated || ""))}</small>{link && <a className="source-link" href={link.href} target="_blank" rel="noreferrer">{link.label} ↗</a>}</div></div>
      </header>
      <div className="ticket-layout">
        <aside className="ticket-chapters"><nav aria-label="Ticket sections">
          {[["details", "Details"], ["description", "Description"], ["agent-work", "Agent work"]].map(([id, label]) => <button key={id} className={!editor && section === id ? "selected" : ""} aria-current={!editor && section === id ? "location" : undefined} onClick={() => jumpTo(id)}>{label}</button>)}
        </nav></aside>
        <main className="ticket-main">
          {error && <div role="alert" className="inline-error">{error} <button onClick={load}>Retry</button></div>}
          {editor ? <SourceEditor draft={editor} projectRoot={repoRoot} notes={editorNotes} onBack={() => protect(() => setEditor(null))} onDraft={setEditor} onQuote={quoteSource} onReply={replyFromEditor} notify={notify} /> : <SourceActionsContext.Provider value={sourceActions}>
            <TicketTab detail={detail} trackers={trackers} openSource={openSource} copySource={copySource} />
            <div id="agent-work"><AgentTab detail={detail} bunkerId={bunker.id} scope={scope} onScope={setScope} onQuote={quoteReport} openSource={openSource} copySource={copySource} notify={notify} /></div>
          </SourceActionsContext.Provider>}
        </main>
        <TicketRail detail={detail} bunkerId={bunker.id} repoRoot={repoRoot} repositoryId={repositoryId} repositoryLocked={Boolean(editor)} onRepository={(value) => { setRepositoryChoice(value); setScope(""); setQuote(null); setPending(null); }} scope={scope} onScope={(value) => protect(() => { setScope(value); setEditor(null); window.requestAnimationFrame(() => document.getElementById("agent-work")?.scrollIntoView({ behavior: "smooth", block: "start" })); })} onReload={load} onFetch={fetchBranches} notify={notify} />
      </div>
      <CoworkerDrawer open={chatOpen} onOpen={setChatOpen} detail={detail} bunkerId={bunker.id} repoRoot={repoRoot} scope={scope} quote={quote} onQuote={setQuote} pending={pending} onPendingSent={() => setPending(null)} openFile={editor?.path || ""} onReload={load} notify={notify} />
    </div>
  );
}
