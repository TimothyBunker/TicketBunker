import { lazy, memo, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import IconButton from "./IconButton";
import { command } from "./transport";
import { useUnsavedEditor } from "./UnsavedChanges";
import { CoworkerDrawer, type Quote } from "./TicketWorkspace";
import Markdown, { SourceActionsContext } from "./Markdown";
import { sourceNotes, verifiedStamp } from "./report";
import type { Bunker, Repository, SourceDraft, TicketDetail, TicketPage, TicketReference } from "./types";
import type { CodeSelection } from "./WorkspaceEditor";
import "./repository-workspace.css";

const CodeEditor = lazy(() => import("./WorkspaceEditor"));
type Tab = SourceDraft & { saved: string; changed?: boolean };
type ViewState = { tabs: Tab[]; active: string; overlays: string[]; conversation: string; bunkerId: string; expanded?: string[] };
type TreeEntry = { name: string; path: string; directory: boolean };
export type WorkspaceTarget = { bunkerId: string; repositoryId: string; path?: string; line?: number; ticket?: TicketReference; nonce?: number };
type Props = { bunker: Bunker; repository: Repository; visible: boolean; target?: WorkspaceTarget | null;
  onTicket: (ticket: TicketReference) => void; notify: (message: string, error?: boolean) => void };

export default function RepositoryWorkspace({ bunker, repository, visible, target, onTicket, notify }: Props) {
  const payload = useMemo(() => ({ bunker_id: bunker.id, repository_id: repository.id, expected_root: repository.local_path || "" }), [bunker.id, repository.id, repository.local_path]);
  const paneId = useId();
  const [view, setView] = useState<ViewState>({ tabs: [], active: "", overlays: [], conversation: "", bunkerId: bunker.id });
  const [ready, setReady] = useState(false);
  const [branch, setBranch] = useState("");
  const [head, setHead] = useState("");
  const [root, setRoot] = useState(repository.local_path || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [treeVersion, setTreeVersion] = useState(0);
  const [treeOpen, setTreeOpen] = useState(true);
  const [contextOpen, setContextOpen] = useState(true);
  const [pane, setPane] = useState<"comments" | "session">("comments");
  const [details, setDetails] = useState<Record<string, TicketDetail>>({});
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [tickets, setTickets] = useState<TicketReference[]>([]);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [selection, setSelection] = useState<CodeSelection | null>(null);
  const [closing, setClosing] = useState("");
  const closeDialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!closing) return;
    const focused = document.activeElement as HTMLElement | null;
    if (closeDialog.current?.showModal) closeDialog.current.showModal();
    else closeDialog.current?.setAttribute("open", "");
    return () => focused?.focus?.();
  }, [closing]);
  const [fileQuery, setFileQuery] = useState("");
  const [foundFiles, setFoundFiles] = useState<string[]>([]);
  const [jump, setJump] = useState<{ line: number; sequence: number }>();
  const revision = useRef(0);
  const serial = useRef(Promise.resolve());
  const latest = useRef(view);
  latest.current = view;
  const targetSeen = useRef<number | undefined>(undefined);
  const file = view.tabs.find((tab) => tab.path === view.active);
  const activeTicket = details[view.conversation];
  const cleanFile = Boolean(file && !file.changed && file.content === file.saved);
  // Parsing reports is independent of typing and cursor movement.
  const fileNotes = useMemo(() => view.overlays.flatMap((id) => (details[id]?.reports || [])
    .filter((report) => report.repository_id === repository.id && report.scope.startsWith("branch:"))
    .flatMap((report) => file?.path ? sourceNotes(report.content, file.path).map((note) => ({ id, report, note, stamp: verifiedStamp(report.content) })) : [])),
  [view.overlays, details, repository.id, file?.path]);
  const currentNotes = useMemo(() => cleanFile ? fileNotes.filter(({ stamp }) => stamp?.branch === branch && Boolean(stamp.commit) && head.startsWith(stamp.commit))
    .map(({ id, note }) => ({ ...note, text: `${id}: ${note.text}` })) : [], [fileNotes, cleanFile, branch, head]);
  function persist() {
    const task = serial.current.then(async () => {
      const result = await command<{ revision: number }>("workspace.save", { ...payload, revision: revision.current, state: latest.current });
      revision.current = result.revision;
    });
    serial.current = task.catch(() => {});
    return task;
  }

  async function refresh(initial = false, reloadTree = false) {
    try {
      const result = await command<{ root: string; branch: string; head: string; revision: number; state: Partial<ViewState> }>("workspace.open", payload);
      setRoot(result.root); setBranch(result.branch); setHead(result.head);
      if (initial) {
        revision.current = result.revision;
        const restored = result.state;
        setView({ tabs: restored.tabs || [], active: restored.active || "", overlays: restored.bunkerId === bunker.id ? restored.overlays || [] : [], conversation: restored.bunkerId === bunker.id ? restored.conversation || "" : "", bunkerId: bunker.id, expanded: restored.expanded || [] });
        setReady(true);
      }
      // Clean buffers follow external changes; dirty buffers stay untouched.
      for (const tab of initial ? result.state.tabs || [] : latest.current.tabs) {
        try {
          const disk = await command<SourceDraft>("workspace.file.read", { ...payload, path: tab.path });
          setView((old) => ({ ...old, tabs: old.tabs.map((current) => current.path !== tab.path ? current : current.content === current.saved
            ? { ...disk, line: current.line, saved: disk.content }
            : { ...current, changed: disk.digest !== current.digest || disk.branch !== current.branch }) }));
        } catch { setView((old) => ({ ...old, tabs: old.tabs.map((current) => current.path === tab.path ? { ...current, changed: true } : current) })); }
      }
      if (initial || reloadTree) setTreeVersion((value) => value + 1);
    } catch (reason) { setError(String(reason)); }
  }
  useEffect(() => { void refresh(true); }, []);
  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => { command<{ paths: string[] }>("workspace.find", { ...payload, query: fileQuery }).then((result) => { if (!cancelled) setFoundFiles(result.paths); }).catch((reason) => !cancelled && setError(String(reason))); }, 200);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [fileQuery]);
  useEffect(() => {
    if (!visible || !ready) return;
    void refresh();
    const focus = () => { if (!document.hidden) void refresh(); };
    window.addEventListener("focus", focus);
    return () => window.removeEventListener("focus", focus);
  }, [visible, ready]);
  useEffect(() => {
    if (!ready) return;
    const timer = window.setTimeout(() => {
      void persist().catch((reason) => setError(`Draft recovery was not updated. ${String(reason)}`));
    }, 350);
    return () => window.clearTimeout(timer);
  }, [view, ready]);

  const openFile = useCallback(async (path: string, line = 1) => {
    setJump({ line, sequence: Date.now() });
    const existing = latest.current.tabs.find((tab) => tab.path === path);
    setSelection(null);
    if (existing) { setView((old) => ({ ...old, active: path, tabs: old.tabs.map((tab) => tab.path === path ? { ...tab, line } : tab) })); return; }
    if (latest.current.tabs.length >= 30) return setError("Close a file tab before opening another (30 open files).");
    try {
      const draft = await command<SourceDraft>("workspace.file.read", { ...payload, path, line });
      setView((old) => ({ ...old, active: draft.path, tabs: old.tabs.some((tab) => tab.path === draft.path) ? old.tabs : [...old.tabs, { ...draft, saved: draft.content }] }));
      setSelection(null);
    } catch (reason) { setError(String(reason)); }
  }, [payload]);
  const sourceActions = useMemo(() => ({ open: openFile }), [openFile]);
  const expanded = useMemo(() => view.expanded || [], [view.expanded]);
  const toggleDirectory = useCallback((path: string) => setView((old) => ({ ...old, expanded: old.expanded?.includes(path) ? old.expanded.filter((value) => value !== path) : [...old.expanded || [], path] })), []);
  useEffect(() => {
    if (!ready || !target || target.repositoryId !== repository.id || target.nonce === targetSeen.current) return;
    targetSeen.current = target.nonce;
    if (target.ticket) addOverlay(target.ticket.id);
    if (target.path) void openFile(target.path, target.line);
  }, [target, ready]);
  async function loadOverlay(id: string) {
    try {
      const detail = await command<TicketDetail>("application.ticket.get", { bunker_id: bunker.id, ticket_id: id, repository_id: repository.id });
      if (!detail.repositories.some((repo) => repo.id === repository.id)) throw new Error("This ticket does not use this repository.");
      setDetails((old) => ({ ...old, [id]: detail }));
    } catch (reason) { setError(String(reason)); }
  }
  function addOverlay(id: string) {
    setView((old) => ({ ...old, overlays: [...new Set([...old.overlays, id])], conversation: id }));
    setAdding(false); setQuote(null);
  }
  useEffect(() => { view.overlays.forEach((id) => { void loadOverlay(id); }); }, [view.overlays.join("|"), branch]);
  useEffect(() => {
    if (!adding) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      command<TicketPage>("application.ticket.list", { bunker_id: bunker.id, project_id: repository.project_id, search: query, page: 1, page_size: 25 })
        .then((value) => { if (!cancelled) setTickets(value.tickets); }).catch((reason) => !cancelled && setError(String(reason)));
    }, 180);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [adding, query]);

  async function save(tab: Tab): Promise<boolean> {
    setBusy(true); setError("");
    try {
      const result = await command<{ content: string; digest: string }>("workspace.file.save", { ...payload, path: tab.path, branch: tab.branch, content: tab.content, expected_digest: tab.digest });
      const updated = { ...latest.current, tabs: latest.current.tabs.map((current) => current.path === tab.path ? { ...current, saved: result.content, content: current.content === tab.content ? result.content : current.content, digest: result.digest, changed: false } : current) };
      latest.current = updated; setView(updated);
      await persist();
      return true;
    } catch (reason) { setError(String(reason)); return false; }
    finally { setBusy(false); }
  }
  useUnsavedEditor({ retained: true, dirty: view.tabs.some((tab) => tab.content !== tab.saved), busy,
    description: `Save unsaved files in ${repository.name}?`,
    save: async () => { for (const tab of latest.current.tabs.filter((tab) => tab.content !== tab.saved)) if (!await save(tab)) return false; return true; },
    discard: async () => { const clean = { ...latest.current, tabs: latest.current.tabs.map((tab) => ({ ...tab, content: tab.saved })) }; latest.current = clean; setView(clean); await persist(); } });
  function closeFile(path: string) {
    setView((old) => { const tabs = old.tabs.filter((tab) => tab.path !== path); return { ...old, tabs, active: old.active === path ? tabs.at(-1)?.path || "" : old.active }; });
    setClosing("");
  }
  function discuss(reference: CodeSelection) {
    if (!file || !activeTicket) return;
    setQuote({ kind: "source", category: "file", scope: branch || "combined", branch, path: file.path, ...reference, unsaved: file.content !== file.saved });
    setPane("session"); setContextOpen(true);
  }

  const contextPane = useMemo(() => (<aside id={paneId + "-context"} className="workspace-context" hidden={!contextOpen}><nav aria-label="File context"><button aria-pressed={pane === "comments"} onClick={() => setPane("comments")}>Comments</button><button aria-pressed={pane === "session"} onClick={() => setPane("session")}>Session</button><IconButton icon="panel-right" label="Hide ticket context" aria-expanded={true} aria-controls={paneId + "-context"} onClick={() => setContextOpen(false)} /></nav>
        <label>Conversation<select aria-label="Active ticket session" value={view.conversation} onChange={(event) => { setView((old) => ({ ...old, conversation: event.target.value })); setQuote(null); }}><option value="">No ticket</option>{view.overlays.map((id) => <option key={id}>{id}</option>)}</select></label>
        <SourceActionsContext.Provider value={sourceActions}>
        <div className="workspace-comments" hidden={pane !== "comments"}>{!view.overlays.length && <p>Add a ticket overlay to see its file notes.</p>}{view.overlays.map((id) => <section key={id}><strong>{id}</strong>{fileNotes.filter((item) => item.id === id).map(({ report, note, stamp }, index) => {
          const current = stamp?.branch === branch && Boolean(stamp?.commit) && head.startsWith(stamp!.commit) && cleanFile;
          return <article key={`${report.scope}:${index}`}><small>{report.scope.slice(7)} · lines {note.line}–{note.endLine}{current ? "" : " · Historical report reference; verify against current code"}</small><Markdown compact>{note.text}</Markdown><button onClick={() => { setView((old) => ({ ...old, conversation: id })); setQuote({ kind: "synthesis", category: "file", scope: report.scope.slice(7), branch: stamp?.branch || "", path: file!.path, line_start: note.line, line_end: note.endLine, text: note.text }); setPane("session"); }}>Discuss</button></article>;
        })}</section>)}</div>
        {view.overlays.map((id) => details[id] && <div className="workspace-session" key={id} hidden={pane !== "session" || id !== view.conversation}><CoworkerDrawer embedded open={visible && contextOpen && pane === "session" && id === view.conversation} onOpen={() => {}} detail={details[id]} bunkerId={bunker.id} repoRoot={root} scope={branch || "all"} quote={id === view.conversation ? quote : null} onQuote={(value) => { if (id === latest.current.conversation) setQuote(value); }} pending={null} onPendingSent={() => {}} openFile={file?.path || ""} onReload={() => loadOverlay(id)} notify={notify} /></div>)}
        {pane === "session" && !activeTicket && <p>Choose a ticket session before sending a message.</p>}
        </SourceActionsContext.Provider>
      </aside>), [paneId, contextOpen, pane, view.conversation, view.overlays, activeTicket, details, fileNotes, cleanFile, branch, head, file?.path, visible, bunker.id, root, quote, notify, sourceActions]);

  return <section className="repository-workspace" aria-label={`${repository.name} workspace`}>
    <header className="repository-workspace-header"><strong title={root}>{repository.name}</strong><span title="Actual checked-out branch">⎇ {branch || "Detached HEAD"}</span>
      <IconButton icon="sync" label="Refresh workspace" hint="Reload Git state, file tree and unchanged files without discarding drafts" onClick={() => refresh(false, true)} />
      <div className="overlay-chips">{view.overlays.map((id) => <span key={id} className={id === view.conversation ? "active" : ""}>
        <button title={`Open ticket ${id}`} onClick={() => details[id] && onTicket(details[id].ticket as TicketReference)}>{id}</button>
        <button aria-label={`Hide ${id} overlay`} onClick={() => { setView((old) => ({ ...old, overlays: old.overlays.filter((value) => value !== id), conversation: old.conversation === id ? old.overlays.find((value) => value !== id) || "" : old.conversation })); setQuote(null); }}>×</button>
      </span>)}<button aria-label="Add ticket overlay" onClick={() => setAdding(!adding)}>+</button></div>
    </header>
    {adding && <div className="overlay-picker"><input aria-label="Find ticket overlay" placeholder="Find ticket" value={query} onChange={(event) => setQuery(event.target.value)} />{tickets.map((ticket) => <button key={ticket.id} onClick={() => addOverlay(ticket.id)}>{ticket.id} {ticket.title}</button>)}</div>}
    {error && <div className="inline-error" role="alert">{error}<button aria-label="Dismiss workspace error" onClick={() => setError("")}>×</button></div>}
    {!ready ? <p>Loading workspace…</p> : <div className="repository-workspace-body">
      {!treeOpen && <div className="workspace-drawer-rail"><IconButton icon="panel-left" label="Show explorer" aria-expanded={false} aria-controls={paneId + "-explorer"} onClick={() => setTreeOpen(true)} /></div>}
      <nav id={paneId + "-explorer"} hidden={!treeOpen} className="workspace-tree" aria-label="Repository files">
        <div className="workspace-pane-header"><span>Explorer</span><IconButton icon="panel-left" label="Hide explorer" aria-expanded={true} aria-controls={paneId + "-explorer"} onClick={() => setTreeOpen(false)} /></div>
        <form onSubmit={(event) => { event.preventDefault(); void openFile(fileQuery); }}><input value={fileQuery} onChange={(event) => setFileQuery(event.target.value)} name="path" aria-label="Find file or open path" placeholder="Find file or path…" /></form>
        {fileQuery ? <ul>{foundFiles.map((path) => <li key={path}><button onClick={() => { void openFile(path); setFileQuery(""); }}>{path}</button></li>)}</ul> : <Directory key={treeVersion} payload={payload} path="" onOpen={openFile} expanded={expanded} toggle={toggleDirectory} />}
      </nav>
      <main className="workspace-file-area"><div className="workspace-file-tabs" role="tablist" aria-label="Open files">{view.tabs.map((tab) => <div key={tab.path}><button role="tab" aria-selected={view.active === tab.path} title={tab.path} onClick={() => { setView((old) => ({ ...old, active: tab.path })); setSelection(null); }}>{tab.path.split("/").pop()}{tab.content !== tab.saved ? " •" : ""}</button><button aria-label={`Close ${tab.path}`} onClick={() => tab.content !== tab.saved ? setClosing(tab.path) : closeFile(tab.path)}>×</button></div>)}</div>
        {file ? <><div className="workspace-file-tools"><span title={file.path}>{file.path}</span>{file.changed && <span role="status">Changed on disk or branch</span>}<button disabled={busy || file.content === file.saved} onClick={() => save(file)}>Save</button>{selection && <button disabled={!activeTicket} title={activeTicket ? `Send selection to ${view.conversation}` : "Add a ticket overlay to discuss code"} onClick={() => discuss(selection)}>Discuss selection</button>}</div>
          <Suspense fallback={<p>Loading editor…</p>}><CodeEditor path={file.path} value={file.content} line={file.line} jump={jump} notes={currentNotes} tabSize={file.settings.tab_size} tabs={file.settings.indent_style === "tabs"} wrap={file.settings.line_wrap} onSave={() => { if (!busy) void save(file); }}
            onChange={(content) => setView((old) => ({ ...old, tabs: old.tabs.map((tab) => tab.path === old.active ? { ...tab, content } : tab) }))}
            onLine={(line) => setView((old) => old.tabs.find((tab) => tab.path === old.active)?.line === line ? old : ({ ...old, tabs: old.tabs.map((tab) => tab.path === old.active ? { ...tab, line } : tab) }))} onSelection={setSelection} /></Suspense></> : <p className="quiet-empty">Open a file from this repository.</p>}
      </main>
      {contextPane}
      {!contextOpen && <div className="workspace-drawer-rail right"><IconButton icon="panel-right" label="Show ticket context" aria-expanded={false} aria-controls={paneId + "-context"} onClick={() => setContextOpen(true)} /></div>}
    </div>}
    {closing && <dialog ref={closeDialog} className="workspace-close-dialog" aria-label="Close file" onCancel={(event) => { event.preventDefault(); if (!busy) setClosing(""); }}><h2>You have unsaved changes</h2><p>{closing}</p><button disabled={busy} onClick={() => setClosing("")}>Cancel</button><button disabled={busy} onClick={() => closeFile(closing)}>Do not save</button><button disabled={busy} onClick={async () => { const tab = view.tabs.find((tab) => tab.path === closing); if (tab && await save(tab)) closeFile(closing); }}>Save</button></dialog>}
  </section>;
}

const Directory = memo(function DirectoryRows({ payload, path, onOpen, expanded, toggle }: { payload: Record<string, string>; path: string; onOpen: (path: string) => void; expanded: string[]; toggle: (path: string) => void }) {
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [message, setMessage] = useState("");
  useEffect(() => { let active = true; command<{ entries: TreeEntry[]; truncated: boolean }>("workspace.tree", { ...payload, path }).then((result) => { if (active) { setEntries(result.entries); setMessage(result.truncated ? "First 1,000 entries shown. Open a file by path if it is not listed." : ""); } }).catch((reason) => active && setMessage(String(reason))); return () => { active = false; }; }, [path]);
  return <ul>{entries.map((entry) => <li key={entry.path}><button title={entry.path} aria-expanded={entry.directory ? expanded.includes(entry.path) : undefined} onClick={() => entry.directory ? toggle(entry.path) : onOpen(entry.path)}>{entry.directory ? expanded.includes(entry.path) ? "▾ " : "▸ " : ""}{entry.name}</button>{entry.directory && expanded.includes(entry.path) && <Directory payload={payload} path={entry.path} onOpen={onOpen} expanded={expanded} toggle={toggle} />}</li>)}{message && <li role="status">{message}</li>}</ul>;
});
