import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TicketWorkspace from "./TicketWorkspace";
import type { Bunker, TicketDetail, TicketReference } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);

const project = { id: "ecs", key: "ECS", name: "ECS", children: [], ticket_count: 1 };
const repositories = ["Coal", "Renderer"].map((name) => ({ id: name.toLowerCase(), project_id: "ecs", name,
  canonical_remote: `github.com/example/${name.toLowerCase()}`, default_branch: "main", local_path: `/work/${name.toLowerCase()}` }));
const bunker: Bunker = { id: "engineering", name: "Engineering", mode: "local", database: "/data/bunker.sqlite3",
  remote: null, projects: [project], repositories, project_count: 1, repository_count: 2, ticket_count: 1 };
const ticket: TicketReference = { id: "ECS-4", title: "Position system", status: "Todo", type: "task", priority: "",
  assignee: "", reporter: "", labels: [], updated: "", project, source_kind: "github", source_reference: "" };
const detail: TicketDetail = { ticket, project, repositories, reports: [], comments: [], activity: [],
  branches: repositories.map((repo) => ({ name: "ada/ECS-4-work", current: true, local: true, remote: false,
    remotes: [], upstream: "", commit: "", last_commit_at: "", last_author: "", ahead: 0, behind: 0,
    base: "main", repository_id: repo.id, repository_name: repo.name })) };

describe("Bunker-native ticket work", () => {
  afterEach(cleanup);
  beforeEach(() => {
    transport.command.mockReset();
    transport.command.mockImplementation((action: string, payload: Record<string, string>) => {
      if (action === "application.ticket.get") return Promise.resolve({ ...detail, selected_repository_id: payload.repository_id || "coal" });
      if (action === "workflow.state") return Promise.resolve({ base_branches: ["main", "release"], related_branches: ["ada/ECS-4-work"] });
      if (action === "workflow.start") return Promise.resolve({ branch: "ada/ECS-4-new-work" });
      if (action === "truth.synthesize") return Promise.resolve({ queued: true });
      throw new Error(`Unexpected action ${action}`);
    });
  });

  it("keeps identical branch names scoped to the selected repository", async () => {
    const { container } = render(<TicketWorkspace bunker={bunker} reference={ticket} onBack={vi.fn()} notify={vi.fn()} />);
    const selector = await screen.findByLabelText("Working repository");
    expect(container.querySelectorAll(".branch-item")).toHaveLength(1);
    fireEvent.change(selector, { target: { value: "renderer" } });
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.ticket.get",
      { bunker_id: "engineering", ticket_id: "ECS-4", repository_id: "renderer" }));
    fireEvent.click(screen.getByRole("button", { name: "Synthesize" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("truth.synthesize",
      { bunker_id: "engineering", project: "ECS", ticket: "ECS-4" }, "/work/renderer"));
  });

  it("reads actual base branches and confirms before creating through the Bunker action", async () => {
    render(<TicketWorkspace bunker={bunker} reference={ticket} onBack={vi.fn()} notify={vi.fn()} />);
    await screen.findByLabelText("Working repository");
    fireEvent.click(screen.getByRole("button", { name: "Create branch" }));
    await screen.findByRole("option", { name: "release" });
    fireEvent.change(screen.getByLabelText("Short description"), { target: { value: "new-work" } });
    fireEvent.change(screen.getByLabelText("Branch from"), { target: { value: "release" } });
    fireEvent.click(screen.getByRole("button", { name: "Review branch" }));
    expect(transport.command.mock.calls.some(([action]) => action === "workflow.start")).toBe(false);
    const buttons = screen.getAllByRole("button", { name: "Create branch" });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("workflow.start",
      { bunker_id: "engineering", ticket: "ECS-4", name: "new-work", base: "release" }, "/work/coal"));
  });
});
