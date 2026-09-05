import { FormEvent, MouseEvent, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Markdown, { TRUST_LABELS } from "./Markdown";
import { provenanceLine, reportTopic, relativeTime, TOPICS, verifiedStamp, type Topic } from "./report";
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
  category: Topic;
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
  ]);
  const metadata = Object.entries(ticket).filter(([key, value]) => !known.has(key) && value !== "" && value !== null && value !== undefined);
  return (
    <article className="ticket-body">
      <section id="description" className="description">
        <Markdown onOpenSource={openSource} onCopySource={copySource}>
          {String(ticket.description || "No description was provided.")}
        </Markdown>
      </section>
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
      {detail.comments.length > 0 && (
        <section className="ticket-stream"><h2>Comments</h2>{detail.comments.map((comment, index) => <article key={index}><header><strong>{text(comment.author || comment.user)}</strong><time>{text(comment.created || comment.created_at)}</time></header><Markdown compact>{text(comment.body || comment.text)}</Markdown></article>)}</section>
      )}
      {detail.activity.length > 0 && (
        <section className="ticket-stream"><h2>Activity</h2>{detail.activity.map((event, index) => <article key={index}><header><strong>{text(event.event || event.action || "Update")}</strong><time>{text(event.created || event.timestamp)}</time></header><p>{text(event.detail || event.message)}</p></article>)}</section>
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
  function selected(event: MouseEvent) {
    const selection = window.getSelection();
    const selectedText = selection?.toString().trim() || "";
    if (!selectedText || !synthesisRef.current?.contains(event.target as Node)) return;
    onQuote({
      kind: "synthesis",
      category: topic,
      scope,
      branch: report.scope.startsWith("branch:") ? report.scope.slice(7) : "",
      text: selectedText.slice(0, 4000),
    });
  }
  return (
    <section className="synthesis-card" onMouseUp={selected}>
      <header><h3>{topic[0].toUpperCase() + topic.slice(1)}</h3></header>
      <div ref={synthesisRef} className="synthesis-copy">
        {value.synthesis
          ? <Markdown compact onOpenSource={openSource} onCopySource={copySource}>{value.synthesis}</Markdown>
          : value.trail
            ? <><p className="missing-copy">No synthesis brief; this is the evidence trail.</p><Markdown compact onOpenSource={openSource} onCopySource={copySource}>{value.trail}</Markdown></>
            : <p className="missing-copy">Nothing was published for {topic}.</p>}
      </div>
      {value.synthesis && <details><summary>Full trail</summary>{value.trail ? <Markdown compact onOpenSource={openSource} onCopySource={copySource}>{value.trail}</Markdown> : <p>No evidence trail was published.</p>}</details>}
    </section>
  );
}

function AgentTab({
  detail,
  scope,
  onScope,
  onQuote,
  openSource,
  copySource,
}: {
  detail: TicketDetail;
  scope: string;
  onScope: (value: string) => void;
  onQuote: (quote: Quote) => void;
  openSource: (path: string, line: number) => void;
  copySource: (path: string, line: number) => void;
}) {
  const branchReports = detail.reports.filter((value) => value.scope.startsWith("branch:"));
  const legacyMaster = detail.reports.filter((value) => value.scope === "combined");
  const reports = scope === "all"
    ? (branchReports.length ? branchReports : legacyMaster)
    : detail.reports.filter((value) => value.scope === `branch:${scope}`);
  const selectable = branchReports.length > 0;
  return (
    <article className="agent-work">
      <div className="agent-toolbar">
        <div><h2>Agent work</h2><p>{scope === "all" ? `Every branch report, most recent first (${branchReports.length}).` : "This branch's report. Synthesis first; evidence stays one disclosure away."}</p></div>
        {selectable && <label><span className="sr-only">Report scope</span><select value={scope} onChange={(event) => onScope(event.target.value)}>{branchReports.map((report) => <option key={report.scope} value={report.scope.slice(7)}>{report.scope.slice(7)}</option>)}{branchReports.length > 1 && <option value="all">All branches</option>}</select></label>}
      </div>
      {reports.length > 0 && <p className="trust-legend"><span>Each claim carries how much to trust it:</span>{["observation", "real", "speculation:grounded", "speculation:adjacent", "speculation:ungrounded"].map((key) => <span key={key} className={`trust-chip ${key.startsWith("speculation") ? key.split(":")[1] : key}`} title={TRUST_LABELS[key].title}>{TRUST_LABELS[key].label}</span>)}</p>}
      {reports.length === 0 && scope !== "all" && detail.reports.length > 0 && <div className="quiet-empty"><h2>No report for {scope} yet</h2><p>Check it out and press Synthesize, or pick a branch that has a report.</p></div>}
      {detail.reports.length === 0 && <div className="quiet-empty"><h2>No synthesis yet</h2><p>Check out a ticket branch and press Synthesize when you want a report.</p></div>}
      {reports.map((report) => (
        <section className="branch-report" key={report.scope}>
          {scope === "all" && <div className="branch-divider"><span>{report.scope === "combined" ? "Combined report" : report.scope.slice(7)}</span></div>}
          {report.scope !== "combined" && <p className="report-provenance">{provenanceLine(verifiedStamp(report.content), report.updated_at) || "Provenance unknown"}</p>}
          <div className="synthesis-grid">
            {TOPICS.map((topic) => <SynthesisCard key={topic} topic={topic} report={report} scope={scope} onQuote={onQuote} openSource={openSource} copySource={copySource} />)}
          </div>
        </section>
      ))}
    </article>
  );
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
        <div className="state-line"><i className={`state-dot ${synthesis.status === "current" ? "ready" : synthesis.status === "updating" ? "loading" : ""}`} /><span><strong>{String(synthesis.compact || synthesis.label || "Not synthesized")}</strong><small>{String(synthesis.detail || "Use Synthesize from a checked-out ticket branch.")}</small></span></div>
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
  repoRoot: string;
  scope: string;
  quote: Quote | null;
  onQuote: (value: Quote | null) => void;
  onReload: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [topic, setTopic] = useState<Topic>(quote?.category || "who");
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
    command<Record<string, unknown>>("coworker.state", { project: agentProject(detail), ticket: detail.ticket.id, category: topic, scope: scope === "all" ? "combined" : scope }, repoRoot)
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
      const result = await command<Record<string, unknown>>("coworker.chat", { project: agentProject(detail), ticket: detail.ticket.id, category: topic, scope: scope === "all" ? "combined" : scope, message, reference: quote }, repoRoot);
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
      await command(`coworker.proposal.${action}`, { project: agentProject(detail), ticket: detail.ticket.id, proposal: id }, repoRoot);
      notify(action === "accept" ? "Report update accepted" : "Proposal dismissed");
      onReload();
      const refreshed = await command<Record<string, unknown>>("coworker.state", { project: agentProject(detail), ticket: detail.ticket.id, category: topic, scope: scope === "all" ? "combined" : scope }, repoRoot);
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
      <nav>{TOPICS.map((value) => <button key={value} className={topic === value ? "selected" : ""} onClick={() => { setTopic(value); onQuote(null); }}>{value[0].toUpperCase() + value.slice(1)}</button>)}</nav>
      <div className="chat-messages">{messages.length === 0 && <p className="chat-empty">Ask for context, challenge an assumption, or request a report correction.</p>}{messages.map((message, index) => <article key={index} className={String(message.role) === "user" ? "user-message" : "agent-message"}>{Boolean(message.reference) && <small>Quoted context</small>}<Markdown compact>{String(message.text || "")}</Markdown></article>)}{proposals.filter((item) => item.status === "pending").map((item) => <article className="proposal" key={String(item.id)}><strong>Proposed report update</strong><p>Reviewing this accepts changes only through TicketBunker’s report gates.</p><div><button disabled={busy} onClick={() => proposal("dismiss", String(item.id))}>Dismiss</button><button className="primary" disabled={busy} onClick={() => proposal("accept", String(item.id))}>Accept</button></div></article>)}</div>
      <form className="chat-input" onSubmit={send}>{quote && <div className="quote-chip"><span>{quote.text}</span><button type="button" onClick={() => onQuote(null)}>×</button></div>}<textarea name="message" rows={2} placeholder={`Ask about ${topic}`} required /><button className="primary" disabled={busy}>Send</button>{error && <p className="inline-error">{error}</p>}</form>
    </aside>
  );
}

function SourceEditor({
  draft,
  projectRoot,
  onBack,
  onDraft,
  onQuote,
  notify,
}: {
  draft: SourceDraft;
  projectRoot: string;
  onBack: () => void;
  onDraft: (draft: SourceDraft) => void;
  onQuote: (quote: Quote) => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [content, setContent] = useState(draft.content);
  const [busy, setBusy] = useState(false);
  const editor = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    setContent(draft.content);
    const lineStart = draft.content.split("\n").slice(0, Math.max(0, draft.line - 1)).join("\n").length + (draft.line > 1 ? 1 : 0);
    requestAnimationFrame(() => {
      editor.current?.focus();
      editor.current?.setSelectionRange(lineStart, lineStart);
    });
  }, [draft]);
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
  return (
    <section className="source-editor">
      <header><button className="back-control" onClick={onBack}>← Ticket</button><div><strong>{draft.path}</strong><small>{draft.branch || "Detached"}</small></div><div><button onClick={quoteSelection}>Ask about selection</button><button className="primary" disabled={busy || content === draft.content} onClick={save}>Save</button></div></header>
      <textarea ref={editor} value={content} onChange={(event) => setContent(event.target.value)} spellCheck={false} wrap={draft.settings.line_wrap ? "soft" : "off"} style={{ tabSize: draft.settings.tab_size || 4 }} />
    </section>
  );
}

