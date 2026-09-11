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
const sameCheckout = (left: SourceDraft, right: SourceDraft) => left.branch === right.branch
  && (Boolean(left.branch) || Boolean(left.head) && left.head === right.head);
function validRescue(value: unknown): value is Partial<ViewState> & { tabs: Tab[] } {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const state = value as Record<string, unknown>;
  if (!Array.isArray(state.tabs) || state.tabs.length > 30) return false;
  if (!state.tabs.every((tab) => tab && typeof tab === "object" && !Array.isArray(tab)
    && ["path", "content", "saved", "branch", "digest"].every((key) => typeof tab[key] === "string")
    && Number.isInteger(tab.line) && tab.line >= 1 && tab.settings && typeof tab.settings === "object" && !Array.isArray(tab.settings))) return false;
  const paths = state.tabs.map((tab) => tab.path.replace(/\\/g, "/").split("/").filter((part: string) => part && part !== ".").join("/"));
  if (paths.some((path) => !path || path.split("/").includes("..")) || new Set(paths).size !== paths.length) return false;
  if (!["active", "conversation", "bunkerId"].every((key) => state[key] === undefined || typeof state[key] === "string")) return false;
  return ["overlays", "expanded"].every((key) => state[key] === undefined || Array.isArray(state[key]) && state[key].every((entry) => typeof entry === "string"))
    && (!Array.isArray(state.overlays) || state.overlays.length <= 32);
}
export type WorkspaceTarget = { bunkerId: string; repositoryId: string; path?: string; line?: number; ticket?: TicketReference; nonce?: number };
type Props = { bunker: Bunker; repository: Repository; visible: boolean; target?: WorkspaceTarget | null;
  onTicket: (ticket: TicketReference) => void; notify: (message: string, error?: boolean) => void };

