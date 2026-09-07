import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ConfigurationPanel from "./ConfigurationPanel";
import { UnsavedChangesBoundary } from "./UnsavedChanges";
import type { Bunker, ConfigurationState } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn(), chooseGitDirectory: vi.fn() }));
vi.mock("./transport", () => transport);
const project = { id: "nav", key: "NAV", name: "Navigation", children: [], ticket_count: 0 };
const bunker: Bunker = { id: "lab", name: "Lab", mode: "local", database: "/data/lab.db", remote: null,
  projects: [project], repositories: [], project_count: 1, repository_count: 0, ticket_count: 0 };
const initial: ConfigurationState = { scope_type: "project", scope_id: "nav", composition: "pinned",
  permission: { can_write: true, reason: "Local", capabilities: ["config:write"] },
  repositories: { revision: "repos:1", items: [] },
  repository_registry: { revision: "registry:1", items: [{ id: "github.com/example/navigation", project_id: "", name: "Navigation", canonical_remote: "github.com/example/navigation", default_branch: "main", local_path: null }] },
  documents: { checks: { kind: "checks", revision: "checks:1", updated_at: "", content: { build: "make", test: "", timeout_seconds: 600, discover: false } } },
};
let snapshot: ConfigurationState;
function setup() {
  const close = vi.fn();
  render(<UnsavedChangesBoundary><ConfigurationPanel bunker={bunker} project={project} initialTab="checks" onClose={close} onBunker={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
  return close;
}
async function editBuild(value = "make navigation") {
  const input = await screen.findByLabelText("Build command");
  fireEvent.change(input, { target: { value } });
  return input;
}
beforeEach(() => {
  snapshot = structuredClone(initial);
  transport.command.mockReset();
  transport.command.mockImplementation(async (name, payload) => {
    if (name === "application.bunker.configuration.get") return structuredClone(snapshot);
    if (name === "application.bunker.configuration.replace") {
      snapshot.documents.checks = { ...snapshot.documents.checks!, content: payload.content, revision: "checks:2" };
      return structuredClone(snapshot);
    }
    if (name === "application.project.repositories.select") {
      snapshot.repositories = { revision: "repos:2", items: payload.repositories };
      return { configuration: structuredClone(snapshot), bunker };
    }
    throw new Error(`Unexpected command ${name}`);
  });
});
afterEach(cleanup);
describe("Repository setup scopes", () => {
  it("edits allowed remotes at Bunker scope without local folder controls", async () => {
    snapshot.scope_type = "bunker";
    snapshot.scope_id = bunker.id;
    snapshot.repositories = structuredClone(snapshot.repository_registry!);
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((name, payload) => name === "application.bunker.repository.replace"
      ? Promise.resolve({ configuration: snapshot, bunker }) : original(name, payload));
    render(<UnsavedChangesBoundary><ConfigurationPanel bunker={bunker} initialTab="repositories" onClose={vi.fn()} onBunker={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
    expect(await screen.findByRole("table", { name: "Allowed repositories" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Repository 1 name"), { target: { value: "Navigator" } });
    expect(screen.queryByRole("button", { name: "Connect folder" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save repositories" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.repository.replace",
      expect.objectContaining({ project_id: "", expected_revision: "registry:1", repositories: [expect.objectContaining({ name: "Navigator" })] })));
  });

  it("selects a Project repository from the Bunker instead of entering another remote", async () => {
    setup();
    await screen.findByLabelText("Build command");
    fireEvent.click(screen.getByRole("button", { name: "Repositories" }));
    fireEvent.click(screen.getByText("+ Add repository"));
    const select = screen.getByRole("combobox", { name: "Repository 1 allowed remote" });
    fireEvent.change(select, { target: { value: "github.com/example/navigation" } });
    expect(screen.getByLabelText("Repository 1 name")).toHaveValue("Navigation");
    expect(screen.getByLabelText("Repository 1 name")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Connect folder" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Save repositories" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.project.repositories.select",
      expect.objectContaining({ project_id: "nav", repositories: [expect.objectContaining({ canonical_remote: "github.com/example/navigation", id: "" })] })));
  });

  it("lets members connect local folders but not change the shared selection", async () => {
    snapshot.permission = { can_write: false, reason: "Ask an owner", capabilities: [] };
    snapshot.repositories.items = [{ ...snapshot.repository_registry!.items[0], id: "binding", project_id: project.id }];
    render(<UnsavedChangesBoundary><ConfigurationPanel bunker={bunker} project={project} initialTab="repositories" onClose={vi.fn()} onBunker={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
    expect(await screen.findByRole("button", { name: "Connect folder" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save repositories" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "+ Add repository" })).toBeDisabled();
    expect(screen.getByLabelText("Repository 1 allowed remote")).toBeDisabled();
  });
});
describe("Configuration draft safety", () => {
  it("cancels without discarding and explicitly discards without publishing", async () => {
    const close = setup();
    const input = await editBuild();
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Save applies these settings");
    fireEvent.click(screen.getByText("Cancel"));
    expect(close).not.toHaveBeenCalled(); expect(input).toHaveValue("make navigation");
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    fireEvent.click(screen.getByText("Do not save"));
    expect(close).toHaveBeenCalledOnce();
    expect(transport.command.mock.calls.every(([name]) => name.endsWith(".get"))).toBe(true);
  });
  it("publishes with the loaded revision before leaving", async () => {
    const close = setup(); await editBuild();
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByText("Save"));
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(transport.command).toHaveBeenCalledWith("application.bunker.configuration.replace", expect.objectContaining({ expected_revision: "checks:1", content: expect.objectContaining({ build: "make navigation" }) }));
  });
  it("retains the draft when publishing fails", async () => {
    const close = setup(); const input = await editBuild();
    transport.command.mockRejectedValue(new Error("Another member changed Checks. Reload before retrying."));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByText("Save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Another member changed Checks");
    expect(close).not.toHaveBeenCalled(); expect(input).toHaveValue("make navigation");
  });
  it("preserves a checks draft and its revision when another section publishes", async () => {
    setup(); await editBuild();
    snapshot.documents.checks = { ...snapshot.documents.checks!, revision: "checks:remote", content: { ...snapshot.documents.checks!.content, build: "someone else's command" } };
    fireEvent.click(screen.getByRole("button", { name: "Repositories" }));
    fireEvent.click(screen.getByRole("button", { name: "Save repositories" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.project.repositories.select", expect.anything()));
    await waitFor(() => expect(screen.getByRole("button", { name: "Checks" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Checks" }));
    expect(screen.getByLabelText("Build command")).toHaveValue("make navigation");
    fireEvent.click(screen.getByRole("button", { name: "Save checks" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.configuration.replace", expect.objectContaining({ expected_revision: "checks:1" })));
  });

  it("does not republish successful sections when Save retries a partial failure", async () => {
    const close = setup(); await editBuild();
    fireEvent.click(screen.getByRole("button", { name: "Repositories" }));
    fireEvent.click(screen.getByText("+ Add repository"));
    fireEvent.change(screen.getByLabelText("Repository 1 allowed remote"), { target: { value: "github.com/example/navigation" } });
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((name, payload) => name === "application.bunker.configuration.replace"
      ? Promise.reject(new Error("Checks publication is offline")) : original(name, payload));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByText("Save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Checks publication is offline");
    expect(close).not.toHaveBeenCalled();
    transport.command.mockImplementation(original);
    fireEvent.click(within(screen.getByRole("dialog")).getByText("Save"));
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(transport.command.mock.calls.filter(([name]) => name === "application.project.repositories.select")).toHaveLength(1);
  });

  it("retains an absent base revision if someone creates Checks during editing", async () => {
    delete snapshot.documents.checks;
    setup(); await editBuild();
    snapshot.documents.checks = { ...initial.documents.checks!, revision: "checks:remote" };
    fireEvent.click(screen.getByRole("button", { name: "Repositories" }));
    fireEvent.click(screen.getByRole("button", { name: "Save repositories" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.project.repositories.select", expect.anything()));
    await waitFor(() => expect(screen.getByRole("button", { name: "Checks" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Checks" }));
    fireEvent.click(screen.getByRole("button", { name: "Save checks" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.configuration.replace", expect.objectContaining({ expected_revision: "" })));
  });
});
