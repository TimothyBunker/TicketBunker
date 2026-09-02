import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BunkerHub from "./BunkerHub";
import type { AppState, ConfigurationState } from "./types";

const transport = vi.hoisted(() => ({
  command: vi.fn(),
  chooseGitDirectory: vi.fn(),
}));

vi.mock("./transport", () => transport);

const project = { id: "ecs", key: "ECS", name: "Entity Component System", children: [] };
const bunker = {
  id: "engineering",
  name: "Engineering",
  mode: "local" as const,
  database: "/data/engineering.sqlite3",
  remote: null,
  projects: [project],
  repositories: [],
  project_count: 1,
  repository_count: 0,
  ticket_count: 0,
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
    items: [{
      id: "coal",
      project_id: "ecs",
      name: "Coal",
      canonical_remote: "github.com/Example/coal",
      default_branch: "main",
      local_path: null,
    }],
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

  it("keeps remote database controls at Bunker level", () => {
    const { rerender } = render(
      <BunkerHub state={state} selection={{ bunkerId: bunker.id, projectId: project.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );

    expect(screen.queryByRole("button", { name: "Connect remote" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Attach repository" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add subproject" })).toBeInTheDocument();

    rerender(
      <BunkerHub state={state} selection={{ bunkerId: bunker.id }} onSelection={vi.fn()} onTicket={vi.fn()} onState={vi.fn()} onSettings={vi.fn()} notify={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Connect remote" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Attach repository" })).not.toBeInTheDocument();
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
