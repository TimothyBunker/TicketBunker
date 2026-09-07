export type TrackerSource = {
  id?: string;
  provider: string;
  kind: string;
  owner: string;
  number: number;
  repository: string;
  include_comments: boolean;
  title?: string;
  url?: string;
};

export type Board = {
  kind: string;
  owner: string;
  number: number;
  repository: string;
  title: string;
  url: string;
  open_issues?: number;
  private?: boolean;
};

export type Project = {
  id: string;
  parent_id?: string | null;
  key: string;
  name: string;
  description?: string;
  children?: Project[];
  ticket_count?: number;
  tracker?: TrackerSource | null;
  trackers?: TrackerSource[];
};

export type Repository = {
  id: string;
  project_id: string;
  name: string;
  canonical_remote?: string;
  default_branch?: string;
  local_path?: string | null;
  attached_at?: string | null;
  last_fetched_at?: string | null;
};

export type Bunker = {
  id: string;
  name: string;
  mode: "local" | "remote";
  available?: boolean;
  database: string;
  remote?: {
    server_url: string;
    remote_bunker_id: string;
    state: string;
    last_synced_at?: string;
  } | null;
  membership?: { id: string; display_name: string; email?: string; role: string; state?: string } | null;
  sharing?: {
    state: string;
    role: string;
    is_owner: boolean;
    can_share: boolean;
    can_invite: boolean;
    can_manage_members: boolean;
    server: string;
  };
  connections?: Record<string, { account: string; name?: string }>;
  projects: Project[];
  repositories: Repository[];
  project_count: number;
  repository_count: number;
  repository_registry?: { revision: string; items: Repository[] };
  ticket_count: number;
};

export type AppState = {
  ok?: boolean;
  profile: {
    name: string;
    email: string;
    branch_user: string;
    complete: boolean;
  };
  agent: {
    enabled: boolean;
    provider: string;
    model: string;
    configured: boolean;
    decided: boolean;
  };
  platform: {
    family: string;
    architecture: string;
    runtime_key: string;
    data_home: string;
  };
  diagnostics?: { application?: string };
  trackers?: Record<string, boolean>;
  release?: {
    current: string;
    latest: string;
    available: boolean;
    checked_at: string;
    attempted_at: string;
    release_url: string;
    download_url: string;
    asset: string;
  };
  bunkers: Bunker[];
  legacy_projects?: Array<Record<string, unknown>>;
  launch?: { repository?: string | null; route?: string };
};

export type TicketReference = {
  id: string;
  title: string;
  status: string;
  type: string;
  priority: string;
  assignee: string;
  reporter: string;
  labels: string[];
  updated: string;
  project: { id: string; key: string; name: string };
  source_kind: string;
  source_reference: string;
};

export type TicketPage = {
  tickets: TicketReference[];
  total: number;
  page: number;
  page_size: number;
  page_count: number;
  facets: {
    statuses: string[];
    assignees: string[];
    reporters: string[];
    labels: string[];
  };
};

export type EffectiveChecks = { build: string; test: string; timeout_seconds: number; source: "ticket" | "project" | "bunker" | "discovered" | "none"; detail?: string };

export type TicketDetail = {
  selected_repository_id?: string;
  ticket: TicketReference & Record<string, unknown>;
  project: Project;
  checks?: EffectiveChecks;
  check_results?: { branch: string; commit: string; checked_at: string; stale: boolean | null;
    results: Array<{ kind: string; command: string; passed: boolean; exit: number | null; timed_out: boolean; seconds: number; first_error: string; log_text: string }> };
  repositories: Repository[];
  branches: Array<{
    name: string;
    current: boolean;
    local: boolean;
    remote: boolean;
    remotes: string[];
    upstream: string;
    commit: string;
    last_commit_at: string;
    last_author: string;
    ahead: number | null;
    behind: number | null;
    base: string;
    repository_id: string;
    repository_name: string;
  }>;
  reports: Array<{ scope: string; repository_id?: string; content: string; updated_at: string }>;
  comments: Array<Record<string, unknown>>;
  activity: Array<Record<string, unknown>>;
  truth?: Record<string, unknown>;
};

export type SourceDraft = {
  path: string;
  line: number;
  content: string;
  digest: string;
  branch: string;
  settings: {
    tab_size?: number;
    indent_style?: string;
    line_wrap?: boolean;
  };
};

export type TicketField = {
  key: string;
  label: string;
  type: string;
  required: boolean;
  values?: string[];
};

export type Integration = {
  label: string;
  template: string;
  fields: Record<string, string>;
};

export type ConfigurationState = {
  repository_registry?: { revision: string; items: Repository[] };
  scope_type: "bunker" | "project";
  scope_id: string;
  composition: "pinned" | "defaults-for-new-projects";
  permission: {
    can_write: boolean;
    reason: string;
    capabilities: string[];
  };
  repositories: {
    revision: string;
    items: Repository[];
  };
  documents: {
    "ticket-fields"?: {
      kind: "ticket-fields";
      revision: string;
      updated_at: string;
      content: { fields: TicketField[] };
    };
    integrations?: {
      kind: "integrations";
      revision: string;
      updated_at: string;
      content: { providers: Record<string, Integration> };
    };
    "tracker-source"?: {
      kind: "tracker-source";
      revision: string;
      updated_at: string;
      content: TrackerSource;
    };
    "tracker-sources"?: {
      kind: "tracker-sources";
      revision: string;
      updated_at: string;
      content: { sources: TrackerSource[] };
    };
    connections?: {
      kind: "connections";
      revision: string;
      updated_at: string;
      content: { providers: Record<string, { account: string; name?: string }> };
    };
    checks?: {
      kind: "checks";
      revision: string;
      updated_at: string;
      content: { build: string; test: string; timeout_seconds: number; discover?: boolean; tickets?: Record<string, { build: string; test: string }> };
    };
  };
};

export function linkedBoards(documents: ConfigurationState["documents"]): TrackerSource[] {
  const plural = documents["tracker-sources"]?.content.sources;
  if (plural) return plural;
  const single = documents["tracker-source"]?.content;
  return single ? [{ ...single, id: single.id || "legacy" }] : [];
}

export function trackersSummary(trackers: TrackerSource[] | null | undefined): string {
  const values = (trackers || []).map(trackerSummary).filter(Boolean);
  if (values.length === 0) return "";
  return values.length === 1 ? values[0] : `${values[0]} +${values.length - 1} more`;
}

export function trackerSummary(tracker: TrackerSource | null | undefined): string {
  if (!tracker) return "";
  const label = tracker.provider === "github" ? "GitHub" : tracker.provider;
  if (tracker.title) return `${label} · ${tracker.title}`;
  return tracker.kind === "project"
    ? `${label} Project #${tracker.number} · ${tracker.owner}`
    : `${label} issues · ${tracker.owner}/${tracker.repository}`;
}

export type Member = {
  id: string;
  display_name: string;
  email: string;
  role: string;
  state: string;
};

export type Invitation = {
  id: string;
  role: string;
  state: string;
  created_at: string;
  created_by: string;
  expires_at: string;
};
