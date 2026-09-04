import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BunkerHub from "./BunkerHub";
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
    expect(screen.getByRole("button", { name: "Share Bunker" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Members" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Attach repository" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Share Bunker" }));
    expect(screen.getByLabelText("Server address")).toBeInTheDocument();
    expect(screen.getByLabelText("Setup token")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish and share" })).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "Members" }));
    expect(await screen.findByText("Tim Bunker")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Create invitation" }));
    expect(await screen.findByText(/ticketbunker:\/\/join\?/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy link" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument();
    expect(transport.command).toHaveBeenCalledWith("application.bunker.invitation.create", { bunker_id: shared.id, role: "member" });
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
    fireEvent.click(screen.getByRole("button", { name: "Members" }));
    expect(await screen.findByText("Tim Bunker")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create invitation" })).not.toBeInTheDocument();
    expect(transport.command).not.toHaveBeenCalledWith("application.bunker.invitations", expect.anything());
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
    expect(await screen.findByRole("table", { name: "Project repositories" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("github.com/Example/coal")).toBeInTheDocument();
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith(
      "application.bunker.configuration.get",
      { bunker_id: bunker.id, project_id: project.id },
    ));
  });
});
