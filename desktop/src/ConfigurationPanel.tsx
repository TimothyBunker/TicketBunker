import { useEffect, useMemo, useRef, useState } from "react";
import BackButton from "./BackButton";
import { useGuardedAction, useUnsavedEditor } from "./UnsavedChanges";
import TrackerConnections, { type Connection } from "./TrackerConnections";
import { chooseGitDirectory, command } from "./transport";
import { linkedBoards, trackerSummary, type Board, type Bunker, type ConfigurationState, type Integration, type Project, type Repository, type TicketField, type TrackerSource } from "./types";

const FIELD_TYPES = ["string", "text", "number", "boolean", "date", "datetime", "user", "enum", "tags", "url"];
const EMPTY_TRACKER: TrackerSource = { provider: "github", kind: "project", owner: "", number: 0, repository: "", include_comments: true, title: "", url: "" };
export type ConfigurationTab = "board" | "repositories" | "checks" | "trackers" | "defaults";

type ChecksContent = { build: string; test: string; timeout_seconds: number; discover: boolean; tickets: Record<string, { build: string; test: string }> };
type CheckSuggestion = { tool: string; reason: string; build: string; test: string; repository: string };
const EMPTY_CHECKS: ChecksContent = { build: "", test: "", timeout_seconds: 600, discover: false, tickets: {} };
const FIELD_LABELS: Record<string, string> = { title: "Title", description: "Description", status: "Status", type: "Type", priority: "Priority", assignee: "Assignee", reporter: "Reporter", labels: "Labels", created: "Created", updated: "Updated" };

function sameBoard(left: TrackerSource | Board, right: TrackerSource | Board) {
  return left.kind === right.kind && left.owner === right.owner && Number(left.number) === Number(right.number) && (left.repository || "") === (right.repository || "");
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

type DraftKind = "ticket-fields" | "integrations" | "repositories" | "checks";
function normalizedChecks(content: Partial<ChecksContent> = {}): ChecksContent {
  return { build: String(content.build || ""), test: String(content.test || ""), timeout_seconds: Number(content.timeout_seconds) || 600, discover: Boolean(content.discover), tickets: clone(content.tickets || {}) };
}
const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);
const repositoryFields = (items: Repository[]) => items.map(({ id, name, canonical_remote, default_branch }) => ({ id, name, canonical_remote, default_branch }));

function projectChoices(projects: Project[], parent = ""): { project: Project; label: string }[] {
  return projects.flatMap((project) => {
    const label = `${parent}${project.key} · ${project.name}`;
    return [{ project, label }, ...projectChoices(project.children || [], `${label} / `)];
  });
}

