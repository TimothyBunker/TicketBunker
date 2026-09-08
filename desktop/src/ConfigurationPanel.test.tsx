import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ConfigurationPanel from "./ConfigurationPanel";
import { clearBoardCache } from "./boardCache";
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
  clearBoardCache();
  snapshot = structuredClone(initial);
  transport.chooseGitDirectory.mockReset();
  transport.chooseGitDirectory.mockResolvedValue("/work/navigation");
  transport.command.mockReset();
  transport.command.mockImplementation(async (name, payload) => {
    if (name === "application.bunker.configuration.get") return structuredClone(snapshot);
    if (name === "application.bunker.configuration.replace") {
      snapshot.documents[payload.kind as "checks"] = { kind: payload.kind, updated_at: "", content: payload.content, revision: `${payload.kind}:2` };
      return structuredClone(snapshot);
    }
    if (name === "application.project.repositories.select") {
      snapshot.repositories = { revision: "repos:2", items: payload.repositories };
      return { configuration: structuredClone(snapshot), bunker };
    }
    if (name === "application.bunker.repository.attach") return { bunker };
    if (name === "application.bunker.repository.replace") {
      snapshot.repositories = { revision: "registry:2", items: payload.repositories };
      return { configuration: structuredClone(snapshot), bunker };
    }
    throw new Error(`Unexpected command ${name}`);
  });
});
afterEach(cleanup);
describe("Repository setup scopes", () => {
  it("reuses Boards across tabs and reloads them only on explicit refresh", async () => {
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((name, payload) => name === "application.tracker.boards" ? Promise.resolve({ boards: [], notes: [] }) : original(name, payload));
    render(<UnsavedChangesBoundary><ConfigurationPanel bunker={bunker} project={project} trackers={{ github: true }} onClose={vi.fn()} onBunker={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
    await screen.findByRole("list", { name: "Boards" });
    fireEvent.click(screen.getByRole("button", { name: "Repositories" }));
    fireEvent.click(screen.getByRole("button", { name: "Boards" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Refresh boards" })).toBeEnabled());
    expect(transport.command.mock.calls.filter(([name]) => name === "application.tracker.boards")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Refresh boards" }));
    await waitFor(() => expect(transport.command.mock.calls.filter(([name]) => name === "application.tracker.boards")).toHaveLength(2));
  });

  it("shows full detected build commands for review without enabling automatic execution", async () => {
    const original = transport.command.getMockImplementation()!;
    const build = "cmake -S . -B .tb-check-build && cmake --build .tb-check-build";
    transport.command.mockImplementation((name, payload) => name === "application.project.checks.detect" ? Promise.resolve({ repositories: ["Navigation"], suggestions: [{ tool: "CMake", repository: "Navigation", reason: "CMakeLists.txt", build, test: "ctest --test-dir .tb-check-build" }] }) : original(name, payload));
    setup(); await screen.findByLabelText("Build command");
    expect(screen.queryByText("Discover automatically")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Find commands" }));
    await screen.findByText(build);
    expect(screen.getByText(/is the CMake output folder/)).toBeInTheDocument();
    expect(transport.command.mock.calls.some(([name]) => name.endsWith(".replace"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Use" }));
    expect(screen.getByLabelText("Build command")).toHaveValue(build);
    fireEvent.click(screen.getByRole("button", { name: "Save checks" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.configuration.replace", expect.objectContaining({ content: expect.objectContaining({ build, discover: false }) })));
  });

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

  it("matches a selected local folder without asking for a remote or binding", async () => {
    snapshot.repositories.items = [{ ...snapshot.repository_registry!.items[0], id: "binding", project_id: project.id }];
    setup();
    await screen.findByLabelText("Build command");
    fireEvent.click(screen.getByRole("button", { name: "Repositories" }));
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save repositories" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add local repository" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.repository.attach",
      { bunker_id: "lab", project_id: "nav", path: "/work/navigation" }));
    expect(transport.command.mock.calls.some(([name]) => name === "application.project.repositories.select")).toBe(false);
  });

  it("lets members connect local folders but not change the shared selection", async () => {
    snapshot.permission = { can_write: false, reason: "Ask an owner", capabilities: [] };
    snapshot.repositories.items = [{ ...snapshot.repository_registry!.items[0], id: "binding", project_id: project.id }];
    render(<UnsavedChangesBoundary><ConfigurationPanel bunker={bunker} project={project} initialTab="repositories" onClose={vi.fn()} onBunker={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
    expect(await screen.findByRole("button", { name: "Add local repository" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Save repositories" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add repository" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Repository 1 allowed remote")).not.toBeInTheDocument();
  });
});
describe("Bunker repository availability", () => {
  const child = { id: "tools", key: "TOOLS", name: "Tools", children: [] };
  const tree = [{ ...project, children: [child] }];
  function registrySetup(canWrite = true) {
    snapshot.scope_type = "bunker"; snapshot.scope_id = bunker.id;
    snapshot.repositories = structuredClone(snapshot.repository_registry!);
    snapshot.permission.can_write = canWrite;
    const other = { ...snapshot.repositories.items[0], id: "existing", project_id: project.id, canonical_remote: "github.com/example/other" };
    let bindings = [other];
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation(async (name, payload) => {
      if (name === "application.bunker.configuration.get" && payload.project_id) return {
        ...structuredClone(snapshot), scope_type: "project", scope_id: payload.project_id,
        repositories: { revision: `revision-${payload.project_id}`, items: bindings.filter((row) => row.project_id === payload.project_id) },
      };
      if (name === "application.project.repositories.select") {
        bindings = [...bindings.filter((row) => row.project_id !== payload.project_id), ...payload.repositories.map((row: { id: string }) => ({ ...row, id: row.id || `binding-${payload.project_id}`, project_id: payload.project_id }))];
        return { bunker: { ...bunker, projects: tree, repositories: bindings } };
      }
      return original(name, payload);
    });
    render(<UnsavedChangesBoundary><ConfigurationPanel bunker={{ ...bunker, projects: tree, repositories: bindings }} initialTab="repositories" onClose={vi.fn()} onBunker={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
  }
  it("toggles the same remote for multiple Projects, including subprojects, preserving other bindings", async () => {
    registrySetup();
    fireEvent.click(await screen.findByText("Projects · 0"));
    const parent = screen.getByRole("checkbox", { name: "NAV · Navigation availability for Navigation" });
    const nested = screen.getByRole("checkbox", { name: "NAV · Navigation / TOOLS · Tools availability for Navigation" });
    fireEvent.click(parent);
    await waitFor(() => expect(parent).toBeChecked());
    expect(transport.command).toHaveBeenCalledWith("application.project.repositories.select", expect.objectContaining({
      expected_revision: "revision-nav", repositories: [expect.objectContaining({ id: "existing" }), expect.objectContaining({ id: "", canonical_remote: "github.com/example/navigation" })],
    }));
    fireEvent.click(nested);
    await waitFor(() => expect(nested).toBeChecked());
    expect(parent).toBeChecked();
    fireEvent.click(parent);
    await waitFor(() => expect(parent).not.toBeChecked());
    expect(nested).toBeChecked();
    expect(transport.command).toHaveBeenLastCalledWith("application.project.repositories.select", expect.objectContaining({ project_id: "nav", repositories: [expect.objectContaining({ id: "existing" })] }));
  });
  it("does not show an unaccepted toggle as saved", async () => {
    registrySetup();
    fireEvent.click(await screen.findByText("Projects · 0"));
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((name, payload) => name === "application.project.repositories.select" ? Promise.reject(new Error("Repositories changed. Reopen and retry.")) : original(name, payload));
    const parent = screen.getByRole("checkbox", { name: "NAV · Navigation availability for Navigation" });
    fireEvent.click(parent);
    expect(await screen.findByText(/Repositories changed/)).toBeInTheDocument();
    expect(parent).not.toBeChecked();
    expect(parent).toBeEnabled();
  });
  it("prevents availability writes while remotes are unsaved", async () => {
    registrySetup();
    fireEvent.change(await screen.findByLabelText("Repository 1 name"), { target: { value: "Renamed" } });
    fireEvent.click(screen.getByText("Projects · 0"));
    expect(screen.getByText("Save repositories before changing availability.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "NAV · Navigation availability for Renamed" })).toBeDisabled();
  });
  it("shows members the assignments without allowing policy changes", async () => {
    registrySetup(false);
    fireEvent.click(await screen.findByText("Projects · 0"));
    expect(screen.getByRole("checkbox", { name: "NAV · Navigation availability for Navigation" })).toBeDisabled();
  });
});

describe("Configuration draft safety", () => {
  it("cancels without discarding and explicitly discards without publishing", async () => {
    const close = setup();
    const input = await editBuild();
    fireEvent.click(screen.getByRole("button", { name: "Close configuration" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Save applies these settings");
    fireEvent.click(screen.getByText("Cancel"));
    expect(close).not.toHaveBeenCalled(); expect(input).toHaveValue("make navigation");
    fireEvent.click(screen.getByRole("button", { name: "Close configuration" }));
    fireEvent.click(screen.getByText("Do not save"));
    expect(close).toHaveBeenCalledOnce();
    expect(transport.command.mock.calls.every(([name]) => name.endsWith(".get"))).toBe(true);
  });
  it("publishes with the loaded revision before leaving", async () => {
    const close = setup(); await editBuild();
    fireEvent.click(screen.getByRole("button", { name: "Close configuration" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByText("Save"));
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(transport.command).toHaveBeenCalledWith("application.bunker.configuration.replace", expect.objectContaining({ expected_revision: "checks:1", content: expect.objectContaining({ build: "make navigation" }) }));
  });
  it("retains the draft when publishing fails", async () => {
    const close = setup(); const input = await editBuild();
    transport.command.mockRejectedValue(new Error("Another member changed Checks. Reload before retrying."));
    fireEvent.click(screen.getByRole("button", { name: "Close configuration" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByText("Save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Another member changed Checks");
    expect(close).not.toHaveBeenCalled(); expect(input).toHaveValue("make navigation");
  });
  it("preserves a checks draft and its revision when another section publishes", async () => {
    snapshot.repositories.items = [{ ...snapshot.repository_registry!.items[0], id: "binding", project_id: project.id }];
    setup(); await editBuild();
    snapshot.documents.checks = { ...snapshot.documents.checks!, revision: "checks:remote", content: { ...snapshot.documents.checks!.content, build: "someone else's command" } };
    fireEvent.click(screen.getByRole("button", { name: "Repositories" }));
    fireEvent.click(screen.getByRole("button", { name: "Add local repository" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.repository.attach", expect.anything()));
    await waitFor(() => expect(screen.getByRole("button", { name: "Checks" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Checks" }));
    expect(screen.getByLabelText("Build command")).toHaveValue("make navigation");
    fireEvent.click(screen.getByRole("button", { name: "Save checks" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.configuration.replace", expect.objectContaining({ expected_revision: "checks:1" })));
  });

  it("does not republish successful sections when Save retries a partial failure", async () => {
    snapshot.scope_type = "bunker"; snapshot.scope_id = bunker.id;
    snapshot.documents["ticket-fields"] = { kind: "ticket-fields", updated_at: "", revision: "fields:1", content: { fields: [] } };
    const close = vi.fn();
    render(<UnsavedChangesBoundary><ConfigurationPanel bunker={bunker} initialTab="defaults" onClose={close} onBunker={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
    fireEvent.click(await screen.findByRole("button", { name: "Add field" }));
    fireEvent.click(screen.getByRole("button", { name: "Repositories" }));
    fireEvent.click(screen.getByRole("button", { name: "Add repository" }));
    fireEvent.change(screen.getByLabelText("Repository 1 allowed remote"), { target: { value: "github.com/example/navigation" } });
    const original = transport.command.getMockImplementation()!;
    transport.command.mockImplementation((name, payload) => name === "application.bunker.repository.replace"
      ? Promise.reject(new Error("Repository save is offline")) : original(name, payload));
    fireEvent.click(screen.getByRole("button", { name: "Close configuration" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByText("Save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Repository save is offline");
    expect(close).not.toHaveBeenCalled();
    transport.command.mockImplementation(original);
    fireEvent.click(within(screen.getByRole("dialog")).getByText("Save"));
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(transport.command.mock.calls.filter(([name]) => name === "application.bunker.configuration.replace")).toHaveLength(1);
  });

  it("retains an absent base revision if someone creates Checks during editing", async () => {
    snapshot.repositories.items = [{ ...snapshot.repository_registry!.items[0], id: "binding", project_id: project.id }];
    delete snapshot.documents.checks;
    setup(); await editBuild();
    snapshot.documents.checks = { ...initial.documents.checks!, revision: "checks:remote" };
    fireEvent.click(screen.getByRole("button", { name: "Repositories" }));
    fireEvent.click(screen.getByRole("button", { name: "Add local repository" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.repository.attach", expect.anything()));
    await waitFor(() => expect(screen.getByRole("button", { name: "Checks" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Checks" }));
    fireEvent.click(screen.getByRole("button", { name: "Save checks" }));
    await waitFor(() => expect(transport.command).toHaveBeenCalledWith("application.bunker.configuration.replace", expect.objectContaining({ expected_revision: "" })));
  });
});