export default function RepositoryWorkspace({ bunker, repository, visible, target, onTicket, notify }: Props) {
  const payload = useMemo(() => ({ bunker_id: bunker.id, repository_id: repository.id, expected_root: repository.local_path || "" }), [bunker.id, repository.id, repository.local_path]);
  const paneId = useId();
  const drawerFocus = useRef<string | null>(null);
  const rescueKey = `tb.workspace.rescue:${JSON.stringify(payload)}`;
  const unreadableRescue = useRef(false);
  const [view, setView] = useState<ViewState>({ tabs: [], active: "", overlays: [], conversation: "", bunkerId: bunker.id });
  const [ready, setReady] = useState(false);
  const [branch, setBranch] = useState("");
  const [head, setHead] = useState("");
  const checkout = useRef({ branch: "", head: "" });
  const [root, setRoot] = useState(repository.local_path || "");
  const [error, setError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const [busy, setBusy] = useState(false);
  const [writing, setWriting] = useState(false);
  const writeInFlight = useRef(false);
  const saveOperations = useRef(0);
  const [treeVersion, setTreeVersion] = useState(0);
  const [treeOpen, setTreeOpen] = useState(true);
  const [contextOpen, setContextOpen] = useState(true);
  useEffect(() => {
    if (drawerFocus.current) document.getElementById(drawerFocus.current)?.focus();
    drawerFocus.current = null;
  }, [treeOpen, contextOpen]);
  function toggleDrawer(side: "explorer" | "context", open: boolean) {
    drawerFocus.current = `${paneId}-${side}-${open ? "hide" : "show"}`;
    if (side === "explorer") setTreeOpen(open); else setContextOpen(open);
  }
  const [pane, setPane] = useState<"comments" | "session">("comments");
  const [details, setDetails] = useState<Record<string, TicketDetail>>({});
  const [contextVersion, setContextVersion] = useState(0);
  const overlayRequests = useRef(new Map<string, number>());
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
  const refreshGeneration = useRef(0);
  const fileEpochs = useRef(new Map<string, number>());
  const pendingFiles = useRef(new Map<string, Promise<SourceDraft>>());
  const openIntent = useRef(0);
  const bumpFile = (path: string) => fileEpochs.current.set(path, (fileEpochs.current.get(path) || 0) + 1);
  const latest = useRef(view);
  latest.current = view;
  const targetSeen = useRef<number | undefined>(undefined);
  const file = view.tabs.find((tab) => tab.path === view.active);
  const activeTicket = details[view.conversation];
  const cleanFile = Boolean(file && !file.changed && file.content === file.saved);
  // Parsing reports is independent of typing and cursor movement.
  const fileNotes = useMemo(() => view.overlays.flatMap((id) => (details[id]?.reports || [])
    .filter((report) => report.repository_id === repository.id && report.scope.startsWith("branch:"))
    .flatMap((report) => {
      if (!file?.path) return [];
      const stamp = verifiedStamp(report.content);
      return sourceNotes(report.content, file.path, root).map((note) => ({ id, report, note, stamp }));
    })),
  [view.overlays, details, repository.id, file?.path, root]);
  const currentNotes = useMemo(() => cleanFile ? fileNotes.filter(({ stamp }) => stamp?.branch === branch && Boolean(stamp.commit) && head.startsWith(stamp.commit))
    .map(({ id, note }) => ({ ...note, text: `${id}: ${note.text}` })) : [], [fileNotes, cleanFile, branch, head]);
  function rescueDrafts() {
    try {
      if (unreadableRescue.current) return; // Preserve malformed raw recovery; never silently overwrite it.
      if (latest.current.tabs.some((tab) => tab.content !== tab.saved)) sessionStorage.setItem(rescueKey, JSON.stringify({ state: latest.current, revision: revision.current }));
      else sessionStorage.removeItem(rescueKey);
    } catch { setError("This window's reload recovery could not be stored. Save your files or copy your unsaved text before reloading or closing."); }
  }
  function persist() {
    const task = serial.current.then(async () => {
      const result = await command<{ revision: number }>("workspace.save", { ...payload, revision: revision.current, state: latest.current });
      revision.current = result.revision;
    });
    serial.current = task.catch(() => {});
    return task;
  }

  async function refresh(initial = false, reloadTree = false) {
    initial ||= !ready;
    const generation = ++refreshGeneration.current;
    setRefreshError("");
    try {
      const result = await command<{ root: string; branch: string; head: string; revision: number; state: Partial<ViewState> }>("workspace.open", payload);
      if (generation !== refreshGeneration.current) return;
      checkout.current = { branch: result.branch, head: result.head };
      setRoot(result.root); setBranch(result.branch); setHead(result.head);
      if (initial) {
        revision.current = result.revision;
        let restored = result.state;
        try {
          const stored = JSON.parse(sessionStorage.getItem(rescueKey) || "null") as { state?: ViewState; revision?: number } & Partial<ViewState> | null;
          const rescue = stored?.state || stored;
          if (rescue && !validRescue(rescue)) {
            unreadableRescue.current = true;
            setError("Local reload recovery could not be restored. Showing saved workspace recovery. Save your files or copy drafts before closing; the unreadable reload copy has been kept.");
          } else if (rescue?.bunkerId === bunker.id && rescue.tabs.some((tab) => tab.content !== tab.saved)) {
            restored = rescue;
            // Reload must not silently adopt another window's revision and
            // overwrite its durable recovery with this window's older draft.
            // Older state-only rescue entries conservatively start at zero.
            const rescuedRevision = stored?.revision;
            revision.current = typeof rescuedRevision === "number" && Number.isSafeInteger(rescuedRevision) && rescuedRevision >= 0 ? rescuedRevision : 0;
          }
        } catch {
          unreadableRescue.current = true;
          setError("Local reload recovery could not be read. Showing saved workspace recovery. Save your files or copy drafts before closing.");
        }
        setView({ tabs: restored.tabs || [], active: restored.active || "", overlays: restored.bunkerId === bunker.id ? restored.overlays || [] : [], conversation: restored.bunkerId === bunker.id ? restored.conversation || "" : "", bunkerId: bunker.id, expanded: restored.expanded || [] });
        setReady(true);
      }
      // Clean buffers follow external changes; dirty buffers stay untouched.
      for (const tab of initial ? result.state.tabs || [] : latest.current.tabs) {
        const epoch = fileEpochs.current.get(tab.path) || 0;
        try {
          const disk = await command<SourceDraft>("workspace.file.read", { ...payload, path: tab.path });
          if (generation !== refreshGeneration.current || epoch !== (fileEpochs.current.get(tab.path) || 0)) continue;
          setView((old) => ({ ...old, tabs: old.tabs.map((current) => current.path !== tab.path || current.digest !== tab.digest || current.branch !== tab.branch ? current : current.content === current.saved
            ? { ...disk, line: current.line, saved: disk.content }
            : sameCheckout(disk, current) && disk.content === current.content
              ? { ...disk, line: current.line, saved: disk.content }
            : { ...current, changed: disk.digest !== current.digest || !sameCheckout(disk, current) }) }));
        } catch { if (generation === refreshGeneration.current && epoch === (fileEpochs.current.get(tab.path) || 0)) setView((old) => ({ ...old, tabs: old.tabs.map((current) => current.path === tab.path ? { ...current, changed: true } : current) })); }
      }
      if (generation === refreshGeneration.current && (initial || reloadTree)) {
        setTreeVersion((value) => value + 1);
        if (!initial) setContextVersion((value) => value + 1);
      }
    } catch (reason) { if (generation === refreshGeneration.current) setRefreshError(String(reason)); }
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
    const flush = () => rescueDrafts();
    window.addEventListener("beforeunload", flush);
    return () => {
      window.removeEventListener("beforeunload", flush);
      // Binding/configuration updates may unmount this view before the
      // recovery debounce fires, without a browser unload event.
      rescueDrafts();
    };
  }, [ready]);
  useEffect(() => {
    if (!ready) return;
    const timer = window.setTimeout(() => {
      rescueDrafts();
      void persist().catch((reason) => setError(`Draft recovery was not updated. ${String(reason)} Save your files or copy your drafts before closing this window.`));
    }, 350);
    return () => window.clearTimeout(timer);
  }, [view, ready]);

  const openFile = useCallback(async (path: string, line = 1) => {
    const existing = latest.current.tabs.find((tab) => tab.path === path);
    if (!existing && !pendingFiles.current.has(path) && latest.current.tabs.length + pendingFiles.current.size >= 30) return setError("Close a file tab before opening another (30 open files).");
    const intent = ++openIntent.current;
    if (existing) { setJump({ line, sequence: intent }); setSelection(null); setView((old) => ({ ...old, active: path, tabs: old.tabs.map((tab) => tab.path === path ? { ...tab, line } : tab) })); return; }
    try {
      let read = pendingFiles.current.get(path);
      if (!read) { read = command<SourceDraft>("workspace.file.read", { ...payload, path, line }); pendingFiles.current.set(path, read); }
      const draft = await read;
      bumpFile(draft.path);
      if (intent === openIntent.current) { setJump({ line, sequence: intent }); setSelection(null); }
      setView((old) => {
        const present = old.tabs.some((tab) => tab.path === draft.path);
        if (!present && old.tabs.length >= 30) return old;
        const changed = draft.branch !== checkout.current.branch || Boolean(draft.head && checkout.current.head && draft.head !== checkout.current.head);
        return { ...old, active: intent === openIntent.current ? draft.path : old.active, tabs: present ? old.tabs : [...old.tabs, { ...draft, line, saved: draft.content, changed }] };
      });
    } catch (reason) { setError(`${path}: ${String(reason)}`); }
    finally { pendingFiles.current.delete(path); }
  }, [payload]);
  const sourceActions = useMemo(() => ({ open: openFile }), [openFile]);
  const expanded = useMemo(() => view.expanded || [], [view.expanded]);
  const toggleDirectory = useCallback((path: string) => setView((old) => ({ ...old, expanded: old.expanded?.includes(path) ? old.expanded.filter((value) => value !== path) : [...old.expanded || [], path] })), []);
  useEffect(() => {
    if (!ready || !target || target.bunkerId !== bunker.id || target.repositoryId !== repository.id || target.nonce === targetSeen.current) return;
    targetSeen.current = target.nonce;
    if (target.ticket) addOverlay(target.ticket.id);
    if (target.path) void openFile(target.path, target.line);
  }, [target, ready]);
  async function loadOverlay(id: string) {
    const version = (overlayRequests.current.get(id) || 0) + 1;
    overlayRequests.current.set(id, version);
    const current = () => overlayRequests.current.get(id) === version && latest.current.overlays.includes(id);
    try {
      const detail = await command<TicketDetail>("application.ticket.get", { bunker_id: bunker.id, ticket_id: id, repository_id: repository.id });
      if (!current()) return;
      if (!detail.repositories.some((repo) => repo.id === repository.id)) throw new Error("This ticket does not use this repository.");
      setDetails((old) => ({ ...old, [id]: detail }));
      setError((old) => old.startsWith(`${id}: `) ? "" : old);
    } catch (reason) {
      if (!current()) return;
      setDetails((old) => { const copy = { ...old }; delete copy[id]; return copy; });
      setError(`${id}: ${String(reason)}`);
    }
  }
  function addOverlay(id: string) {
    if (!latest.current.overlays.includes(id) && latest.current.overlays.length >= 32) {
      setError("Hide a ticket overlay before adding another (32 ticket overlays).");
      return;
    }
    setView((old) => old.overlays.length >= 32 && !old.overlays.includes(id) ? old : ({ ...old, overlays: [...new Set([...old.overlays, id])], conversation: id }));
    setAdding(false); setQuote(null);
  }
  useEffect(() => {
    view.overlays.forEach((id) => { void loadOverlay(id); });
    return () => { overlayRequests.current.forEach((version, id) => overlayRequests.current.set(id, version + 1)); };
  }, [view.overlays.join("|"), branch, contextVersion]);
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
    if (writeInFlight.current) {
      setError("A file save is in progress. Any newer edits remain unsaved; wait for it to finish, then press Save again.");
      return false;
    }
    if (tab.content === tab.saved) return true;
    writeInFlight.current = true; setWriting(true);
    saveOperations.current += 1;
    bumpFile(tab.path);
    setBusy(true); setError("");
    try {
      try {
        const result = await command<{ content: string; digest: string }>("workspace.file.save", { ...payload, path: tab.path, branch: tab.branch, head: tab.head, content: tab.content, expected_digest: tab.digest });
        bumpFile(tab.path);
        const updated = { ...latest.current, tabs: latest.current.tabs.map((current) => current.path === tab.path ? { ...current, saved: result.content, content: current.content === tab.content ? result.content : current.content, digest: result.digest, changed: false } : current) };
        latest.current = updated; setView(updated);
        rescueDrafts();
      } finally {
        // Recovery receipts must not block another explicit source save. Keep
        // the close guard busy until all operations (including recovery) settle.
        writeInFlight.current = false; setWriting(false);
      }
      try { await persist(); }
      catch (reason) { setError(`Saved ${tab.path}, but workspace recovery was not updated. Save any remaining files or copy unsaved text before closing. ${String(reason)}`); }
      return true;
    } catch (reason) { setError(String(reason)); return false; }
    finally { saveOperations.current -= 1; setBusy(saveOperations.current > 0); }
  }
  useUnsavedEditor({ retained: true, dirty: view.tabs.some((tab) => tab.content !== tab.saved), busy,
    description: `Save unsaved files in ${repository.name}?`,
    save: async () => { for (const tab of latest.current.tabs.filter((tab) => tab.content !== tab.saved)) if (!await save(tab)) return false; return true; },
    discard: async () => {
      const clean = { ...latest.current, tabs: latest.current.tabs.map((tab) => ({ ...tab, content: tab.saved })) };
      latest.current = clean; setView(clean); rescueDrafts();
      // The user explicitly discarded this window's drafts. A conflicting
      // recovery owned by another window must not prevent closing this one.
      try { await persist(); }
      catch (reason) { setError(`Unsaved changes were discarded; shared workspace recovery was not changed. ${String(reason)}`); }
    } });
  function closeFile(path: string) {
    bumpFile(path);
    openIntent.current++;
    setView((old) => { const tabs = old.tabs.filter((tab) => tab.path !== path); return { ...old, tabs, active: old.active === path ? tabs.at(-1)?.path || "" : old.active }; });
    setClosing("");
  }
  function discuss(reference: CodeSelection) {
    if (!file || !activeTicket) return;
    setQuote({ kind: "source", category: "file", scope: branch || "combined", branch, path: file.path, ...reference, unsaved: file.content !== file.saved });
    setPane("session"); setContextOpen(true);
  }

  const contextPane = useMemo(() => (<aside id={paneId + "-context"} className="workspace-context" hidden={!contextOpen}><nav aria-label="File context"><button aria-pressed={pane === "comments"} onClick={() => setPane("comments")}>Comments</button><button aria-pressed={pane === "session"} onClick={() => setPane("session")}>Session</button><IconButton id={paneId + "-context-hide"} icon="panel-right" label="Hide ticket context" aria-expanded={true} aria-controls={paneId + "-context"} onClick={() => toggleDrawer("context", false)} /></nav>
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
    {(error || refreshError) && <div className="inline-error" role="alert">{[error, refreshError].filter(Boolean).join(" ")}<button aria-label="Dismiss workspace error" onClick={() => { setError(""); setRefreshError(""); }}>×</button></div>}
    {!ready ? <p>Loading workspace…</p> : <div className="repository-workspace-body">
      {!treeOpen && <div className="workspace-drawer-rail"><IconButton id={paneId + "-explorer-show"} icon="panel-left" label="Show explorer" aria-expanded={false} aria-controls={paneId + "-explorer"} onClick={() => toggleDrawer("explorer", true)} /></div>}
      <nav id={paneId + "-explorer"} hidden={!treeOpen} className="workspace-tree" aria-label="Repository files">
        <div className="workspace-pane-header"><span>Explorer</span><IconButton id={paneId + "-explorer-hide"} icon="panel-left" label="Hide explorer" aria-expanded={true} aria-controls={paneId + "-explorer"} onClick={() => toggleDrawer("explorer", false)} /></div>
        <form onSubmit={(event) => { event.preventDefault(); void openFile(fileQuery); }}><input value={fileQuery} onChange={(event) => setFileQuery(event.target.value)} name="path" aria-label="Find file or open path" placeholder="Find file or path…" /></form>
        {fileQuery ? <ul>{foundFiles.map((path) => <li key={path}><button onClick={() => { void openFile(path); setFileQuery(""); }}>{path}</button></li>)}</ul> : <Directory key={treeVersion} payload={payload} path="" onOpen={openFile} expanded={expanded} toggle={toggleDirectory} />}
      </nav>
      <main className="workspace-file-area"><div className="workspace-file-tabs" role="tablist" aria-label="Open files">{view.tabs.map((tab) => <div key={tab.path}><button role="tab" aria-selected={view.active === tab.path} title={tab.path} onClick={() => { openIntent.current++; setView((old) => ({ ...old, active: tab.path })); setSelection(null); }}>{tab.path.split("/").pop()}{tab.content !== tab.saved ? " •" : ""}</button><button aria-label={`Close ${tab.path}`} onClick={() => tab.content !== tab.saved ? setClosing(tab.path) : closeFile(tab.path)}>×</button></div>)}</div>
        {file ? <><div className="workspace-file-tools"><span title={file.path}>{file.path}</span>{file.changed && <span role="status">Changed on disk or branch</span>}<button disabled={writing || file.content === file.saved} onClick={() => save(file)}>Save</button>{selection && <button disabled={!activeTicket} title={activeTicket ? `Send selection to ${view.conversation}` : "Add a ticket overlay to discuss code"} onClick={() => discuss(selection)}>Discuss selection</button>}</div>
          <Suspense fallback={<p>Loading editor…</p>}><CodeEditor openPaths={view.tabs.map((tab) => tab.path)} path={file.path} value={file.content} line={file.line} jump={jump} notes={currentNotes} tabSize={file.settings.tab_size} tabs={file.settings.indent_style === "tabs"} wrap={file.settings.line_wrap} onSave={() => { void save(file); }}
            onChange={(content) => setView((old) => ({ ...old, tabs: old.tabs.map((tab) => tab.path === old.active ? { ...tab, content } : tab) }))}
            onLine={(line) => setView((old) => old.tabs.find((tab) => tab.path === old.active)?.line === line ? old : ({ ...old, tabs: old.tabs.map((tab) => tab.path === old.active ? { ...tab, line } : tab) }))} onSelection={setSelection} /></Suspense></> : <p className="quiet-empty">Open a file from this repository.</p>}
      </main>
      {contextPane}
      {!contextOpen && <div className="workspace-drawer-rail right"><IconButton id={paneId + "-context-show"} icon="panel-right" label="Show ticket context" aria-expanded={false} aria-controls={paneId + "-context"} onClick={() => toggleDrawer("context", true)} /></div>}
    </div>}
    {closing && <dialog ref={closeDialog} className="workspace-close-dialog" aria-label="Close file" onCancel={(event) => { event.preventDefault(); if (!busy) setClosing(""); }}><h2>You have unsaved changes</h2><p>{closing}</p>{error && <p>{error}</p>}<button disabled={busy} onClick={() => setClosing("")}>Cancel</button><button disabled={busy} onClick={() => closeFile(closing)}>Do not save</button><button disabled={busy} onClick={async () => {
      const tab = latest.current.tabs.find((tab) => tab.path === closing);
      if (!tab || !await save(tab)) return;
      const current = latest.current.tabs.find((value) => value.path === closing);
      if (current && current.content !== current.saved) setError("New changes arrived while saving. Review the draft or choose Save again.");
      else closeFile(closing);
    }}>Save</button></dialog>}
  </section>;
}

const Directory = memo(function DirectoryRows({ payload, path, onOpen, expanded, toggle }: { payload: Record<string, string>; path: string; onOpen: (path: string) => void; expanded: string[]; toggle: (path: string) => void }) {
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [message, setMessage] = useState("");
  useEffect(() => { let active = true; command<{ entries: TreeEntry[]; truncated: boolean }>("workspace.tree", { ...payload, path }).then((result) => { if (active) { setEntries(result.entries); setMessage(result.truncated ? "First 1,000 entries shown. Open a file by path if it is not listed." : ""); } }).catch((reason) => active && setMessage(String(reason))); return () => { active = false; }; }, [path]);
  return <ul>{entries.map((entry) => <li key={entry.path}><button title={entry.path} aria-expanded={entry.directory ? expanded.includes(entry.path) : undefined} onClick={() => entry.directory ? toggle(entry.path) : onOpen(entry.path)}>{entry.directory ? expanded.includes(entry.path) ? "▾ " : "▸ " : ""}{entry.name}</button>{entry.directory && expanded.includes(entry.path) && <Directory payload={payload} path={entry.path} onOpen={onOpen} expanded={expanded} toggle={toggle} />}</li>)}{message && <li role="status">{message}</li>}</ul>;
});
