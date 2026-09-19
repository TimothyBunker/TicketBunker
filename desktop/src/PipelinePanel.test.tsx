import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PipelinePanel, { PipelineIndicator, type PipelineSnapshot } from "./PipelinePanel";
import type { Bunker } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
const bunker: Bunker = { id: "lab", name: "Lab", mode: "remote", database: "private.sqlite",
  remote: { server_url: "https://service.example", remote_bunker_id: "team", state: "ready" },
  projects: [], repositories: [], project_count: 0, repository_count: 0, ticket_count: 0 };
const snapshot: PipelineSnapshot = { bunker_id: "lab", authority: "service", pending_count: 1, operations_truncated: false,
  rows: [
    { id: "shared-refresh", name: "Shared refresh", source: "Shared Bunker", target: "This device", trigger: "Open Bunker or Sync", state: "recorded", result: "Snapshot downloaded", action: "sync" },
    { id: "shared-saves", name: "Shared saves", source: "This device", target: "Shared Bunker", trigger: "Save", state: "attention", result: "1 unfinished save", action: "sync" },
    { id: "tracker:project", name: "Tracker import · LAB", source: "GitHub", target: "Shared Bunker", trigger: "Link board or Sync tickets", state: "unknown", result: "Not imported on this device", action: "tracker", project_id: "project" },
  ], operations: [{ id: "lost", state: "unknown", created_at: "2026-09-19T12:00:00Z", accepted: 1, total: 2,
    error: "Receipt missing", same_destination: true, targets_truncated: false,
    targets: [{ kind: "report", record_id: "LAB-1|branch:work", ticket_id: "LAB-1", project_id: "project", scope: "branch:work", repository_id: "repo" }] }] };
const props = () => ({ bunker, onClose: vi.fn(), onChanged: vi.fn(), notify: vi.fn(), onReview: vi.fn(), onResult: vi.fn() });

describe("Pipeline visibility", () => {
  afterEach(cleanup);
  beforeEach(() => { transport.command.mockReset(); transport.command.mockResolvedValue(snapshot); });

  it("only reads receipts on open and expands the requested pipeline", async () => {
    render(<PipelinePanel {...props()} initialRow="shared-saves" />);
    expect(await screen.findByText("Outcome unknown")).toBeVisible();
    expect(screen.getByText(/1\/2 changes confirmed/)).toBeVisible();
    expect(transport.command.mock.calls).toEqual([["application.bunker.pipelines", { bunker_id: "lab" }]]);
    expect(screen.getByText("Snapshot downloaded").closest("details")).not.toHaveAttribute("open");
  });

  it("separates local receipt refresh from an explicit Bunker sync", async () => {
    const callbacks = props();
    transport.command.mockImplementation((name: string) => Promise.resolve(name === "application.bunker.sync" ? { bunkers: [bunker] } : snapshot));
    render(<PipelinePanel {...callbacks} />);
    await screen.findByText("Snapshot downloaded");
    fireEvent.click(screen.getByRole("button", { name: "Refresh pipeline status" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledTimes(2));
    expect(transport.command.mock.calls.every(([name]) => name === "application.bunker.pipelines")).toBe(true);
    await screen.findByText("Snapshot downloaded");
    fireEvent.click(screen.getByRole("button", { name: "Sync Bunker" }));
    await waitFor(() => expect(callbacks.onChanged).toHaveBeenCalledWith([bunker]));
    expect(transport.command).toHaveBeenCalledWith("application.bunker.sync", { bunker_id: "lab" });
  });

  it("routes tracker retry to its Project, not the shared refresh", async () => {
    const callbacks = props();
    transport.command.mockImplementation((name: string) => Promise.resolve(name === "application.project.sync" ? { bunkers: [bunker] } : snapshot));
    render(<PipelinePanel {...callbacks} initialRow="tracker:project" />);
    fireEvent.click(await screen.findByRole("button", { name: "Sync tickets" }));
    await waitFor(() => expect(callbacks.onChanged).toHaveBeenCalled());
    expect(transport.command).toHaveBeenCalledWith("application.project.sync", { bunker_id: "lab", project_id: "project" });
  });

  it("retains conflicts after a successful download and offers review", async () => {
    const callbacks = props(); const warning = "A previous save conflicts; review it.";
    const conflicted = { ...snapshot, operations: [{ ...snapshot.operations[0], state: "conflict" }] };
    transport.command.mockImplementation((name: string) => Promise.resolve(name === "application.bunker.sync" ? { bunkers: [bunker], warning } : conflicted));
    render(<PipelinePanel {...callbacks} initialRow="shared-saves" />);
    fireEvent.click(await screen.findByRole("button", { name: "Sync Bunker" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(warning);
    expect(callbacks.notify).toHaveBeenCalledWith(warning, true);
    expect(callbacks.onResult).toHaveBeenCalledWith("sync", undefined, warning);
    fireEvent.click(await screen.findByRole("button", { name: "Review" }));
    expect(callbacks.onReview).toHaveBeenCalledWith({ ticketId: "LAB-1", projectId: "project" });
  });

  it("does not replay a failed write automatically", async () => {
    transport.command.mockImplementation((name: string) => name === "application.bunker.sync" ? Promise.reject(new Error("Disconnected; outcome unknown")) : Promise.resolve(snapshot));
    render(<PipelinePanel {...props()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Sync Bunker" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("outcome unknown");
    await waitFor(() => expect(transport.command.mock.calls.filter(([name]) => name === "application.bunker.pipelines")).toHaveLength(2));
    expect(transport.command.mock.calls.filter(([name]) => name === "application.bunker.sync")).toHaveLength(1);
  });

  it("filters displayed receipts to the selected ticket", async () => {
    transport.command.mockResolvedValue({ ...snapshot, operations: [...snapshot.operations, { ...snapshot.operations[0], id: "other", error: "Other ticket error", targets: [{ ...snapshot.operations[0].targets[0], ticket_id: "LAB-2" }] }] });
    render(<PipelinePanel {...props()} initialRow="shared-saves" ticketId="LAB-1" />);
    expect(await screen.findByText("LAB-1 saves")).toBeVisible();
    expect(screen.queryByText("Other ticket error")).not.toBeInTheDocument();
    expect(screen.getByText(/Bunker sync still reconciles/)).toBeVisible();
  });

  it("has a named local indicator without a phantom shared success", () => {
    render(<PipelineIndicator bunker={{ ...bunker, remote: null, mode: "local" }} onClick={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Bunker pipelines" })).toHaveAttribute("title", expect.stringContaining("Stored on this device"));
  });

  it("returns keyboard focus to its opener", async () => {
    function Host() { return <button>Opener</button>; }
    const opener = render(<Host />); screen.getByRole("button", { name: "Opener" }).focus();
    const view = render(<PipelinePanel {...props()} />);
    await screen.findByText("Snapshot downloaded");
    expect(screen.getByRole("complementary", { name: "Bunker pipelines" })).toHaveFocus();
    view.unmount();
    expect(within(opener.container).getByRole("button")).toHaveFocus();
  });
});
