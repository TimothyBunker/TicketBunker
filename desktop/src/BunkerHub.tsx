import { FormEvent, useEffect, useMemo, useState } from "react";
import ConfigurationPanel from "./ConfigurationPanel";
import { chooseGitDirectory, command } from "./transport";
import type { AppState, Bunker, Project, TicketPage, TicketReference } from "./types";
import { relativeTime } from "./report";

type Selection = { bunkerId: string; projectId?: string };

type Props = {
  state: AppState;
  selection: Selection | null;
  onSelection: (value: Selection | null) => void;
  onTicket: (bunkerId: string, ticket: TicketReference) => void;
  onState: (state: AppState) => void;
  onSettings: () => void;
  notify: (message: string, error?: boolean) => void;
};

function flatten(projects: Project[], depth = 0): Array<{ project: Project; depth: number }> {
  return projects.flatMap((project) => [
    { project, depth },
    ...flatten(project.children || [], depth + 1),
  ]);
}

function remoteLabel(bunker: Bunker) {
  if (!bunker.remote) return "Local";
  try {
    const host = new URL(bunker.remote.server_url).host;
    return bunker.remote.state === "ready" ? host : `${host} needs attention`;
  } catch {
    return "Remote needs attention";
  }
}

function TicketCatalog({
  bunker,
  project,
  onTicket,
}: {
  bunker: Bunker;
  project?: Project;
  onTicket: (ticket: TicketReference) => void;
}) {
  const [page, setPage] = useState<TicketPage | null>(null);
  const [query, setQuery] = useState("");
  const [committedQuery, setCommittedQuery] = useState("");
  const [status, setStatus] = useState("active");
  const [assignee, setAssignee] = useState("");
  const [reporter, setReporter] = useState("");
  const [label, setLabel] = useState("");
  const [pageNumber, setPageNumber] = useState(1);
  const [error, setError] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setCommittedQuery(query), 220);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => setPageNumber(1), [bunker.id, project?.id, committedQuery, status, assignee, reporter, label]);

  useEffect(() => {
    let active = true;
    setError("");
    command<TicketPage>("application.ticket.list", {
      bunker_id: bunker.id,
      project_id: project?.id || "",
      search: committedQuery,
      status,
      assignee,
      reporter,
      label,
      page: pageNumber,
      page_size: 25,
    })
      .then((value) => active && setPage(value))
      .catch((reason) => active && setError(String(reason)))
    return () => {
      active = false;
    };
  }, [bunker.id, project?.id, committedQuery, status, assignee, reporter, label, pageNumber]);

  const groups = useMemo(() => {
    const values = new Map<string, TicketReference[]>();
    for (const ticket of page?.tickets || []) {
      const key = ticket.status || "Open";
      values.set(key, [...(values.get(key) || []), ticket]);
    }
    return [...values.entries()];
  }, [page]);

  return (
    <section className="catalog" aria-label="Tickets">
      <div className="catalog-toolbar">
        <label className="search-field">
          <span className="sr-only">Search tickets</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search tickets"
          />
        </label>
        <select aria-label="Status" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="active">Active</option>
          <option value="all">All statuses</option>
          {(page?.facets.statuses || []).map((value) => (
            <option key={value} value={value.toLowerCase()}>{value}</option>
          ))}
        </select>
        <select aria-label="Assignee" value={assignee} onChange={(event) => setAssignee(event.target.value)}>
          <option value="">Any assignee</option>
          {(page?.facets.assignees || []).map((value) => <option key={value}>{value}</option>)}
        </select>
        <select aria-label="Reporter" value={reporter} onChange={(event) => setReporter(event.target.value)}>
          <option value="">Any reporter</option>
          {(page?.facets.reporters || []).map((value) => <option key={value}>{value}</option>)}
        </select>
        <select aria-label="Label" value={label} onChange={(event) => setLabel(event.target.value)}>
          <option value="">Any label</option>
          {(page?.facets.labels || []).map((value) => <option key={value}>{value}</option>)}
        </select>
      </div>
      {error && <div className="inline-error">{error}</div>}
      {!page && !error && <div className="ticket-loading">Loading tickets</div>}
      {page && page.total === 0 && (
        <div className="quiet-empty">
          <h2>No tickets here</h2>
          <p>Change the filters or sync tickets from a configured source.</p>
        </div>
      )}
      {groups.map(([group, tickets]) => (
        <section className="status-group" key={group}>
          <header><h2>{group}</h2><span>{tickets.length}</span></header>
          <div className="ticket-rows">
            {tickets.map((ticket) => (
              <button className="ticket-row" key={ticket.id} onClick={() => onTicket(ticket)}>
                <span className="ticket-key">{ticket.id}</span>
                <span className="ticket-title">{ticket.title}</span>
                <span className="ticket-labels">
                  {ticket.labels.slice(0, 2).map((value) => <span key={value}>{value}</span>)}
                </span>
                <span className="ticket-person">{ticket.assignee || "Unassigned"}</span>
                <span className="ticket-updated">{relativeTime(ticket.updated)}</span>
              </button>
            ))}
          </div>
        </section>
      ))}
      {page && page.page_count > 1 && (
        <nav className="pagination" aria-label="Ticket pages">
          <button disabled={page.page <= 1} onClick={() => setPageNumber(page.page - 1)}>Previous</button>
          <span>{page.page} of {page.page_count}</span>
          <button disabled={page.page >= page.page_count} onClick={() => setPageNumber(page.page + 1)}>Next</button>
        </nav>
      )}
    </section>
  );
}

