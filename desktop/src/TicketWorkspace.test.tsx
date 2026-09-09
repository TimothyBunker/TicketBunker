import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
      if (action === "application.ticket.get") return Promise.resolve({ ...detail, check_results: {}, ticket: { ...ticket, description: "See `README.md:1`." }, selected_repository_id: payload.repository_id || "coal" });
      if (action === "editor.read") return Promise.resolve({ path: "README.md", content: "original\n", digest: "old", branch: "ada/ECS-4-work", line: 1, settings: { tab_size: 4, line_wrap: false } });
      if (action === "editor.save") return Promise.resolve({ content: payload.content, digest: "new", saved: true });
      if (action === "workflow.attach") return Promise.resolve({ branch: payload.branch });
      if (action === "workflow.state") return Promise.resolve({ base_branches: ["main", "release"], related_branches: ["ada/ECS-4-work"], current_branch: "ada/ECS-4-work" });
      if (action === "workflow.start") return Promise.resolve({ branch: "ada/ECS-4-new-work" });
      if (action === "truth.synthesize") return Promise.resolve({ queued: true });
      if (action === "truth.queue") return Promise.resolve({ queued: true });
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
      { bunker_id: "engineering", project: "ECS", ticket: "ECS-4", branch: "ada/ECS-4-work" }, "/work/renderer"));
  });

  it("shows the checked-out ticket branch and preselects it for manual linking", async () => {
    render(<TicketWorkspace bunker={bunker} reference={ticket} onBack={vi.fn()} notify={vi.fn()} />);
    const current = await screen.findByLabelText("Current ticket branch");
    expect(current).toHaveTextContent("ada/ECS-4-work");
    expect(current).toHaveTextContent("Matched to ECS-4");
    expect(within(current).getByRole("button", { name: "Synthesize" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Create branch" }));
    await waitFor(() => expect(screen.getByLabelText("Existing branch")).toHaveValue("ada/ECS-4-work"));
  });

  it("hides the current-branch action when no ticket branch is checked out and rereads on focus", async () => {
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((action, ...args) => action === "application.ticket.get" ? Promise.resolve({ ...detail,
      branches: detail.branches.map((branch) => ({ ...branch, current: false })),
    }) : original(action, ...args));
    render(<TicketWorkspace bunker={bunker} reference={ticket} onBack={vi.fn()} notify={vi.fn()} />);
    await screen.findByLabelText("Working repository");
    expect(screen.queryByLabelText("Current ticket branch")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Synthesize" })).not.toBeInTheDocument();
    const reads = transport.command.mock.calls.filter(([action]) => action === "application.ticket.get").length;
    fireEvent.focus(window);
    await waitFor(() => expect(transport.command.mock.calls.filter(([action]) => action === "application.ticket.get").length).toBeGreaterThan(reads));
    expect(transport.command.mock.calls.some(([action]) => action === "truth.synthesize")).toBe(false);
  });

  it("groups readable details and keeps optional empty fields in More details", async () => {
    const { container } = render(<TicketWorkspace bunker={bunker} reference={ticket} onBack={vi.fn()} notify={vi.fn()} />);
    await screen.findByRole("heading", { name: "Details" });
    const facts = container.querySelector(".facts-grid")!;
    expect(facts.querySelectorAll(".facts-column")).toHaveLength(2);
    expect(within(facts as HTMLElement).getByText("Unassigned")).toBeVisible();
    expect(within(facts as HTMLElement).getByText("Type")).toBeVisible();
    expect(within(facts as HTMLElement).queryByText("Priority")).not.toBeInTheDocument();
    expect(within(facts as HTMLElement).queryByText("Updated")).not.toBeInTheDocument();
    expect(within(facts as HTMLElement).queryByText("Labels")).not.toBeInTheDocument();
    const more = container.querySelector("details.more-details")!;
    expect(more).not.toHaveAttribute("open");
    expect(within(more as HTMLElement).getByText("Priority")).toBeInTheDocument();
    expect(within(more as HTMLElement).getByText("Boards")).toBeInTheDocument();
  });

  it("preserves technical metadata including false and zero without repeating header fields", async () => {
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((action, ...args) => action === "application.ticket.get" ? Promise.resolve({ ...detail,
      ticket: { ...ticket, reporter: "Ada", priority: "high", labels: ["ecs"], locked: false, estimate: 0, custom_empty: "", issue_node_id: "fixture-node" },
    }) : original(action, ...args));
    const { container } = render(<TicketWorkspace bunker={bunker} reference={ticket} onBack={vi.fn()} notify={vi.fn()} />);
    await screen.findByRole("heading", { name: "Details" });
    const columns = container.querySelectorAll(".facts-column");
    expect(columns[0]).toHaveTextContent("ReporterAda");
    expect(columns[1]).toHaveTextContent("PriorityHigh");
    const more = container.querySelector("details.more-details")!;
    expect(more).toHaveTextContent("LockedNo");
    expect(more).toHaveTextContent("Estimate0");
    expect(more).toHaveTextContent("Custom empty—");
    expect(more).toHaveTextContent("Issue node idfixture-node");
    expect(container.querySelectorAll(".label-chip")).toHaveLength(1);
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

  it("protects an actual ticket editor draft when returning to the ticket", async () => {
    render(<TicketWorkspace bunker={bunker} reference={ticket} onBack={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "README.md line 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Open in editor" }));
    const input = await screen.findByLabelText("Edit README.md");
    fireEvent.change(input, { target: { value: "edited\n" } });
    const back = screen.getByRole("button", { name: "Back to ticket" });
    expect(back.textContent).toBe("←");
    expect(back).toHaveAttribute("title", "Back to ticket");
    const chapters = screen.getByRole("navigation", { name: "Ticket sections" });
    fireEvent.click(within(chapters).getByRole("button", { name: "Description" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(input).toHaveValue("edited\n");
    fireEvent.click(screen.getByRole("button", { name: "Back to ticket" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("editor.save", { path: "README.md", content: "edited\n", expected_digest: "old" }, "/work/coal"));
    await waitFor(() => expect(screen.queryByLabelText("Edit README.md")).not.toBeInTheDocument());
  });

  it("reloads after a failed creation and offers an existing branch attachment", async () => {
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((action, ...args) => action === "workflow.start"
      ? Promise.reject(new Error("Branch exists locally. Sync and attach it.")) : original(action, ...args));
    render(<TicketWorkspace bunker={bunker} reference={ticket} onBack={vi.fn()} notify={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Create branch" }));
    await screen.findByRole("option", { name: "release" });
    fireEvent.change(screen.getByLabelText("Short description"), { target: { value: "work" } });
    fireEvent.click(screen.getByText("Review branch"));
    fireEvent.click(screen.getAllByRole("button", { name: "Create branch" }).at(-1)!);
    await screen.findByRole("alert");
    expect(transport.command.mock.calls.filter(([name]) => name === "application.ticket.get").length).toBeGreaterThan(1);
    fireEvent.click(screen.getByRole("button", { name: "Create branch" }));
    fireEvent.change(await screen.findByLabelText("Existing branch"), { target: { value: "ada/ECS-4-work" } });
    fireEvent.click(screen.getByText("Attach branch"));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("workflow.attach", { bunker_id: bunker.id, ticket: ticket.id, branch: "ada/ECS-4-work" }, "/work/coal"));
  });

  it("fetches refs on open and supports an explicit retry without creating work", async () => {
    render(<TicketWorkspace bunker={bunker} reference={ticket} onBack={vi.fn()} notify={vi.fn()} />);
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("truth.queue", { bunker_id: bunker.id, project: "ECS", ticket: ticket.id }, "/work/coal"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh branches" }));
    expect(screen.queryByRole("button", { name: "Fetch branches" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh branches" }).textContent).toBe("");
    const chapters = screen.getByRole("navigation", { name: "Ticket sections" });
    expect(within(chapters).getAllByRole("button").map((button) => button.textContent)).toEqual(["Details", "Description", "Agent work"]);
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("truth.queue", { bunker_id: bunker.id, project: "ECS", ticket: ticket.id, force: true }, "/work/coal"));
    expect(transport.command.mock.calls.some(([name]) => ["truth.synthesize", "workflow.start"].includes(name))).toBe(false);
  });

  it("shows deterministic failures without an agent report", async () => {
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((action, ...args) => action === "application.ticket.get" ? Promise.resolve({ ...detail,
      checks: { source: "project", build: "", test: "check", timeout_seconds: 60 },
      check_results: { branch: "ada/ECS-4-work", commit: "abcdef12", checked_at: "", stale: true,
        results: [{ kind: "test", command: "check", passed: false, exit: 1, seconds: 0.1, log_text: "Assertion failed" }] },
    }) : original(action, ...args));
    render(<TicketWorkspace bunker={bunker} reference={ticket} onBack={vi.fn()} notify={vi.fn()} />);
    expect(await screen.findByText("Test: Failed")).toBeInTheDocument();
    expect(screen.getByText("Last checks · Out of date")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Test: Failed"));
    expect(screen.getByText("Assertion failed")).toBeVisible();
  });

  it.each(["cached", "failed", "refreshing", "current"])("shows Git state %s without conflating cached data with failure", async (status) => {
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((action, ...args) => action === "application.ticket.get" ? Promise.resolve({ ...detail,
      truth: { git: { status, detail: "Fixture Git state" } },
    }) : original(action, ...args));
    render(<TicketWorkspace bunker={bunker} reference={ticket} onBack={vi.fn()} notify={vi.fn()} />);
    const refresh = await screen.findByRole("button", { name: "Refresh branches" });
    expect(Boolean(refresh.querySelector(".action-issue"))).toBe(status === "failed");
    expect(refresh.classList.contains("is-loading")).toBe(status === "refreshing");
    if (status === "cached") {
      expect(screen.getByText(/Git freshness unknown/)).not.toHaveClass("inline-error");
    }
  });
});