export default function TicketWorkspace({ bunker, reference, onBack, notify }: Props) {
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [error, setError] = useState("");
  const [scope, setScope] = useState("");
  const [section, setSection] = useState("description");
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
  useEffect(() => {
    if (!detail || editor) return;
    const targets = ["description", "details", "agent-work"].map((id) => document.getElementById(id)).filter((value): value is HTMLElement => Boolean(value));
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
          {[["description", "Ticket"], ["details", "Details"], ["agent-work", "Agent work"]].map(([id, label]) => <button key={id} className={section === id ? "selected" : ""} aria-current={section === id ? "true" : undefined} onClick={() => jumpTo(id)}>{label}</button>)}
        </nav>}
      </header>
      <div className="ticket-layout">
        <main className="ticket-main">
          {editor ? <SourceEditor draft={editor} projectRoot={repoRoot} onBack={() => setEditor(null)} onDraft={setEditor} onQuote={quoteSource} notify={notify} /> : <>
            <TicketTab detail={detail} trackers={trackers} openSource={openSource} copySource={copySource} />
            <div id="agent-work"><AgentTab detail={detail} scope={scope} onScope={setScope} onQuote={quoteReport} openSource={openSource} copySource={copySource} /></div>
          </>}
        </main>
        <TicketRail detail={detail} bunkerId={bunker.id} repoRoot={repoRoot} scope={scope} onScope={(value) => { setScope(value); setEditor(null); window.requestAnimationFrame(() => document.getElementById("agent-work")?.scrollIntoView({ behavior: "smooth", block: "start" })); }} onReload={load} notify={notify} />
      </div>
      <CoworkerDrawer open={chatOpen} onOpen={setChatOpen} detail={detail} repoRoot={repoRoot} scope={scope} quote={quote} onQuote={setQuote} onReload={load} notify={notify} />
    </div>
  );
}
