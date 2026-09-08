import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BunkerHub from "./BunkerHub";
import { clearBoardCache } from "./boardCache";
import type { AppState, ConfigurationState } from "./types";

const transport = vi.hoisted(() => ({
  command: vi.fn(),
  chooseGitDirectory: vi.fn(),
}));

vi.mock("./transport", () => transport);

const project = { id: "ecs", key: "ECS", name: "Entity Component System", children: [], ticket_count: 3 };
const repository = {
  id: "coal",
  project_id: "ecs",
  name: "Coal",
  canonical_remote: "github.com/Example/coal",
  default_branch: "main",
  local_path: null,
};
const bunker = {
  id: "engineering",
  name: "Engineering",
  mode: "local" as const,
  database: "/data/engineering.sqlite3",
  remote: null,
  projects: [project],
  repositories: [repository],
  project_count: 1,
  repository_count: 1,
  ticket_count: 3,
};
const state: AppState = {
  profile: { name: "Tim Bunker", email: "tim@example.test", branch_user: "tim", complete: true },
  agent: { enabled: true, provider: "openai", model: "gpt", configured: true, decided: true },
  platform: { family: "linux", architecture: "x64", runtime_key: "linux-x64", data_home: "/data" },
  bunkers: [bunker],
};
const configuration: ConfigurationState = {
  scope_type: "project",
  scope_id: "ecs",
  composition: "pinned",
  permission: { can_write: true, reason: "Local", capabilities: ["config:write"] },
  repositories: {
    revision: "repositories:one",
    items: [repository],
  },
  documents: {
    "ticket-fields": { kind: "ticket-fields", revision: "1", updated_at: "", content: { fields: [] } },
    integrations: { kind: "integrations", revision: "1", updated_at: "", content: { providers: {} } },
  },
};

