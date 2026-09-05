import { FormEvent, useEffect, useMemo, useState } from "react";
import ConfigurationPanel, { type ConfigurationTab } from "./ConfigurationPanel";
import { command } from "./transport";
import { trackerSummary, type AppState, type Bunker, type Invitation, type Member, type Project, type TicketPage, type TicketReference } from "./types";
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

function ancestors(projects: Project[], id: string): Project[] {
  const byId = new Map(flatten(projects).map(({ project }) => [project.id, project]));
  const chain: Project[] = [];
  let current = byId.get(id);
  while (current?.parent_id && byId.has(current.parent_id)) {
    current = byId.get(current.parent_id);
    if (current) chain.unshift(current);
  }
  return chain;
}

function remoteLabel(bunker: Bunker) {
  if (!bunker.remote) return "Local";
  try {
    const host = new URL(bunker.remote.server_url).host;
    return bunker.remote.state === "ready" ? `Shared on ${host}` : `${host} needs attention`;
  } catch {
    return "Shared service needs attention";
  }
}

function roleLabel(role: string) {
  return role ? role[0].toUpperCase() + role.slice(1) : "";
}

function MembersPanel({
  bunker,
  onClose,
  notify,
}: {
  bunker: Bunker;
  onClose: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [canInvite, setCanInvite] = useState(Boolean(bunker.sharing?.can_invite));
  const [role, setRole] = useState("member");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setError("");
    command<{ members: Member[]; sharing?: Bunker["sharing"] }>("application.bunker.members", { bunker_id: bunker.id })
      .then((value) => {
        if (!active) return;
        setMembers(value.members);
        const invite = Boolean(value.sharing?.can_invite);
        setCanInvite(invite);
        if (invite) {
          return command<{ invitations: Invitation[] }>("application.bunker.invitations", { bunker_id: bunker.id })
            .then((listed) => active && setInvitations(listed.invitations));
        }
        return undefined;
      })
      .catch((reason) => active && setError(String(reason)));
    return () => { active = false; };
  }, [bunker.id]);

  async function invite() {
    setBusy(true);
    setError("");
    try {
      const value = await command<{ invitation: { link: string }; invitations: Invitation[] }>("application.bunker.invitation.create", { bunker_id: bunker.id, role });
      setLink(value.invitation.link);
      setInvitations(value.invitations);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }
  async function copyLink() {
    try {
      await navigator.clipboard.writeText(link);
      notify("Invitation link copied");
    } catch {
      notify("Copy was unavailable; select the link above and copy it.", true);
    }
  }
  async function revoke(id: string) {
    setBusy(true);
    setError("");
    try {
      const value = await command<{ invitations: Invitation[] }>("application.bunker.invitation.revoke", { bunker_id: bunker.id, invitation_id: id });
      setInvitations(value.invitations);
      notify("Invitation revoked");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }
  const pending = invitations.filter((value) => value.state === "pending");
  return (
    <aside className="action-drawer members-drawer" aria-label="Members">
      <header><h2>Members</h2><button className="icon-control" onClick={onClose} aria-label="Close">←</button></header>
      <p className="drawer-copy">{remoteLabel(bunker)}. {canInvite ? "Only Bunker owners can invite members or change shared setup." : "Ask a Bunker owner to invite people or change shared setup."}</p>
      {error && <div className="inline-error">{error}</div>}
      {!members && !error && <div className="ticket-loading">Loading members</div>}
      {members && <ul className="members-list">{members.map((member) => <li key={member.id}><span><strong>{member.display_name}</strong>{member.email && <small>{member.email}</small>}</span><span className="role-chip">{roleLabel(member.role)}</span></li>)}</ul>}
      {canInvite && <section className="invite-section">
        <h3>Invite</h3>
        <div className="invite-controls"><label>Role<select value={role} onChange={(event) => setRole(event.target.value)}><option value="member">Member</option><option value="owner">Owner</option></select></label><button className="primary" disabled={busy} onClick={invite}>Create invitation</button></div>
        {link && <div className="invitation-link"><p>Send this link. It works once and expires in seven days.</p><code>{link}</code><button onClick={copyLink}>Copy link</button></div>}
        {pending.length > 0 && <ul className="invitations-list">{pending.map((value) => <li key={value.id}><span><strong>{roleLabel(value.role)} invitation</strong><small>Expires {relativeTime(value.expires_at) === "just now" ? "soon" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(Date.parse(value.expires_at))}</small></span><button className="danger-control" disabled={busy} onClick={() => revoke(value.id)}>Revoke</button></li>)}</ul>}
      </section>}
    </aside>
  );
}

function count(value: number, singular: string, plural: string) {
  return `${value} ${value === 1 ? singular : plural}`;
}

function ProjectCatalog({
  bunker,
  projects,
  title,
  onProject,
}: {
  bunker: Bunker;
  projects: Project[];
  title: string;
  onProject: (project: Project) => void;
}) {
  const rows = flatten(projects);
  return (
    <section className="project-catalog" aria-label={title}>
      <header><h2>{title}</h2><span>{rows.length}</span></header>
      {rows.length === 0 ? (
        <div className="quiet-empty"><h3>No projects yet</h3><p>Add a project to hold tickets and attach repositories.</p></div>
      ) : (
        <div className="project-rows">
          {rows.map(({ project, depth }) => {
            const repositories = bunker.repositories.filter((value) => value.project_id === project.id);
            return (
              <button className="project-row" key={project.id} style={{ paddingLeft: `${4 + depth * 18}px` }} onClick={() => onProject(project)}>
                <span className="project-key">{project.key}</span>
                <span className="project-name"><strong>{project.name}</strong>{project.description && <small>{project.description}</small>}</span>
                <span className="project-repositories">
                  {repositories.length === 0
                    ? <small>No repositories</small>
                    : repositories.map((value) => <span key={value.id} className={value.local_path ? "connected" : ""} title={value.local_path ? `Connected at ${value.local_path}` : "Not connected on this computer"}>{value.name}</span>)}
                </span>
                <span className="project-count">{count(project.ticket_count ?? 0, "ticket", "tickets")}</span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

function TicketCatalog({
  bunker,
  project,
  onTicket,
  version = 0,
}: {
  bunker: Bunker;
  project?: Project;
  onTicket: (ticket: TicketReference) => void;
  version?: number;
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
  }, [bunker.id, project?.id, committedQuery, status, assignee, reporter, label, pageNumber, version]);

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

type ActionMode = "" | "create" | "join" | "project" | "share" | "members" | "config";

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
  if (!mode || mode === "config" || mode === "members") return null;

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
    } else if (mode === "share" && bunker) {
      await submit(
        "application.bunker.share",
        { ...values, bunker_id: bunker.id },
        `${bunker.name} is shared. Invite people from Members.`,
      );
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
}: {
  mode: ActionMode;
  bunker?: Bunker;
  project?: Project;
  busy: boolean;
  error: string;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
}) {
  const title = {
    create: "Create Bunker",
    join: "Join Bunker",
    project: project ? "Add subproject" : "Add project",
    share: "Share Bunker",
    members: "Members",
    config: "Configuration",
    "": "",
  }[mode];
  const submitLabel = { create: "Create Bunker", join: "Join Bunker", project: title, share: "Publish and share", members: "", config: "", "": "" }[mode];
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
          <p className="drawer-copy">Paste the link a Bunker owner sent you. TicketBunker signs this computer in to their server and installs a copy of the Bunker with its projects and setup already configured.</p>
          <label>Invitation link<input name="invitation" type="password" placeholder="ticketbunker://join?…" autoComplete="off" autoFocus required /></label>
        </>}
        {mode === "share" && <>
          <p className="drawer-copy">Publish this Bunker to a TicketBunker service you run. You become its owner; members join with invitation links. Run <code>tb serve</code> on a machine you control to get a server address and a one-time setup token.</p>
          <label>Server address<input name="server_url" type="url" placeholder="https://tickets.example.com" autoFocus required /></label>
          <label>Setup token<input name="setup_token" type="password" autoComplete="off" placeholder="Printed once by tb serve" /></label>
          <small className="field-note">Leave the token empty if this computer already joined a Bunker on that server.</small>
        </>}
        {mode === "project" && <>
          {project
            ? <p className="drawer-copy">Placed under {project.key} · {project.name}.</p>
            : <label>Parent<select name="parent_id"><option value="">Top level</option>{flatten(bunker?.projects || []).map(({ project: value, depth }) => <option key={value.id} value={value.id}>{"· ".repeat(depth)}{value.key} · {value.name}</option>)}</select></label>}
          <label>Project key<input name="key" placeholder="ECS" maxLength={32} required /></label>
          <label>Project name<input name="name" required /></label>
          <label>Description<textarea name="description" rows={3} /></label>
        </>}
        {error && <div className="inline-error">{error}</div>}
        <div className="form-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Working…" : submitLabel}</button></div>
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
  const [configTab, setConfigTab] = useState<ConfigurationTab | undefined>(undefined);
  const [syncing, setSyncing] = useState(false);
  const [catalogVersion, setCatalogVersion] = useState(0);
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

  async function syncTickets() {
    if (!bunker || !project) return;
    setSyncing(true);
    try {
      const result = await command<{ sync: { created: number; updated: number; unchanged: number; source: string }; bunkers: Bunker[] }>(
        "application.project.sync",
        { bunker_id: bunker.id, project_id: project.id },
      );
      onState({ ...state, bunkers: result.bunkers });
      setCatalogVersion((value) => value + 1);
      notify(`Synced ${result.sync.source}: ${result.sync.created} new, ${result.sync.updated} updated, ${result.sync.unchanged} unchanged`);
    } catch (reason) {
      notify(String(reason), true);
    } finally {
      setSyncing(false);
    }
  }

  function openConfiguration(tab?: ConfigurationTab) {
    setConfigTab(tab);
    setAction("config");
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
                <span>{value.name}</span><small>{value.project_count}</small>
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
            <div className="quiet-empty large"><h2>Create or join a Bunker</h2><p>A Bunker owns projects, tickets, configuration, and membership. Repositories attach to its projects afterward.</p></div>
          ) : (
            <div className="bunker-overview">
              {state.bunkers.map((value) => (
                <button key={value.id} onClick={() => onSelection({ bunkerId: value.id })}>
                  <span><strong>{value.name}</strong><small><i className={!value.remote || value.remote.state === "ready" ? "state-dot ready" : "state-dot"} />{remoteLabel(value)}{value.remote && value.sharing?.role ? ` · ${roleLabel(value.sharing.role)}` : ""}</small></span>
                  <span className="bunker-counts">{count(value.project_count, "project", "projects")} · {count(value.repository_count, "repository", "repositories")} · {count(value.ticket_count, "ticket", "tickets")}</span>
                </button>
              ))}
            </div>
          )}
          {legacy.length > 0 && <section className="migration-list"><header><p className="eyebrow">Migration</p><h2>Companion projects</h2></header><p>Import a read-only copy into the standalone database. The repository stays unchanged.</p>{legacy.map((value) => <div key={String(value.path)}><span><strong>{String(value.project || value.name || "Project")}</strong><small>{String(value.path || "")}</small></span><button disabled={Boolean(value.imported) || importing === value.path} onClick={() => importLegacy(value)}>{value.imported ? "Imported" : importing === value.path ? "Importing…" : "Import copy"}</button></div>)}</section>}
        </>}

        {bunker && !project && <>
          <header className="workspace-header">
            <div>
              <button className="breadcrumb" onClick={() => onSelection(null)}>Bunkers</button>
              <h1>{bunker.name}</h1>
              <p className="bunker-meta"><i className={!bunker.remote || bunker.remote.state === "ready" ? "state-dot ready" : "state-dot"} />{remoteLabel(bunker)}{bunker.remote && bunker.sharing?.role ? ` · ${roleLabel(bunker.sharing.role)}` : ""} · {count(bunker.repository_count, "repository", "repositories")} · {count(bunker.ticket_count, "ticket", "tickets")}</p>
            </div>
            <div className="header-actions">
              <button onClick={() => openConfiguration("trackers")}>Trackers</button>
              <button onClick={() => openConfiguration("defaults")}>Defaults</button>
              {bunker.remote ? <button onClick={() => setAction("members")}>Members</button> : <button onClick={() => setAction("share")}>Share Bunker</button>}
              <button className="primary" onClick={() => setAction("project")}>Add project</button>
            </div>
          </header>
          <ProjectCatalog bunker={bunker} projects={bunker.projects} title="Projects" onProject={(value) => onSelection({ bunkerId: bunker.id, projectId: value.id })} />
          <details className="storage-details"><summary>Storage</summary><p>{bunker.database}</p>{bunker.remote && <p>{bunker.remote.server_url} · {bunker.remote.remote_bunker_id}{bunker.sharing?.role ? ` · ${roleLabel(bunker.sharing.role)}` : ""}</p>}</details>
        </>}

        {bunker && project && <>
          <header className="workspace-header">
            <div>
              <nav className="breadcrumbs" aria-label="Location">
                <button className="breadcrumb" onClick={() => onSelection({ bunkerId: bunker.id })}>{bunker.name}</button>
                {ancestors(bunker.projects, project.id).map((value) => <button className="breadcrumb" key={value.id} onClick={() => onSelection({ bunkerId: bunker.id, projectId: value.id })}>{value.name}</button>)}
              </nav>
              <h1><span className="project-key">{project.key}</span>{project.name}</h1>
              {project.description && <p className="project-description">{project.description}</p>}
              <p className="tracker-line">{project.tracker ? trackerSummary(project.tracker) : "No board linked"}</p>
            </div>
            <div className="header-actions">
              <button onClick={() => openConfiguration()}>Configure</button>
              {project.tracker
                ? <button disabled={syncing} onClick={syncTickets}>{syncing ? "Syncing…" : "Sync tickets"}</button>
                : <button onClick={() => openConfiguration("board")}>Connect a board</button>}
              <button className="primary" onClick={() => setAction("project")}>Add subproject</button>
            </div>
          </header>
          {(project.children || []).length > 0 && <ProjectCatalog bunker={bunker} projects={project.children || []} title="Subprojects" onProject={(value) => onSelection({ bunkerId: bunker.id, projectId: value.id })} />}
          <TicketCatalog bunker={bunker} project={project} version={catalogVersion} onTicket={(ticket) => onTicket(bunker.id, ticket)} />
        </>}
      </main>
      <ActionPanel mode={action} bunker={bunker} project={project} onClose={() => setAction("")} state={state} onState={onState} notify={notify} />
      {action === "members" && bunker && <MembersPanel bunker={bunker} onClose={() => setAction("")} notify={notify} />}
      {action === "config" && bunker && <ConfigurationPanel bunker={bunker} project={project} trackers={state.trackers} initialTab={configTab} onClose={() => { setAction(""); setConfigTab(undefined); }} onBunker={(value) => onState({ ...state, bunkers: state.bunkers.map((item) => item.id === value.id ? value : item) })} onConnected={(trackers, _connection, refreshed) => onState({ ...state, trackers, bunkers: refreshed ? state.bunkers.map((item) => item.id === refreshed.id ? refreshed : item) : state.bunkers })} onSync={syncTickets} notify={notify} />}
    </div>
  );
}
