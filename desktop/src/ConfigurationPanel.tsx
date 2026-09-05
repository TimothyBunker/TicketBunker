import { useEffect, useMemo, useState } from "react";
import TrackerConnections, { type Connection } from "./TrackerConnections";
import { chooseGitDirectory, command } from "./transport";
import { trackerSummary, type Board, type Bunker, type ConfigurationState, type Integration, type Project, type Repository, type TicketField, type TrackerSource } from "./types";

const FIELD_TYPES = ["string", "text", "number", "boolean", "date", "datetime", "user", "enum", "tags", "url"];
const EMPTY_TRACKER: TrackerSource = { provider: "github", kind: "project", owner: "", number: 0, repository: "", include_comments: true, title: "", url: "" };
export type ConfigurationTab = "board" | "repositories" | "trackers" | "defaults";
const FIELD_LABELS: Record<string, string> = { title: "Title", description: "Description", status: "Status", type: "Type", priority: "Priority", assignee: "Assignee", reporter: "Reporter", labels: "Labels", created: "Created", updated: "Updated" };

function sameBoard(left: TrackerSource | Board, right: TrackerSource | Board) {
  return left.kind === right.kind && left.owner === right.owner && Number(left.number) === Number(right.number) && (left.repository || "") === (right.repository || "");
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
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
  onSync?: () => Promise<void>;
}) {
  const [state, setState] = useState<ConfigurationState | null>(null);
  const [fields, setFields] = useState<TicketField[]>([]);
  const [providers, setProviders] = useState<Record<string, Integration>>({});
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [tracker, setTracker] = useState<TrackerSource>(EMPTY_TRACKER);
  const [tab, setTab] = useState<ConfigurationTab>(initialTab || (project ? "board" : "trackers"));
  const [provider, setProvider] = useState("github");
  const [busy, setBusy] = useState(false);
  const [connecting, setConnecting] = useState("");
  const [error, setError] = useState("");
  const [boards, setBoards] = useState<Board[] | null>(null);
  const [boardNotes, setBoardNotes] = useState<string[]>([]);
  const [boardError, setBoardError] = useState("");
  const [boardFilter, setBoardFilter] = useState("");
  const [choice, setChoice] = useState<Board | null>(null);
  const [linkSummary, setLinkSummary] = useState<string[]>([]);
  const githubConnected = Boolean(trackers?.github);

  function applyConfiguration(value: ConfigurationState) {
    setState(value);
    setFields(clone(value.documents["ticket-fields"]?.content.fields || []));
    const next = clone(value.documents.integrations?.content.providers || {});
    setProviders(next);
    setRepositories(clone(value.repositories.items || []));
    setTracker(clone(value.documents["tracker-source"]?.content || EMPTY_TRACKER));
    setProvider((current) => current in next ? current : Object.keys(next)[0] || "");
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
  const selected = providers[provider];
  const mappingRows = useMemo(() => Object.entries(selected?.fields || {}), [selected]);

  async function publish(kind: "ticket-fields" | "integrations") {
    const document = state?.documents[kind];
    if (!document) return setError("This configuration is not available in the current snapshot.");
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
      applyConfiguration(value);
      notify(kind === "ticket-fields" ? "Ticket fields published" : "Integration mappings published");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function linkBoard(board: Board) {
    if (!state || !project) return;
    setBusy(true);
    setError("");
    try {
      const value = await command<{ configuration: ConfigurationState; bunker: Bunker; link: { summary: string[]; items: number; fields_found: number } }>("application.project.link", {
        bunker_id: bunker.id,
        project_id: project.id,
        board,
        include_comments: tracker.include_comments,
      });
      applyConfiguration(value.configuration);
      setChoice(null);
      setLinkSummary(value.link.summary);
      onBunker(value.bunker);
      notify(`Linked ${board.title}. Sync tickets whenever you want the latest.`);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function publishRepositories() {
    if (!state || !project) return;
    setBusy(true);
    setError("");
    try {
      const value = await command<{
        configuration: ConfigurationState;
        bunker: Bunker;
      }>("application.bunker.repository.replace", {
        bunker_id: bunker.id,
        project_id: project.id,
        expected_revision: state.repositories.revision,
        repositories: repositories.map(({ id, name, canonical_remote, default_branch }) => ({
          id,
          name,
          canonical_remote,
          default_branch,
        })),
      });
      applyConfiguration(value.configuration);
      onBunker(value.bunker);
      notify("Project repositories published");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  function updateRepository(index: number, patch: Partial<Repository>) {
    setRepositories((values) => values.map((value, item) => item === index ? { ...value, ...patch } : value));
  }

  async function connectRepository(repository: Repository) {
    if (!project || !repository.id) return;
    setError("");
    try {
      const path = await chooseGitDirectory();
      if (!path) return;
      setConnecting(repository.id);
      const value = await command<{ bunker: Bunker }>("application.bunker.repository.attach", {
        bunker_id: bunker.id,
        project_id: project.id,
        binding_id: repository.id,
        path,
      });
      const configuration = await command<ConfigurationState>("application.bunker.configuration.get", {
        bunker_id: bunker.id,
        project_id: project.id,
      });
      applyConfiguration(configuration);
      onBunker(value.bunker);
      notify("Repository connected and fetched");
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
        <div><p className="eyebrow">{project ? `${project.key} · pinned project setup` : "Bunker defaults · new projects"}</p><h2>Configuration</h2></div>
        <button className="icon-control" onClick={onClose} aria-label="Back">←</button>
      </header>
      <nav>
        {project && <button className={tab === "board" ? "selected" : ""} onClick={() => setTab("board")}>Board</button>}
        {project && <button className={tab === "repositories" ? "selected" : ""} onClick={() => setTab("repositories")}>Repositories</button>}
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
          <div><h3>Board</h3><p>{state.documents["tracker-source"] ? `Linked to ${trackerSummary(state.documents["tracker-source"].content)}.` : "Pick the board this Project mirrors. Tickets, fields, and mappings follow from it."}</p></div>
          {state.documents["tracker-source"] && onSync && <button className="primary" disabled={busy} onClick={async () => { setBusy(true); try { await onSync(); } finally { setBusy(false); } }}>Sync tickets</button>}
        </div>
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
                const current = state.documents["tracker-source"] ? sameBoard(state.documents["tracker-source"].content, board) : false;
                const selected = choice ? sameBoard(choice, board) : current;
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
              <button className="primary" disabled={!canWrite || busy || !choice || (state.documents["tracker-source"] ? sameBoard(state.documents["tracker-source"].content, choice) : false)} onClick={() => choice && linkBoard(choice)}>{busy ? "Linking…" : state.documents["tracker-source"] ? "Change board" : "Link board"}</button>
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
            <div className="integration-actions"><button disabled={!canWrite} onClick={() => setProviders((values) => ({ ...values, github: { ...values.github, fields: { ...values.github.fields, [fields.find((field) => !values.github?.fields[field.key])?.key || ""]: "" } } }))}>+ Add mapping</button><button className="primary" disabled={!canWrite || busy} onClick={() => { setProvider("github"); publish("integrations"); }}>Publish mappings</button></div>
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
            <div className="integration-actions"><button disabled={!canWrite} onClick={() => setFields((values) => [...values, { key: `field_${values.length + 1}`, label: "New field", type: "string", required: false }])}>+ Add field</button><button className="primary" disabled={!canWrite || busy} onClick={() => publish("ticket-fields")}>Publish fields</button></div>
          </details>
        </div>
      </section>}

      {state && project && tab === "repositories" && <section className="config-surface">
        <div className="config-heading">
          <div><h3>Project repositories</h3><p>Allowed remotes and this computer's checkout.</p></div>
          <button className="primary" disabled={!canWrite || busy} onClick={publishRepositories}>{busy ? "Publishing…" : "Publish repositories"}</button>
        </div>
        <div className="repository-matrix" role="table" aria-label="Project repositories">
          <div className="repository-heading" role="row"><span>#</span><span>Name</span><span>Allowed remote</span><span>Base</span><span>This computer</span><span /></div>
          {repositories.map((repository, index) => <div className="repository-row" role="row" key={repository.id || `new-${index}`}>
            <span className="repository-index">{index + 1}</span>
            <input aria-label={`Repository ${index + 1} name`} value={repository.name} disabled={!canWrite} onChange={(event) => updateRepository(index, { name: event.target.value })} />
            <input aria-label={`Repository ${index + 1} allowed remote`} value={repository.canonical_remote || ""} disabled={!canWrite || Boolean(repository.local_path)} placeholder="github.com/owner/repository" onChange={(event) => updateRepository(index, { canonical_remote: event.target.value })} />
            <input aria-label={`Repository ${index + 1} base branch`} value={repository.default_branch || ""} disabled={!canWrite} placeholder="main" onChange={(event) => updateRepository(index, { default_branch: event.target.value })} />
            <div className="repository-local">
              {repository.local_path ? <span title={repository.local_path}>{repository.local_path}</span> : <span>Not connected</span>}
              <button disabled={!repository.id || connecting === repository.id} onClick={() => connectRepository(repository)}>{connecting === repository.id ? "Working…" : repository.local_path ? "Change" : "Connect"}</button>
              {repository.local_path && <button className="danger-control" disabled={connecting === repository.id} onClick={() => disconnectRepository(repository)}>Disconnect</button>}
            </div>
            <button aria-label={`Remove ${repository.name || `repository ${index + 1}`}`} disabled={!canWrite || Boolean(repository.local_path)} onClick={() => setRepositories((values) => values.filter((_, item) => item !== index))}>−</button>
          </div>)}
          {repositories.length === 0 && <div className="repository-empty">No repositories allowed</div>}
        </div>
        <button disabled={!canWrite} onClick={() => setRepositories((values) => [...values, { id: "", project_id: project.id, name: "New repository", canonical_remote: "", default_branch: "main", local_path: null }])}>+ Add repository</button>
      </section>}

      {state && !project && tab === "defaults" && <section className="config-surface">
        <div className="config-heading"><div><h3>Ticket fields</h3><p>New Projects copy these fields when created.</p></div><button className="primary" disabled={!canWrite || busy} onClick={() => publish("ticket-fields")}>Publish fields</button></div>
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
        <div className="config-heading"><div><h3>Field mappings</h3><p>Default matrices new Projects start from. Each row pairs one TicketBunker field with one external payload path.</p></div><button className="primary" disabled={!canWrite || busy || !selected} onClick={() => publish("integrations")}>Publish mappings</button></div>
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
    </aside>
  );
}
