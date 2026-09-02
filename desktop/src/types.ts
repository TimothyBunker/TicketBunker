export type Project = {
  id: string;
  parent_id?: string | null;
  key: string;
  name: string;
  description?: string;
  children?: Project[];
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
  projects: Project[];
  repositories: Repository[];
  project_count: number;
  repository_count: number;
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

export type TicketDetail = {
  ticket: TicketReference & Record<string, unknown>;
  project: Project;
  repositories: Repository[];
  branches: Array<{
    name: string;
    current: boolean;
    remote: boolean;
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
  };
};