export default function ConfigurationPanel({
  bunker,
  project,
  onClose,
  onBunker,
  notify,
  trackers,
  initialTab,
  onConnected,
  onSync,
}: {
  bunker: Bunker;
  project?: Project;
  onClose: () => void;
  onBunker: (bunker: Bunker) => void;
  notify: (message: string, error?: boolean) => void;
  trackers?: Record<string, boolean>;
  initialTab?: ConfigurationTab;
  onConnected?: (trackers: Record<string, boolean>, connection: Connection, bunker?: Bunker) => void;
  onSync?: () => Promise<boolean>;
}) {
  const protect = useGuardedAction();
  const [state, setState] = useState<ConfigurationState | null>(null);
  const [fields, setFields] = useState<TicketField[]>([]);
  const [providers, setProviders] = useState<Record<string, Integration>>({});
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [availableBindings, setAvailableBindings] = useState(bunker.repositories);
  const [tracker, setTracker] = useState<TrackerSource>(EMPTY_TRACKER);
  const [tab, setTab] = useState<ConfigurationTab>(initialTab || (project ? "board" : "trackers"));
  const [provider, setProvider] = useState("github");
  const [busy, setBusy] = useState(false);
  const [savingAll, setSavingAll] = useState(false);
  const [checks, setChecks] = useState<ChecksContent>(EMPTY_CHECKS);
  const baseline = useRef({ fields: [] as TicketField[], providers: {} as Record<string, Integration>, repositories: [] as Repository[], checks: EMPTY_CHECKS });
  const [connecting, setConnecting] = useState("");
  const [pathRepository, setPathRepository] = useState<Repository | "auto" | null>(null);
  const [localPath, setLocalPath] = useState("");
  const [error, setError] = useState("");
  const [boards, setBoards] = useState<Board[] | null>(null);
  const [boardNotes, setBoardNotes] = useState<string[]>([]);
  const [boardError, setBoardError] = useState("");
  const [boardFilter, setBoardFilter] = useState("");
  const [choice, setChoice] = useState<Board | null>(null);
  const [linkSummary, setLinkSummary] = useState<string[]>([]);
  const githubConnected = Boolean(trackers?.github);
  useEffect(() => setAvailableBindings(bunker.repositories), [bunker.repositories]);
  const drafts = useRef({ fields, providers, repositories, checks });
  drafts.current = { fields, providers, repositories, checks };

  function applyConfiguration(value: ConfigurationState, saved?: DraftKind) {
    const previous = baseline.current;
    const keep = {
      fields: saved !== "ticket-fields" && !same(drafts.current.fields, previous.fields),
      providers: saved !== "integrations" && !same(drafts.current.providers, previous.providers),
      repositories: saved !== "repositories" && !same(repositoryFields(drafts.current.repositories), repositoryFields(previous.repositories)),
      checks: saved !== "checks" && !same(drafts.current.checks, previous.checks),
    };
    const next = {
      fields: clone(value.documents["ticket-fields"]?.content.fields || []),
      providers: clone(value.documents.integrations?.content.providers || {}),
      repositories: clone(value.repositories.items || []),
      checks: normalizedChecks(value.documents.checks?.content),
    };
    baseline.current = { fields: keep.fields ? previous.fields : next.fields, providers: keep.providers ? previous.providers : next.providers,
      repositories: keep.repositories ? previous.repositories : next.repositories, checks: keep.checks ? previous.checks : next.checks };
    // Preserve each dirty section's original expected revision too. Receiving a
    // newer snapshot is not permission to overwrite someone else's changes.
    setState((current) => {
      const documents = { ...value.documents };
      for (const [kind, retained] of [["ticket-fields", keep.fields], ["integrations", keep.providers], ["checks", keep.checks]] as const) {
        if (retained) Object.assign(documents, { [kind]: current?.documents[kind] });
      }
      return { ...value, documents, repositories: keep.repositories && current ? current.repositories : value.repositories };
    });
    setFields((current) => keep.fields ? current : next.fields);
    setProviders((current) => keep.providers ? current : next.providers);
    setRepositories((current) => keep.repositories ? current : next.repositories);
    setChecks((current) => keep.checks ? current : next.checks);
    setTracker(clone(value.documents["tracker-source"]?.content || EMPTY_TRACKER));
    setProvider((current) => current in next.providers || keep.providers ? current : Object.keys(next.providers)[0] || "");
  }

  useEffect(() => {
    if (!project || tab !== "board" || !githubConnected) return;
    let active = true;
    setBoardError("");
    command<{ boards: Board[]; notes: string[] }>("application.tracker.boards", { provider: "github" })
      .then((value) => {
        if (!active) return;
        setBoards(value.boards);
        setBoardNotes(value.notes || []);
      })
      .catch((reason) => active && setBoardError(String(reason)));
    return () => { active = false; };
  }, [project?.id, tab, githubConnected]);

  useEffect(() => {
    let active = true;
    setError("");
    setTab(initialTab || (project ? "board" : "trackers"));
    command<ConfigurationState>("application.bunker.configuration.get", {
      bunker_id: bunker.id,
      project_id: project?.id || "",
    }).then((value) => {
      if (!active) return;
      applyConfiguration(value);
    }).catch((reason) => active && setError(String(reason)));
    return () => { active = false; };
  }, [bunker.id, project?.id]);

  const canWrite = Boolean(state?.permission.can_write);
  const linked = state ? linkedBoards(state.documents) : [];
  const checksDocument = state?.documents.checks as { revision: string; content: Partial<ChecksContent> } | undefined;
  const [suggestions, setSuggestions] = useState<CheckSuggestion[] | null>(null);
  const [agentSuggestion, setAgentSuggestion] = useState<{ build: string; test: string; reason: string } | null>(null);
  const [asking, setAsking] = useState(false);
  const dirty = {
    "ticket-fields": !same(fields, baseline.current.fields),
    integrations: !same(providers, baseline.current.providers),
    repositories: !same(repositoryFields(repositories), repositoryFields(baseline.current.repositories)),
    checks: !same(checks, baseline.current.checks),
  };
  const hasDraft = Boolean(state && Object.values(dirty).some(Boolean));
  function discardDrafts() {
    setFields(clone(baseline.current.fields)); setProviders(clone(baseline.current.providers));
    setRepositories(clone(baseline.current.repositories)); setChecks(clone(baseline.current.checks));
  }
  async function saveDrafts(): Promise<boolean | string> {
    setSavingAll(true);
    try {
      for (const kind of ["ticket-fields", "integrations", "repositories", "checks"] as DraftKind[]) {
        if (!dirty[kind]) continue;
        const result = kind === "checks" ? await publishChecks() : kind === "repositories" ? await publishRepositories() : await publish(kind);
        if (result !== true) return result || "Configuration was not saved.";
      }
      return true;
    } finally { setSavingAll(false); }
  }
  useUnsavedEditor({ dirty: hasDraft, busy: busy || savingAll || Boolean(connecting) || asking, save: saveDrafts, discard: discardDrafts,
    description: `Save applies these settings to ${bunker.remote ? "the shared" : "this local"} Bunker.${Object.values(dirty).filter(Boolean).length > 1 ? " Sections save separately; a later failure does not undo earlier saves." : ""}` });

  async function askAgentForChecks() {
    setAsking(true);
    setError("");
    try {
      const value = await command<{ build: string; test: string; reason: string }>("application.checks.discover", { bunker_id: bunker.id, project_id: project?.id || "" });
      setAgentSuggestion(value);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setAsking(false);
    }
  }

  async function detectChecks() {
    setBusy(true);
    setError("");
    try {
      const value = await command<{ suggestions: CheckSuggestion[]; repositories: string[] }>("application.project.checks.detect", { bunker_id: bunker.id, project_id: project?.id || "" });
      setSuggestions(value.suggestions);
      if (value.repositories.length === 0) setError("Attach a local repository to this Project first; detection reads its root.");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function publishChecks() {
    setBusy(true);
    setError("");
    try {
      const value = await command<ConfigurationState>("application.bunker.configuration.replace", {
        bunker_id: bunker.id,
        project_id: project?.id || "",
        kind: "checks",
        expected_revision: checksDocument?.revision || "",
        content: checks,
      });
      applyConfiguration(value, "checks");
      notify("Checks saved");
      return true;
    } catch (reason) {
      setError(String(reason));
      return String(reason);
    } finally {
      setBusy(false);
    }
  }
  const selected = providers[provider];
  const mappingRows = useMemo(() => Object.entries(selected?.fields || {}), [selected]);

  async function publish(kind: "ticket-fields" | "integrations") {
    const document = state?.documents[kind];
    if (!document) {
      const message = "This configuration is not available in the current snapshot. Reopen Configuration and retry.";
      setError(message);
      return message;
    }
    setBusy(true);
    setError("");
    try {
      const value = await command<ConfigurationState>("application.bunker.configuration.replace", {
        bunker_id: bunker.id,
        project_id: project?.id || "",
        kind,
        expected_revision: document.revision,
        content: kind === "ticket-fields" ? { fields } : { providers },
      });
      applyConfiguration(value, kind);
      notify(kind === "ticket-fields" ? "Ticket fields saved" : "Integration mappings saved");
      return true;
    } catch (reason) {
      setError(String(reason));
      return String(reason);
    } finally {
      setBusy(false);
    }
  }

  async function linkBoard(board: Board) {
    if (!state || !project) return;
    setBusy(true);
    setError("");
    try {
      const value = await command<{ configuration: ConfigurationState; bunker: Bunker; sync?: { status: string; message?: string }; link: { summary: string[]; items: number; fields_found: number } }>("application.project.link", {
        bunker_id: bunker.id,
        project_id: project.id,
        board,
        include_comments: tracker.include_comments,
      });
      applyConfiguration(value.configuration);
      setChoice(null);
      setLinkSummary(value.link.summary);
      onBunker(value.bunker);
      if (value.sync?.status === "failed") setError(`Board connected. Ticket sync failed: ${value.sync.message} Use Sync tickets to retry.`);
      else notify(`Connected ${board.title}. Tickets synced.`);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function unlinkBoard(source: TrackerSource) {
    if (!state || !project || !source.id) return;
    setBusy(true);
    setError("");
    try {
      const value = await command<{ configuration: ConfigurationState; bunker: Bunker }>("application.project.unlink", {
        bunker_id: bunker.id,
        project_id: project.id,
        source_id: source.id,
      });
      applyConfiguration(value.configuration);
      onBunker(value.bunker);
      notify(`Unlinked ${trackerSummary(source)}. Its tickets stay in the Project.`);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function publishRepositories() {
    if (!state || project) return;
    setBusy(true);
    setError("");
    try {
      const value = await command<{
        configuration: ConfigurationState;
        bunker: Bunker;
      }>("application.bunker.repository.replace", {
        bunker_id: bunker.id,
        project_id: "",
        expected_revision: state.repositories.revision,
        repositories: repositories.map(({ id, name, canonical_remote, default_branch }) => ({
          id,
          name,
          canonical_remote,
          default_branch,
        })),
      });
      applyConfiguration(value.configuration, "repositories");
      onBunker(value.bunker);
      notify("Repositories saved");
      return true;
    } catch (reason) {
      setError(String(reason));
      return String(reason);
    } finally {
      setBusy(false);
    }
  }

  function updateRepository(index: number, patch: Partial<Repository>) {
    setRepositories((values) => values.map((value, item) => item === index ? { ...value, ...patch } : value));
  }

  async function toggleAvailability(repository: Repository, target: Project, enabled: boolean) {
    if (project || !canWrite || busy || dirty.repositories) return;
    setBusy(true);
    setError("");
    try {
      // Read this Project's revision, then change only this remote. Other
      // repositories and their stable binding IDs must survive the toggle.
      const current = await command<ConfigurationState>("application.bunker.configuration.get", { bunker_id: bunker.id, project_id: target.id });
      const items = current.repositories.items;
      const exists = items.some((row) => row.canonical_remote === repository.canonical_remote);
      const selected = enabled
        ? exists ? items : [...items, { ...repository, id: "", project_id: target.id }]
        : items.filter((row) => row.canonical_remote !== repository.canonical_remote);
      const value = await command<{ bunker: Bunker }>("application.project.repositories.select", {
        bunker_id: bunker.id, project_id: target.id,
        expected_revision: current.repositories.revision, repositories: repositoryFields(selected),
      });
      setAvailableBindings(value.bunker.repositories);
      onBunker(value.bunker);
      notify(`${repository.name} ${enabled ? "available to" : "removed from"} ${target.key}`);
    } catch (reason) {
      setError(String(reason));
    } finally { setBusy(false); }
  }

  async function connectRepository(repository: Repository | "auto" = "auto", suppliedPath?: string) {
    if (!project) return;
    setError("");
    try {
      const path = suppliedPath ?? await chooseGitDirectory();
      if (!path) {
        if (!window.__TAURI__?.dialog) {
          setPathRepository(repository);
          setLocalPath(repository === "auto" ? "" : repository.local_path || "");
        }
        return;
      }
      setConnecting(repository === "auto" ? "auto" : repository.id);
      const value = await command<{ bunker: Bunker }>("application.bunker.repository.attach", {
        bunker_id: bunker.id,
        project_id: project.id,
        ...(repository === "auto" ? {} : { binding_id: repository.id }),
        path,
      });
      const configuration = await command<ConfigurationState>("application.bunker.configuration.get", {
        bunker_id: bunker.id,
        project_id: project.id,
      });
      applyConfiguration(configuration);
      onBunker(value.bunker);
      notify("Repository connected and fetched");
      setPathRepository(null);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setConnecting("");
    }
  }

  async function disconnectRepository(repository: Repository) {
    if (!project || !repository.id) return;
    setConnecting(repository.id);
    setError("");
    try {
      const value = await command<{ bunker: Bunker }>("application.bunker.repository.detach", {
        bunker_id: bunker.id,
        project_id: project.id,
        binding_id: repository.id,
      });
      const configuration = await command<ConfigurationState>("application.bunker.configuration.get", {
        bunker_id: bunker.id,
        project_id: project.id,
      });
      applyConfiguration(configuration);
      onBunker(value.bunker);
      notify("Local checkout disconnected");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setConnecting("");
    }
  }

  function updateField(index: number, patch: Partial<TicketField>) {
    setFields((values) => values.map((value, item) => item === index ? { ...value, ...patch } : value));
  }

  function updateProvider(patch: Partial<Integration>) {
    setProviders((values) => ({ ...values, [provider]: { ...values[provider], ...patch } }));
  }

  function updateMapping(index: number, ours: string, theirs: string) {
    const rows = mappingRows.map(([left, right], item) => item === index ? [ours, theirs] : [left, right]);
    updateProvider({ fields: Object.fromEntries(rows.filter(([left]) => left)) });
  }

  function addProvider() {
    let suffix = 1;
    let id = "custom";
    while (providers[id]) id = `custom-${++suffix}`;
    setProviders((values) => ({ ...values, [id]: { label: "Custom integration", template: "custom", fields: {} } }));
    setProvider(id);
  }

  function renameProvider(next: string) {
    const id = next.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[^a-z]+|[-._]+$/g, "");
    if (!id || id === provider || providers[id]) return;
    const nextProviders = { ...providers };
    nextProviders[id] = nextProviders[provider];
    delete nextProviders[provider];
    setProviders(nextProviders);
    setProvider(id);
  }

  return (
    <aside className="configuration-drawer" aria-label="Configuration">
      <header>
        <div><p className="eyebrow">{project ? `${project.key} · project setup` : tab === "defaults" ? "Bunker defaults · new projects" : "Bunker setup"}</p><h2>Configuration</h2></div>
        <BackButton onClick={() => protect(onClose)} />
      </header>
      <fieldset className="configuration-fields" disabled={busy || savingAll || Boolean(connecting)}>
      <nav>
        {project && <button className={tab === "board" ? "selected" : ""} onClick={() => setTab("board")}>Boards</button>}
        <button className={tab === "repositories" ? "selected" : ""} onClick={() => setTab("repositories")}>Repositories</button>
        {project && <button className={tab === "checks" ? "selected" : ""} onClick={() => setTab("checks")}>Checks</button>}
        {!project && <button className={tab === "trackers" ? "selected" : ""} onClick={() => setTab("trackers")}>Trackers</button>}
        {!project && <button className={tab === "defaults" ? "selected" : ""} onClick={() => setTab("defaults")}>Defaults</button>}
      </nav>
      {!state && !error && <div className="ticket-loading">Loading configuration</div>}
      {state && !canWrite && <div className="permission-note"><strong>Read only</strong><span>{state.permission.reason}</span></div>}
      {error && <div className="inline-error">{error}</div>}

      {state && !project && tab === "trackers" && <section className="config-surface">
        <div className="config-heading"><div><h3>Trackers</h3><p>Connect your agile app once. Each Project then picks its board from a list.</p></div></div>
        <TrackerConnections trackers={trackers} connections={bunker.connections} bunkerId={bunker.id} onConnected={(next, connection, refreshed) => { onConnected?.(next, connection, refreshed); if (refreshed) onBunker(refreshed); }} onMessage={(message, isError) => isError ? setError(message) : notify(message)} />
      </section>}

      {state && project && tab === "board" && <section className="config-surface board-surface">
        <div className="config-heading">
          <div><h3>Boards</h3><p>{linked.length ? `${linked.length === 1 ? "One board" : `${linked.length} boards`} linked. Tickets, fields, and mappings follow from them.` : "Pick the boards this Project mirrors. Tickets, fields, and mappings follow from them."}</p></div>
          {linked.length > 0 && onSync && <button className="primary" disabled={busy} onClick={async () => { setBusy(true); try { if (await onSync()) setError(""); } catch (reason) { setError(String(reason)); } finally { setBusy(false); } }}>Sync tickets</button>}
        </div>
        {linked.length > 0 && <ul className="linked-boards" aria-label="Linked boards">
          {linked.map((source) => <li key={source.id || trackerSummary(source)}><span><strong>{trackerSummary(source)}</strong>{source.url && <small>{source.url}</small>}</span><button className="danger-control" disabled={!canWrite || busy || !source.id} onClick={() => unlinkBoard(source)}>Unlink</button></li>)}
        </ul>}
        {!githubConnected && <div className="board-connect">
          <h4>Connect GitHub to see your boards</h4>
          <TrackerConnections compact providers={["github"]} trackers={trackers} connections={bunker.connections} bunkerId={bunker.id} onConnected={(next, connection, refreshed) => { onConnected?.(next, connection, refreshed); if (refreshed) onBunker(refreshed); }} onMessage={(message, isError) => isError ? setError(message) : notify(message)} />
        </div>}
        {githubConnected && <div className="board-picker">
          {boardError && <div className="inline-error">{boardError}</div>}
          {!boards && !boardError && <div className="ticket-loading">Listing your GitHub boards</div>}
          {boards && <>
            <label className="search-field"><span className="sr-only">Filter boards</span><input type="search" value={boardFilter} placeholder="Filter boards" onChange={(event) => setBoardFilter(event.target.value)} /></label>
            {boardNotes.map((note) => <p key={note} className="field-note">{note}</p>)}
            <ul className="board-list" aria-label="Boards">
              {boards.filter((board) => !boardFilter || board.title.toLowerCase().includes(boardFilter.toLowerCase())).map((board) => {
                const current = linked.some((source) => sameBoard(source, board));
                const selected = choice ? sameBoard(choice, board) : false;
                return (
                  <li key={`${board.kind}-${board.owner}-${board.number}-${board.repository}`}>
                    <button type="button" className={selected ? "board-row selected" : "board-row"} aria-pressed={selected} disabled={!canWrite} onClick={() => setChoice(board)}>
                      <span className="board-kind">{board.kind === "project" ? "Project board" : "Issues"}</span>
                      <span className="board-title"><strong>{board.title}</strong><small>{board.kind === "project" ? `${board.owner} · #${board.number}` : `${board.open_issues ?? 0} open issues${board.private ? " · private" : ""}`}</small></span>
                      <span className="board-state">{current ? "Linked" : ""}</span>
                    </button>
                  </li>
                );
              })}
              {boards.length === 0 && <li className="board-empty">This token sees no boards yet.</li>}
            </ul>
            <div className="board-actions">
              <label className="check"><input type="checkbox" checked={tracker.include_comments} disabled={!canWrite} onChange={(event) => setTracker({ ...tracker, include_comments: event.target.checked })} />Include comments</label>
              <button className="primary" disabled={!canWrite || busy || !choice || linked.some((source) => sameBoard(source, choice))} onClick={() => choice && linkBoard(choice)}>{busy ? "Connecting and syncing…" : "Link board"}</button>
            </div>
          </>}
        </div>}
        <div className="what-syncs">
          <h4>What syncs</h4>
          {linkSummary.length > 0 && <p className="field-note">Built from the board: {linkSummary.join(" · ")}.</p>}
          <ul className="sync-fields">{Object.entries(providers.github?.fields || {}).map(([ours, theirs]) => <li key={ours}><strong>{FIELD_LABELS[ours] || ours}</strong><span>{theirs}</span></li>)}</ul>
          <details className="advanced"><summary>Adjust field mapping</summary>
            <p className="field-note">Each row pairs one TicketBunker field with one GitHub payload path.</p>
            <div className="mapping-matrix">
              <div className="matrix-heading"><span>TicketBunker</span><span>External field</span><span /></div>
              {Object.entries(providers.github?.fields || {}).map(([ours, theirs], index) => <div className="mapping-row" key={`${ours}-${index}`}>
                <select aria-label={`Mapping ${index + 1} TicketBunker field`} value={ours} disabled={!canWrite} onChange={(event) => { setProvider("github"); const rows = Object.entries(providers.github?.fields || {}).map(([left, right], item) => item === index ? [event.target.value, right] : [left, right]); setProviders((values) => ({ ...values, github: { ...values.github, fields: Object.fromEntries(rows.filter(([left]) => left)) } })); }}><option value="">Choose field</option>{fields.map((field) => <option key={field.key} value={field.key} disabled={field.key !== ours && providers.github?.fields[field.key] !== undefined}>{field.label} · {field.key}</option>)}</select>
                <input aria-label={`Mapping ${index + 1} external field`} value={theirs} disabled={!canWrite} onChange={(event) => setProviders((values) => ({ ...values, github: { ...values.github, fields: { ...values.github.fields, [ours]: event.target.value } } }))} />
                <button aria-label={`Remove mapping ${index + 1}`} disabled={!canWrite} onClick={() => setProviders((values) => { const next = { ...values.github.fields }; delete next[ours]; return { ...values, github: { ...values.github, fields: next } }; })}>−</button>
              </div>)}
            </div>
            <div className="integration-actions"><button disabled={!canWrite} onClick={() => setProviders((values) => ({ ...values, github: { ...values.github, fields: { ...values.github.fields, [fields.find((field) => !values.github?.fields[field.key])?.key || ""]: "" } } }))}>+ Add mapping</button><button className="primary" disabled={!canWrite || busy} onClick={() => { setProvider("github"); publish("integrations"); }}>Save mappings</button></div>
          </details>
          <details className="advanced"><summary>Ticket fields</summary>
            <p className="field-note">This Project keeps this exact composition.</p>
            <div className="field-matrix">
              <div className="matrix-heading"><span>Label</span><span>Key</span><span>Type</span><span>Required</span><span /></div>
              {fields.map((field, index) => <div className="field-row" key={`${field.key}-${index}`}>
                <input aria-label={`Field ${index + 1} label`} value={field.label} disabled={!canWrite} onChange={(event) => updateField(index, { label: event.target.value })} />
                <input aria-label={`Field ${index + 1} key`} value={field.key} disabled={!canWrite} onChange={(event) => updateField(index, { key: event.target.value })} />
                <select aria-label={`Field ${index + 1} type`} value={field.type} disabled={!canWrite} onChange={(event) => updateField(index, { type: event.target.value })}>{FIELD_TYPES.map((value) => <option key={value}>{value}</option>)}</select>
                <input aria-label={`Field ${index + 1} required`} type="checkbox" checked={field.required} disabled={!canWrite} onChange={(event) => updateField(index, { required: event.target.checked })} />
                <button aria-label={`Remove ${field.label}`} disabled={!canWrite} onClick={() => setFields((values) => values.filter((_, item) => item !== index))}>−</button>
                {field.type === "enum" && <input className="enum-values" aria-label={`${field.label} values`} value={(field.values || []).join(", ")} disabled={!canWrite} placeholder="Comma-separated values" onChange={(event) => updateField(index, { values: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) })} />}
              </div>)}
            </div>
            <div className="integration-actions"><button disabled={!canWrite} onClick={() => setFields((values) => [...values, { key: `field_${values.length + 1}`, label: "New field", type: "string", required: false }])}>+ Add field</button><button className="primary" disabled={!canWrite || busy} onClick={() => publish("ticket-fields")}>Save fields</button></div>
          </details>
        </div>
      </section>}

      {state && tab === "repositories" && <section className="config-surface">
        <div className="config-heading">
          <div><h3>{project ? "Local repositories" : "Allowed repositories"}</h3><p>{project ? "Folders match the remotes enabled for this Project." : "Expand a repository to choose its Projects."}</p></div>
          {project ? <button className="primary" disabled={Boolean(connecting) || !repositories.length} onClick={() => connectRepository()}>Add local repository</button>
            : <button className="primary" disabled={!canWrite || busy} onClick={publishRepositories}>{busy ? "Saving…" : "Save repositories"}</button>}
        </div>
        <div className={`repository-matrix ${project ? "repository-checkouts" : "repository-registry"}`} role="table" aria-label={project ? "Local repositories" : "Allowed repositories"}>
          <div className="repository-heading" role="row"><span>#</span><span>Name</span><span>Remote</span><span>Base</span>{project ? <span>This computer</span> : <span />}</div>
          {repositories.map((repository, index) => <div role="rowgroup" key={repository.id || `new-${index}`}>
            <div className="repository-row" role="row">
              <span className="repository-index">{index + 1}</span>
              {project ? <>
                <span>{repository.name}</span><span className="repository-identity">{repository.canonical_remote}</span><span>{repository.default_branch}</span>
                <div className="repository-local">
                  {repository.local_path ? <>
                    <span title={repository.local_path}>{repository.local_path}</span>
                    <button disabled={Boolean(connecting)} onClick={() => connectRepository(repository)}>Change folder</button>
                    <button className="danger-control" disabled={Boolean(connecting)} onClick={() => disconnectRepository(repository)}>Disconnect</button>
                  </> : <span>Not connected</span>}
                </div>
              </> : <>
                <input aria-label={`Repository ${index + 1} name`} value={repository.name} disabled={!canWrite || busy} onChange={(event) => updateRepository(index, { name: event.target.value })} />
                <input aria-label={`Repository ${index + 1} allowed remote`} value={repository.canonical_remote || ""} disabled={!canWrite || busy} placeholder="github.com/owner/repository" onChange={(event) => updateRepository(index, { canonical_remote: event.target.value })} />
                <input aria-label={`Repository ${index + 1} base branch`} value={repository.default_branch || ""} disabled={!canWrite || busy} placeholder="main" onChange={(event) => updateRepository(index, { default_branch: event.target.value })} />
                <button aria-label={`Remove ${repository.name || `repository ${index + 1}`}`} disabled={!canWrite || busy} onClick={() => setRepositories((values) => values.filter((_, item) => item !== index))}>−</button>
              </>}
            </div>
            {!project && <div role="row"><div role="cell" className="repository-projects"><details>
              <summary aria-label={`Projects for ${repository.name}`}>Projects · {availableBindings.filter((row) => row.canonical_remote === repository.canonical_remote).length}</summary>
              {dirty.repositories && <p>Save repositories before changing availability.</p>}
              {projectChoices(bunker.projects).map(({ project: target, label }) => <label key={target.id}>
                <input type="checkbox" aria-label={`${label} availability for ${repository.name}`} checked={availableBindings.some((row) => row.project_id === target.id && row.canonical_remote === repository.canonical_remote)} disabled={!canWrite || busy || dirty.repositories} onChange={(event) => toggleAvailability(repository, target, event.target.checked)} />
                {label}
              </label>)}
              {!bunker.projects.length && <p>Create a Project to make this repository available.</p>}
            </details></div></div>}
          </div>)}
          {repositories.length === 0 && <div className="repository-empty">{project ? "No repositories enabled. Ask a Bunker owner to enable this Project under Bunker → Repositories → Projects." : "No allowed repositories yet."}</div>}
        </div>
        {pathRepository && <form className="branch-form" onSubmit={(event) => { event.preventDefault(); void connectRepository(pathRepository, localPath.trim()); }}>
          <label>Local repository path<input autoFocus value={localPath} onChange={(event) => setLocalPath(event.target.value)} required /></label>
          <p>{pathRepository === "auto" ? "Choose a Git folder on this computer. Its origin selects the matching repository." : `Path to ${pathRepository.name} on this computer.`}</p>
          <div><button type="button" disabled={Boolean(connecting)} onClick={() => setPathRepository(null)}>Cancel</button><button className="primary" disabled={Boolean(connecting) || !localPath.trim()}>Connect repository</button></div>
        </form>}
        {!project && <button disabled={!canWrite || busy} onClick={() => setRepositories((values) => [...values, { id: "", project_id: "", name: "New repository", canonical_remote: "", default_branch: "main", local_path: null }])}>+ Add repository</button>}
      </section>}

      {state && project && tab === "checks" && <section className="config-surface">
        <div className="config-heading"><div><h3>Checks</h3><p>Before the agent writes, TicketBunker runs these in a scratch worktree at the branch head. A failing build becomes a fact in the report instead of a guess.</p></div><button className="primary" disabled={!canWrite || busy} onClick={publishChecks}>Save checks</button></div>
        <div className="checks-form">
          <label>Build command<input value={checks.build} disabled={!canWrite} placeholder="cmake -S . -B build && cmake --build build" onChange={(event) => setChecks({ ...checks, build: event.target.value })} /></label>
          <label>Test command<input value={checks.test} disabled={!canWrite} placeholder="python3 -m pytest -x -q" onChange={(event) => setChecks({ ...checks, test: event.target.value })} /></label>
          <label>Timeout (seconds)<input type="number" min={30} max={3600} value={checks.timeout_seconds} disabled={!canWrite} onChange={(event) => setChecks({ ...checks, timeout_seconds: Number(event.target.value) || 600 })} /></label>
        </div>
        <label className="check-toggle"><input type="checkbox" checked={checks.discover} disabled={!canWrite} onChange={(event) => setChecks({ ...checks, discover: event.target.checked })} /><span><strong>Discover automatically</strong><small>When no commands are set, use the ones detected from the repository's manifests at synthesis time.</small></span></label>
        <div className="integration-actions"><button disabled={busy} onClick={detectChecks}>Detect from repository</button><button disabled={busy || asking} onClick={askAgentForChecks}>{asking ? "Asking the agent…" : "Ask the agent"}</button>{(checks.build || checks.test) && <button className="danger-control" disabled={!canWrite} onClick={() => setChecks({ ...checks, build: "", test: "" })}>Clear</button>}</div>
        {suggestions && suggestions.length === 0 && <p className="quiet-note">No build system was recognized at the repository root. Enter the commands your team runs, or ask the agent.</p>}
        {suggestions && suggestions.length > 0 && <ul className="check-suggestions">{suggestions.map((item) => <li key={`${item.repository}-${item.tool}`}><div><strong>{item.tool}</strong><small>{item.reason}</small>{item.build && <code>{item.build}</code>}{item.test && <code>{item.test}</code>}</div><button disabled={!canWrite} onClick={() => setChecks({ ...checks, build: item.build, test: item.test })}>Use</button></li>)}</ul>}
        {agentSuggestion && <ul className="check-suggestions"><li><div><strong>Agent</strong><small>{agentSuggestion.reason}</small>{agentSuggestion.build && <code>{agentSuggestion.build}</code>}{agentSuggestion.test && <code>{agentSuggestion.test}</code>}</div><button disabled={!canWrite} onClick={() => setChecks({ ...checks, build: agentSuggestion.build, test: agentSuggestion.test })}>Use</button></li></ul>}
        {Object.keys(checks.tickets).length > 0 && <div className="ticket-checks"><h4>Per-ticket commands</h4><p className="quiet-note">These override the project's commands for one ticket. Set them from the ticket page.</p><ul>{Object.entries(checks.tickets).map(([id, item]) => <li key={id}><div><strong>{id}</strong>{item.build && <code>{item.build}</code>}{item.test && <code>{item.test}</code>}</div><button aria-label={`Remove ${id} checks`} disabled={!canWrite} onClick={() => { const tickets = { ...checks.tickets }; delete tickets[id]; setChecks({ ...checks, tickets }); }}>−</button></li>)}</ul></div>}
        <details className="advanced"><summary>What runs, and where</summary><p>Commands run with your shell in a detached worktree at the head of the branch being synthesized, under TicketBunker's data folder, never in your checkout, with the timeout above. The build runs first; the test command is skipped when the build fails. The first error line and a bounded log land in the report's evidence, and a failure sets the Standing verdict to blocked. A ticket's own commands win over the project's, which win over discovered ones.</p></details>
      </section>}

      {state && !project && tab === "defaults" && <section className="config-surface">
        <div className="config-heading"><div><h3>Ticket fields</h3><p>New Projects copy these fields when created.</p></div><button className="primary" disabled={!canWrite || busy} onClick={() => publish("ticket-fields")}>Save fields</button></div>
        <div className="field-matrix">
          <div className="matrix-heading"><span>Label</span><span>Key</span><span>Type</span><span>Required</span><span /></div>
          {fields.map((field, index) => <div className="field-row" key={`${field.key}-${index}`}>
            <input aria-label={`Field ${index + 1} label`} value={field.label} disabled={!canWrite} onChange={(event) => updateField(index, { label: event.target.value })} />
            <input aria-label={`Field ${index + 1} key`} value={field.key} disabled={!canWrite} onChange={(event) => updateField(index, { key: event.target.value })} />
            <select aria-label={`Field ${index + 1} type`} value={field.type} disabled={!canWrite} onChange={(event) => updateField(index, { type: event.target.value })}>{FIELD_TYPES.map((value) => <option key={value}>{value}</option>)}</select>
            <input aria-label={`Field ${index + 1} required`} type="checkbox" checked={field.required} disabled={!canWrite} onChange={(event) => updateField(index, { required: event.target.checked })} />
            <button aria-label={`Remove ${field.label}`} disabled={!canWrite} onClick={() => setFields((values) => values.filter((_, item) => item !== index))}>−</button>
            {field.type === "enum" && <input className="enum-values" aria-label={`${field.label} values`} value={(field.values || []).join(", ")} disabled={!canWrite} placeholder="Comma-separated values" onChange={(event) => updateField(index, { values: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) })} />}
          </div>)}
        </div>
        <button disabled={!canWrite} onClick={() => setFields((values) => [...values, { key: `field_${values.length + 1}`, label: "New field", type: "string", required: false }])}>+ Add field</button>
      </section>}

      {state && !project && tab === "defaults" && <section className="config-surface">
        <div className="config-heading"><div><h3>Field mappings</h3><p>Default matrices new Projects start from. Each row pairs one TicketBunker field with one external payload path.</p></div><button className="primary" disabled={!canWrite || busy || !selected} onClick={() => publish("integrations")}>Save mappings</button></div>
        <div className="integration-selector"><select aria-label="Integration" value={provider} onChange={(event) => setProvider(event.target.value)}>{Object.entries(providers).map(([id, value]) => <option key={id} value={id}>{value.label}</option>)}</select><button disabled={!canWrite} onClick={addProvider}>+ Integration</button></div>
        {selected && <>
          <div className="integration-identity"><label>Name<input value={selected.label} disabled={!canWrite} onChange={(event) => updateProvider({ label: event.target.value })} /></label><label>Identifier<input key={provider} defaultValue={provider} disabled={!canWrite || selected.template !== "custom"} onBlur={(event) => renameProvider(event.target.value)} /></label></div>
          <div className="mapping-matrix">
            <div className="matrix-heading"><span>TicketBunker</span><span>External field</span><span /></div>
            {mappingRows.map(([ours, theirs], index) => <div className="mapping-row" key={`${ours}-${index}`}>
              <select aria-label={`Mapping ${index + 1} TicketBunker field`} value={ours} disabled={!canWrite} onChange={(event) => updateMapping(index, event.target.value, theirs)}><option value="">Choose field</option>{fields.map((field) => <option key={field.key} value={field.key} disabled={field.key !== ours && selected.fields[field.key] !== undefined}>{field.label} · {field.key}</option>)}</select>
              <input aria-label={`Mapping ${index + 1} external field`} value={theirs} disabled={!canWrite} onChange={(event) => updateMapping(index, ours, event.target.value)} />
              <button aria-label={`Remove mapping ${index + 1}`} disabled={!canWrite} onClick={() => updateProvider({ fields: Object.fromEntries(mappingRows.filter((_, item) => item !== index)) })}>−</button>
            </div>)}
          </div>
          <div className="integration-actions"><button disabled={!canWrite} onClick={() => updateProvider({ fields: { ...selected.fields, [fields.find((field) => !selected.fields[field.key])?.key || ""]: "" } })}>+ Add mapping</button>{selected.template === "custom" && <button className="danger-control" disabled={!canWrite} onClick={() => { const values = { ...providers }; delete values[provider]; setProviders(values); setProvider(Object.keys(values)[0] || ""); }}>Remove integration</button>}</div>
        </>}
      </section>}
      </fieldset>
    </aside>
  );
}