type ActionMode = "" | "create" | "join" | "project" | "attach" | "remote" | "config";

function ActionPanel({
  mode,
  bunker,
  project,
  onClose,
  onState,
  state,
  notify,
}: {
  mode: ActionMode;
  bunker?: Bunker;
  project?: Project;
  onClose: () => void;
  onState: (state: AppState) => void;
  state: AppState;
  notify: (message: string, error?: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!mode || mode === "config") return null;

  async function submit(action: string, payload: Record<string, unknown>, success: string) {
    setBusy(true);
    setError("");
    try {
      const result = await command<{ bunkers?: Bunker[]; bunker?: Bunker }>(action, payload);
      onState({ ...state, bunkers: result.bunkers || state.bunkers });
      notify(success);
      onClose();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function handle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    if (mode === "create") {
      await submit("application.bunker.create", values, `Created ${values.name}`);
    } else if (mode === "join") {
      await submit("application.bunker.join", values, "Bunker joined");
    } else if (mode === "project" && bunker) {
      await submit(
        "application.bunker.project.create",
        { ...values, bunker_id: bunker.id, parent_id: project?.id || values.parent_id || "" },
        `Added ${values.name}`,
      );
    } else if (mode === "remote" && bunker) {
      await submit(
        "application.bunker.remote.configure",
        { ...values, bunker_id: bunker.id },
        "Remote relationship saved",
      );
    } else if (mode === "attach" && bunker) {
      await submit(
        "application.bunker.repository.attach",
        { ...values, bunker_id: bunker.id, project_id: project?.id || values.project_id },
        "Repository attached",
      );
    }
  }

  async function pickDirectory(setValue: (path: string) => void) {
    try {
      const path = await chooseGitDirectory();
      if (path) setValue(path);
    } catch (reason) {
      setError(String(reason));
    }
  }

  return (
    <ActionForm
      mode={mode}
      bunker={bunker}
      project={project}
      busy={busy}
      error={error}
      onSubmit={handle}
      onClose={onClose}
      onPick={pickDirectory}
    />
  );
}

function ActionForm({
  mode,
  bunker,
  project,
  busy,
  error,
  onSubmit,
  onClose,
  onPick,
}: {
  mode: ActionMode;
  bunker?: Bunker;
  project?: Project;
  busy: boolean;
  error: string;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
  onPick: (setter: (path: string) => void) => void;
}) {
  const [path, setPath] = useState("");
  const title = {
    create: "Create Bunker",
    join: "Join Bunker",
    project: "Add project",
    attach: "Attach repository",
    remote: "Connect remote service",
    config: "Configuration",
    "": "",
  }[mode];
  return (
    <aside className="action-drawer" aria-label={title}>
      <header><h2>{title}</h2><button className="icon-control" onClick={onClose} aria-label="Close">←</button></header>
      <form onSubmit={onSubmit}>
        {mode === "create" && <>
          <label>Bunker name<input name="name" autoFocus required /></label>
          <label>First project key<input name="project_key" placeholder="ECS" maxLength={32} required /></label>
          <label>First project name<input name="project_name" placeholder="Entity Component System" required /></label>
        </>}
        {mode === "join" && <>
          <label>Invitation<input name="invitation" type="password" autoFocus required /></label>
          <label>Authorization, if requested<input name="authorization" type="password" autoComplete="off" /></label>
          <label>Credential reference<input name="credential_reference" placeholder="System credential store reference" /></label>
        </>}
        {mode === "project" && <>
          {!project && <label>Parent<select name="parent_id"><option value="">Top level</option>{flatten(bunker?.projects || []).map(({ project: value, depth }) => <option key={value.id} value={value.id}>{"· ".repeat(depth)}{value.key} · {value.name}</option>)}</select></label>}
          <label>Project key<input name="key" placeholder="ECS" maxLength={32} required /></label>
          <label>Project name<input name="name" required /></label>
          <label>Description<textarea name="description" rows={3} /></label>
        </>}
        {mode === "attach" && <>
          {!project && <label>Project<select name="project_id" required>{flatten(bunker?.projects || []).map(({ project: value, depth }) => <option key={value.id} value={value.id}>{"· ".repeat(depth)}{value.key} · {value.name}</option>)}</select></label>}
          <label>Git project folder<div className="path-field"><input name="path" value={path} onChange={(event) => setPath(event.target.value)} required /><button type="button" onClick={() => onPick(setPath)}>Browse</button></div></label>
          <label>Repository name<input name="name" placeholder="Defaults to folder name" /></label>
          <label>Base branch<input name="default_branch" placeholder="main" /></label>
        </>}
        {mode === "remote" && <>
          <p className="drawer-copy">TicketBunker connects to a service API, not directly to its database.</p>
          <label>Server<input name="server_url" type="url" defaultValue={bunker?.remote?.server_url || ""} placeholder="https://tickets.example.com" required /></label>
          <label>Remote Bunker ID<input name="remote_bunker_id" defaultValue={bunker?.remote?.remote_bunker_id || ""} required /></label>
          <label>Credential reference<input name="credential_reference" /></label>
        </>}
        {error && <div className="inline-error">{error}</div>}
        <div className="form-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Working…" : title}</button></div>
      </form>
    </aside>
  );
}

export default function BunkerHub({
  state,
  selection,
  onSelection,
  onTicket,
  onState,
  onSettings,
  notify,
}: Props) {
  const [action, setAction] = useState<ActionMode>("");
  const [importing, setImporting] = useState("");
  const bunker = state.bunkers.find((value) => value.id === selection?.bunkerId);
  const projects = bunker ? flatten(bunker.projects) : [];
  const project = projects.find((value) => value.project.id === selection?.projectId)?.project;
  const legacy = (state.legacy_projects || []).filter((value) => value.initialized && !value.missing);

  async function importLegacy(value: Record<string, unknown>) {
    const path = String(value.path || "");
    setImporting(path);
    try {
      const result = await command<{ bunkers: Bunker[] }>("application.bunker.import.companion", { path });
      onState({
        ...state,
        bunkers: result.bunkers,
        legacy_projects: (state.legacy_projects || []).map((item) =>
          item.path === path ? { ...item, imported: true } : item
        ),
      });
      notify("Imported a read-only copy into a local Bunker");
    } catch (reason) {
      notify(String(reason), true);
    } finally {
      setImporting("");
    }
  }

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">TB</span><span>TicketBunker</span></div>
        <nav aria-label="Bunkers and projects">
          <button className={!selection ? "nav-item selected" : "nav-item"} onClick={() => onSelection(null)}>Bunkers</button>
          {state.bunkers.map((value) => (
            <div className="bunker-nav" key={value.id}>
              <button className={selection?.bunkerId === value.id && !selection.projectId ? "nav-item selected" : "nav-item"} onClick={() => onSelection({ bunkerId: value.id })}>
                <span>{value.name}</span><small>{value.ticket_count}</small>
              </button>
              {flatten(value.projects).map(({ project: item, depth }) => (
                <button
                  key={item.id}
                  style={{ paddingLeft: `${30 + depth * 14}px` }}
                  className={selection?.projectId === item.id ? "nav-project selected" : "nav-project"}
                  onClick={() => onSelection({ bunkerId: value.id, projectId: item.id })}
                >
                  <span className="project-key">{item.key}</span><span>{item.name}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>
        <button className="profile-control" onClick={onSettings}>
          <span>{state.profile.name.split(/\s+/).map((part) => part[0]).slice(0, 2).join("")}</span>
          <span><strong>{state.profile.name}</strong><small>{state.release?.available ? "Update available" : "Local settings"}</small></span>
        </button>
      </aside>

      <main className="workspace">
        {!selection && <>
          <header className="workspace-header">
            <div><p className="eyebrow">Your work</p><h1>Bunkers</h1></div>
            <div className="header-actions"><button onClick={() => setAction("join")}>Join</button><button className="primary" onClick={() => setAction("create")}>Create Bunker</button></div>
          </header>
          {state.bunkers.length === 0 ? (
            <div className="quiet-empty large"><h2>Create or join a Bunker</h2><p>A Bunker owns projects, tickets, configuration, and membership. Repositories attach afterward.</p></div>
          ) : (
            <div className="bunker-overview">
              {state.bunkers.map((value) => (
                <button key={value.id} onClick={() => onSelection({ bunkerId: value.id })}>
                  <span><strong>{value.name}</strong><small><i className={value.remote?.state === "ready" ? "state-dot ready" : "state-dot"} />{remoteLabel(value)}</small></span>
                  <span className="bunker-counts">{value.project_count} projects · {value.ticket_count} tickets</span>
                </button>
              ))}
            </div>
          )}
          {legacy.length > 0 && <section className="migration-list"><header><p className="eyebrow">Migration</p><h2>Companion projects</h2></header><p>Import a read-only copy into the standalone database. The repository stays unchanged.</p>{legacy.map((value) => <div key={String(value.path)}><span><strong>{String(value.project || value.name || "Project")}</strong><small>{String(value.path || "")}</small></span><button disabled={Boolean(value.imported) || importing === value.path} onClick={() => importLegacy(value)}>{value.imported ? "Imported" : importing === value.path ? "Importing…" : "Import copy"}</button></div>)}</section>}
        </>}

        {bunker && <>
          <header className="workspace-header">
            <div>
              <button className="breadcrumb" onClick={() => onSelection(null)}>{bunker.name}</button>
              <h1>{project?.name || "Tickets"}</h1>
            </div>
            <div className="header-actions">
              <button onClick={() => setAction("config")}>Configure</button>
              <button onClick={() => setAction("remote")}>{bunker.remote ? "Remote" : "Connect remote"}</button>
              <button onClick={() => setAction("attach")}>Attach repository</button>
              <button className="primary" onClick={() => setAction("project")}>Add project</button>
            </div>
          </header>
          <TicketCatalog bunker={bunker} project={project} onTicket={(ticket) => onTicket(bunker.id, ticket)} />
          <details className="storage-details"><summary>Storage</summary><p>{bunker.database}</p>{bunker.remote && <p>{bunker.remote.server_url} · {bunker.remote.remote_bunker_id}</p>}</details>
        </>}
      </main>
      <ActionPanel mode={action} bunker={bunker} project={project} onClose={() => setAction("")} state={state} onState={onState} notify={notify} />
      {action === "config" && bunker && <ConfigurationPanel bunker={bunker} project={project} onClose={() => setAction("")} notify={notify} />}
    </div>
  );
}