describe("Bunker and Project boundaries", () => {
  afterEach(cleanup);

  beforeEach(() => {
    clearBoardCache();
    transport.chooseGitDirectory.mockReset();
    transport.command.mockReset();
    transport.command.mockImplementation((action: string) => {
      if (action === "application.ticket.list") {
        return Promise.resolve({
          tickets: [], total: 0, page: 1, page_size: 25, page_count: 0,
          facets: { statuses: [], assignees: [], reporters: [], labels: [] },
        });
      }
      if (action === "application.bunker.configuration.get") return Promise.resolve(configuration);
      throw new Error(`Unexpected command ${action}`);
    });
  });

  it("uses compact navigation and names setup issues on the settings gear", () => {
    const { container } = render(<BunkerHub state={state} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />);
    expect(container.querySelector(".sidebar .brand")).toBeNull();
    expect(screen.getByRole("button", { name: "All Bunkers" }).textContent).toBe("");
    expect(screen.getByRole("button", { name: "Create Bunker" })).toHaveAttribute("title", "Create Bunker");
    expect(screen.getByRole("button", { name: "Configure" })).toHaveAttribute("title", expect.stringContaining("No board linked"));
    expect(screen.getByRole("button", { name: "Add subproject" }).textContent).toBe("");
  });

  it("renames through a revision-checked context action without changing the Project key", async () => {
    const onState = vi.fn(); const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((name, payload) => {
      if (name === "application.bunker.project.inspect") return Promise.resolve({ revision: "project:1", tickets: 3, subprojects: 0, permission: { can_write: true } });
      if (name === "application.bunker.project.rename") return Promise.resolve({ bunker: { ...bunker, projects: [{ ...project, name: payload.name }] } });
      return original(name, payload);
    });
    render(<BunkerHub state={state} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={onState} onSettings={vi.fn()} notify={vi.fn()} />);
    fireEvent.contextMenu(screen.getByRole("button", { name: "ECS Entity Component System" }));
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Edit", "Rename", "Delete"]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename Project" });
    fireEvent.change(within(dialog).getByLabelText("Project name"), { target: { value: "Engine" } });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Rename" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.project.rename", { bunker_id: bunker.id, project_id: project.id, expected_revision: "project:1", name: "Engine" }));
    expect(onState).toHaveBeenCalledWith(expect.objectContaining({ bunkers: [expect.objectContaining({ projects: [expect.objectContaining({ key: "ECS", name: "Engine" })] })] }));
  });

  it("requires explicit confirmation for empty-Project deletion", async () => {
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((name, payload) => {
      if (name === "application.bunker.project.inspect") return Promise.resolve({ revision: "project:1", tickets: 0, subprojects: 0, permission: { can_write: true } });
      if (name === "application.bunker.project.delete") return Promise.resolve({ bunker: { ...bunker, projects: [] } });
      return original(name, payload);
    });
    render(<BunkerHub state={state} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />);
    fireEvent.keyDown(screen.getByRole("button", { name: "ECS Entity Component System" }), { key: "F10", shiftKey: true });
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Project" });
    expect(dialog).toHaveTextContent("Local Git folders and tracker issues stay untouched");
    expect(transport.command.mock.calls.some(([name]) => name.endsWith(".delete"))).toBe(false);
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Delete" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.project.delete", expect.objectContaining({ confirmed: true, expected_revision: "project:1" })));
  });

  it("creates a local Bunker with remotes but configures availability separately", async () => {
    transport.command.mockResolvedValue({ bunkers: [bunker], bunker });
    render(<BunkerHub state={{ ...state, bunkers: [] }} selection={null} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Create Bunker" })[0]);
    const drawer = within(screen.getByRole("complementary", { name: "Create Bunker" }));
    fireEvent.change(drawer.getByLabelText("Bunker name"), { target: { value: "Navigation" } });
    fireEvent.change(drawer.getByLabelText("Allowed Git remotes"), { target: { value: "https://github.com/example/navigation.git\nhttps://github.com/example/tools.git" } });
    fireEvent.change(drawer.getByLabelText("First project key"), { target: { value: "NAV" } });
    fireEvent.change(drawer.getByLabelText("First project name"), { target: { value: "Navigation" } });
    expect(drawer.queryByLabelText("First project repository")).not.toBeInTheDocument();
    fireEvent.click(drawer.getByRole("button", { name: "Create Bunker" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.create", expect.objectContaining({
      repositories: [{ canonical_remote: "https://github.com/example/navigation.git", default_branch: "main" }, { canonical_remote: "https://github.com/example/tools.git", default_branch: "main" }],
    })));
  });

  it("offers a manual local path when no native picker exists", async () => {
    transport.chooseGitDirectory.mockResolvedValue(null);
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((name, ...args) => name === "application.bunker.repository.attach" ? Promise.resolve({ bunker }) : original(name, ...args));
    render(<BunkerHub state={state} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    fireEvent.click(await screen.findByRole("button", { name: "Repositories" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add local repository" }));
    fireEvent.change(await screen.findByLabelText("Local repository path"), { target: { value: "/work/coal" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect repository" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.repository.attach", { bunker_id: bunker.id, project_id: project.id, path: "/work/coal" }));
  });

  it("clears search and filters in one click", async () => {
    render(<BunkerHub state={state} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Search tickets"), { target: { value: "absent" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "all" } });
    fireEvent.click(screen.getByText("Clear filters"));
    expect(screen.getByLabelText("Search tickets")).toHaveValue("");
    expect(screen.getByLabelText("Status")).toHaveValue("active");
    await waitFor(() => expect(transport.command).toHaveBeenLastCalledWith("application.ticket.list", expect.objectContaining({ search: "", status: "active", page: 1 })));
  });

  it("guards sidebar navigation while configuration contains a draft", async () => {
    const select = vi.fn();
    render(<BunkerHub state={state} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={select} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    fireEvent.click(await screen.findByRole("button", { name: "Checks" }));
    fireEvent.change(screen.getByLabelText("Build command"), { target: { value: "make navigation" } });
    fireEvent.click(screen.getByRole("button", { name: "All Bunkers" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(select).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Do not save"));
    expect(select).toHaveBeenCalledWith(null);
    expect(screen.queryByRole("heading", { name: "Configuration" })).not.toBeInTheDocument();
  });

  it("keeps failed sync visible until an explicit retry succeeds", async () => {
    const shared = { ...bunker, id: "offline-qa", mode: "remote" as const,
      remote: { server_url: "https://tickets.example.test", remote_bunker_id: "offline-qa", state: "ready", last_synced_at: "2026-09-01T00:00:00Z" } };
    transport.command.mockRejectedValue(new Error("Service unavailable. Retry sync."));
    const { container } = render(<BunkerHub state={{ ...state, bunkers: [shared] }} selection={{ bunkerId: shared.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Sync failed");
    expect(container.querySelector(".bunker-meta .failed")).toBeInTheDocument();
    transport.command.mockResolvedValue({ sync: { tickets: 3, reports: 0 }, bunkers: [shared] });
    fireEvent.click(screen.getByRole("button", { name: "Retry sync" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(container.querySelector(".bunker-meta .ready")).toBeInTheDocument();
  });

  it("keeps sharing controls at Bunker level", () => {
    const { rerender } = render(
      <BunkerHub state={state} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );

    expect(screen.queryByRole("button", { name: "Share Bunker" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Members" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Attach repository" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add subproject" })).toBeInTheDocument();

    rerender(
      <BunkerHub state={state} selection={{ bunkerId: bunker.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );
    fireEvent.click(screen.getByLabelText("Bunker settings"));
    expect(screen.getByRole("button", { name: "Share Bunker" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Members" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Attach repository" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Share Bunker" }));
    expect(screen.getByLabelText("Server address")).toBeInTheDocument();
    expect(screen.getByLabelText("Setup token")).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Share Bunker" })).toBeInTheDocument();
  });

  it("gives a shared Bunker's owner Members with invitation links", async () => {
    const shared = {
      ...bunker,
      mode: "remote" as const,
      remote: { server_url: "https://tickets.example.test", remote_bunker_id: "engineering", state: "ready" },
      sharing: { state: "ready", role: "owner", is_owner: true, can_share: false, can_invite: true, can_manage_members: true, server: "https://tickets.example.test" },
    };
    const members = [{ id: "tim", display_name: "Tim Bunker", email: "tim@example.test", role: "owner", state: "active" }];
    const invitation = { id: "inv-1", role: "member", state: "pending", created_at: "", created_by: "Tim Bunker", expires_at: "2026-09-11T00:00:00+00:00" };
    transport.command.mockImplementation((action: string) => {
      if (action === "application.bunker.members") return Promise.resolve({ members, sharing: shared.sharing });
      if (action === "application.bunker.invitations") return Promise.resolve({ invitations: [] });
      if (action === "application.bunker.invitation.create") {
        return Promise.resolve({
          invitation: { ...invitation, link: "ticketbunker://join?server=https%3A%2F%2Ftickets.example.test&bunker=engineering&invite=one-use" },
          invitations: [invitation],
        });
      }
      throw new Error(`Unexpected command ${action}`);
    });

    render(
      <BunkerHub state={{ ...state, bunkers: [shared] }} selection={{ bunkerId: shared.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );

    expect(screen.queryByRole("button", { name: "Share Bunker" })).not.toBeInTheDocument();
    expect(screen.getByText(/Shared on tickets.example.test/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Bunker settings"));
    fireEvent.click(screen.getByRole("button", { name: "Members" }));
    expect(await screen.findByText("Tim Bunker")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Create invitation" }));
    expect(await screen.findByText(/ticketbunker:\/\/join\?/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy link" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument();
    expect(transport.command).toHaveBeenCalledWith("application.bunker.invitation.create", { bunker_id: shared.id, role: "member" });
  });

  it("pulls a shared Bunker on open and on Sync now", async () => {
    const replica = {
      ...bunker,
      mode: "remote" as const,
      remote: { server_url: "https://tickets.example.test", remote_bunker_id: "engineering", state: "ready", last_synced_at: "" },
      sharing: { state: "ready", role: "member", is_owner: false, can_share: false, can_invite: false, can_manage_members: false, server: "https://tickets.example.test" },
    };
    const onState = vi.fn();
    transport.command.mockImplementation((action: string) => {
      if (action === "application.bunker.sync") return Promise.resolve({ sync: { tickets: 3, reports: 1 }, bunkers: [replica] });
      throw new Error(`Unexpected command ${action}`);
    });

    render(
      <BunkerHub state={{ ...state, bunkers: [replica] }} selection={{ bunkerId: replica.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={onState} onSettings={vi.fn()} notify={vi.fn()} />,
    );

    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.sync", { bunker_id: replica.id }));
    expect(screen.getByText(/synced never/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(transport.command.mock.calls.filter(([action]) => action === "application.bunker.sync")).toHaveLength(2));
    expect(onState).toHaveBeenCalled();
  });

  it("shows members without invitation controls to a member", async () => {
    const replica = {
      ...bunker,
      mode: "remote" as const,
      remote: { server_url: "https://tickets.example.test", remote_bunker_id: "engineering", state: "ready" },
      sharing: { state: "ready", role: "member", is_owner: false, can_share: false, can_invite: false, can_manage_members: false, server: "https://tickets.example.test" },
    };
    transport.command.mockImplementation((action: string) => {
      if (action === "application.bunker.members") {
        return Promise.resolve({ members: [{ id: "tim", display_name: "Tim Bunker", email: "", role: "owner", state: "active" }], sharing: replica.sharing });
      }
      throw new Error(`Unexpected command ${action}`);
    });

    render(
      <BunkerHub state={{ ...state, bunkers: [replica] }} selection={{ bunkerId: replica.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );
    fireEvent.click(screen.getByLabelText("Bunker settings"));
    fireEvent.click(screen.getByRole("button", { name: "Members" }));
    expect(await screen.findByText("Tim Bunker")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create invitation" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add project" })).not.toBeInTheDocument();
    expect(transport.command).not.toHaveBeenCalledWith("application.bunker.invitations", expect.anything());
  });

  it("syncs a Project's tracker board from the Project page", async () => {
    const board = { id: "board-1", provider: "github", kind: "project", owner: "example", number: 8, repository: "", include_comments: true };
    const tracked = { ...project, tracker: board, trackers: [board] };
    const withTracker = { ...bunker, projects: [tracked] };
    const notify = vi.fn();
    const onState = vi.fn();
    transport.command.mockImplementation((action: string) => {
      if (action === "application.ticket.list") {
        return Promise.resolve({ tickets: [], total: 0, page: 1, page_size: 25, page_count: 0, facets: { statuses: [], assignees: [], reporters: [], labels: [] } });
      }
      if (action === "application.project.sync") {
        return Promise.resolve({ sync: { created: 2, updated: 0, unchanged: 1, source: "GitHub Project #8 · example" }, bunkers: [withTracker] });
      }
      throw new Error(`Unexpected command ${action}`);
    });

    render(
      <BunkerHub state={{ ...state, bunkers: [withTracker] }} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={onState} onSettings={vi.fn()} notify={notify} />,
    );

    expect(screen.getByText("GitHub Project #8 · example")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect a board" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sync tickets" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.project.sync", { bunker_id: bunker.id, project_id: project.id }));
    await waitFor(() => expect(notify).toHaveBeenCalledWith("Synced GitHub Project #8 · example: 2 new, 0 updated, 1 unchanged"));
    expect(onState).toHaveBeenCalled();
  });

  it("asks only for a token when no tracker is connected yet", async () => {
    render(
      <BunkerHub state={{ ...state, trackers: { github: false } }} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );

    expect(screen.getByText("No board linked")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sync tickets" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    expect(await screen.findByRole("heading", { name: "Boards" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Connect GitHub to see your boards" })).toBeInTheDocument();
    expect(screen.getByLabelText("GitHub token")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect GitHub" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Boards" })).not.toBeInTheDocument();
    expect(transport.command).not.toHaveBeenCalledWith("application.tracker.boards", expect.anything());
  });

  it("links a board picked from the list the token can see", async () => {
    const onState = vi.fn();
    transport.command.mockImplementation((action: string, payload?: Record<string, unknown>) => {
      if (action === "application.ticket.list") {
        return Promise.resolve({ tickets: [], total: 0, page: 1, page_size: 25, page_count: 0, facets: { statuses: [], assignees: [], reporters: [], labels: [] } });
      }
      if (action === "application.bunker.configuration.get") return Promise.resolve(configuration);
      if (action === "application.tracker.boards") {
        return Promise.resolve({
          provider: "github",
          account: { login: "example", name: "Example" },
          boards: [
            { kind: "project", owner: "example", number: 8, repository: "", title: "Voxel ECS", url: "https://github.com/users/example/projects/8" },
            { kind: "issues", owner: "example", number: 0, repository: "voxel", title: "example/voxel", url: "https://github.com/example/voxel", open_issues: 3 },
          ],
          notes: [],
        });
      }
      if (action === "application.project.link") {
        const board = payload?.board as Record<string, unknown>;
        return Promise.resolve({
          configuration: {
            ...configuration,
            documents: { ...configuration.documents, "tracker-sources": { kind: "tracker-sources", revision: "local:1", updated_at: "", content: { sources: [{ id: "board-1", provider: "github", ...board, include_comments: true }] } } },
          },
          bunker,
          bunkers: [bunker],
          link: { summary: ["Status: Todo, In Progress, Done", "12 items"], items: 12, fields_found: 1 },
        });
      }
      if (action === "application.project.unlink") {
        return Promise.resolve({ configuration, bunker, bunkers: [bunker] });
      }
      throw new Error(`Unexpected command ${action}`);
    });

    render(
      <BunkerHub state={{ ...state, trackers: { github: true } }} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={onState} onSettings={vi.fn()} notify={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    const list = await screen.findByRole("list", { name: "Boards" });
    expect(within(list).getByText("Voxel ECS")).toBeInTheDocument();
    expect(within(list).getByText("example/voxel")).toBeInTheDocument();
    const link = screen.getByRole("button", { name: "Link board" });
    expect(link).toBeDisabled();
    fireEvent.click(within(list).getByRole("button", { name: /Voxel ECS/ }));
    expect(link).toBeEnabled();
    fireEvent.click(link);
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith(
      "application.project.link",
      expect.objectContaining({ bunker_id: bunker.id, project_id: project.id, include_comments: true, board: expect.objectContaining({ kind: "project", owner: "example", number: 8, title: "Voxel ECS" }) }),
    ));
    expect(await screen.findByText(/One board linked/)).toBeInTheDocument();
    expect(screen.getByText(/Built from the board: Status: Todo, In Progress, Done/)).toBeInTheDocument();
    expect(within(screen.getByRole("complementary", { name: "Configuration" })).queryByRole("button", { name: "Sync tickets" })).not.toBeInTheDocument();
    const linkedList = screen.getByRole("list", { name: "Linked boards" });
    expect(within(linkedList).getByText("GitHub · Voxel ECS")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Link board" })).toBeDisabled();
    fireEvent.click(within(linkedList).getByRole("button", { name: "Unlink" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.project.unlink", { bunker_id: bunker.id, project_id: project.id, source_id: "board-1" }));
    await waitFor(() => expect(screen.queryByRole("list", { name: "Linked boards" })).not.toBeInTheDocument());
    expect(screen.getByText("Adjust field mapping")).toBeInTheDocument();
  });

  it("connects trackers at Bunker level", async () => {
    render(
      <BunkerHub state={{ ...state, trackers: { github: false } }} selection={{ bunkerId: bunker.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );
    fireEvent.click(screen.getByLabelText("Bunker settings"));
    fireEvent.click(screen.getByRole("button", { name: "Trackers" }));
    expect(await screen.findByRole("heading", { name: "Trackers" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect GitHub" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect Jira" })).toBeInTheDocument();
  });

  it("joins from one invitation link without raw credentials", () => {
    render(
      <BunkerHub state={state} selection={null} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Join" }));
    expect(screen.getByLabelText("Invitation link")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Authorization/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Credential reference/)).not.toBeInTheDocument();
  });

  it("lists Projects at Bunker scope and tickets only inside a Project", async () => {
    const onSelection = vi.fn();
    const { rerender } = render(
      <BunkerHub state={state} selection={{ bunkerId: bunker.id }} onSelection={onSelection} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );

    expect(screen.getByRole("heading", { name: "Engineering" })).toBeInTheDocument();
    const catalog = screen.getByRole("region", { name: "Projects" });
    expect(within(catalog).getByRole("heading", { name: "Projects" })).toBeInTheDocument();
    const row = within(catalog).getByRole("button", { name: /Entity Component System/ });
    expect(row).toHaveTextContent("Coal");
    expect(row).toHaveTextContent("3 tickets");
    expect(screen.queryByRole("searchbox", { name: "Search tickets" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Tickets" })).not.toBeInTheDocument();
    expect(transport.command).not.toHaveBeenCalledWith("application.ticket.list", expect.anything());

    fireEvent.click(row);
    expect(onSelection).toHaveBeenCalledWith({ bunkerId: bunker.id, projectId: project.id });

    rerender(
      <BunkerHub state={state} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={onSelection} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );
    expect(screen.getByRole("heading", { name: /Entity Component System/ })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Projects" })).not.toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search tickets" })).toBeInTheDocument();
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith(
      "application.ticket.list",
      expect.objectContaining({ bunker_id: bunker.id, project_id: project.id }),
    ));
  });

  it("shows the indexed repository allowlist in Project configuration", async () => {
    render(
      <BunkerHub state={state} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Configure" }));
    fireEvent.click(await screen.findByRole("button", { name: "Repositories" }));
    expect(await screen.findByRole("table", { name: "Local repositories" })).toBeInTheDocument();
    expect(screen.getByText("github.com/Example/coal")).toBeInTheDocument();
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add local repository" })).toBeInTheDocument();
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith(
      "application.bunker.configuration.get",
      { bunker_id: bunker.id, project_id: project.id },
    ));
  });
});
