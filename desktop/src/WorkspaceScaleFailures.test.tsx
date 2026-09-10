import { Component, type ReactNode } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RepositoryWorkspace from "./RepositoryWorkspace";
import { sourceNotes, verifiedStamp } from "./report";
import { UnsavedChangesBoundary } from "./UnsavedChanges";
import type { Bunker, Repository } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
vi.mock("./report", async (original) => { const actual = await original<typeof import("./report")>(); return { ...actual, sourceNotes: vi.fn(actual.sourceNotes), verifiedStamp: vi.fn(actual.verifiedStamp) }; });
vi.mock("./WorkspaceEditor", () => ({ default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => <textarea aria-label="Scale draft" value={value} onChange={(event) => onChange(event.target.value)} /> }));
const repository = { id: "repo", project_id: "project", name: "Scale lab", local_path: "/scale-lab/source" } as Repository;
const bunker = { id: "lab", repositories: [repository], projects: [] } as unknown as Bunker;
const tab = { path: "file.py", content: "durable source", saved: "durable source", branch: "main", digest: "a".repeat(64), line: 1, settings: {} };
const base = { tabs: [tab], active: "file.py", overlays: [], conversation: "", bunkerId: "lab", expanded: [] };
const rescueKey = `tb.workspace.rescue:${JSON.stringify({ bunker_id: "lab", repository_id: "repo", expected_root: repository.local_path })}`;
class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="alert">Workspace crashed</p> : this.props.children; }
}
function start() { return render(<Boundary><UnsavedChangesBoundary><RepositoryWorkspace bunker={bunker} repository={repository} visible onTicket={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary></Boundary>); }
beforeEach(() => {
  sessionStorage.clear(); vi.mocked(sourceNotes).mockClear(); vi.mocked(verifiedStamp).mockClear();
  transport.command.mockReset();
  transport.command.mockImplementation(async (action, payload) => {
    if (action === "workspace.open") return { root: repository.local_path, branch: "main", head: "abcdef", revision: 2, state: base };
    if (action === "workspace.tree") return { entries: [] };
    if (action === "workspace.find") return { paths: [] };
    if (action === "workspace.save") return { revision: payload.revision + 1 };
    if (action === "workspace.file.read") return { ...tab, path: payload.path };
    if (action === "coworker.state") return { messages: [], proposals: [], category: payload.category, path: payload.path };
    throw new Error(action);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each([
  ["non-array overlays", { ...base, overlays: "QA-1", tabs: [{ ...tab, content: "corrupt recovery" }] }],
  ["null settings", { ...base, tabs: [{ ...tab, content: "corrupt recovery", settings: null }] }],
  ["non-string path", { ...base, active: {}, tabs: [{ ...tab, path: {}, content: "corrupt recovery" }] }],
  ["non-string content", { ...base, tabs: [{ ...tab, content: { text: "corrupt recovery" } }] }],
  ["too many open tabs", { ...base, tabs: Array.from({ length: 31 }, (_, index) => ({ ...tab, path: `file-${index}.py`, content: "corrupt recovery" })), active: "file-0.py" }],
])("falls back to durable state for malformed local recovery: %s", async (_label, recovery) => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  sessionStorage.setItem(rescueKey, JSON.stringify({ state: recovery, revision: 0 }));
  start();
  expect(await screen.findByLabelText("Scale draft")).toHaveValue("durable source");
  expect(screen.queryByText("Workspace crashed")).not.toBeInTheDocument();
  expect(transport.command.mock.calls.some(([action]) => action === "workspace.file.save")).toBe(false);
});

it("parses a report's verified stamp once for all its file notes, not once per claim", async () => {
  const report = { repository_id: "repo", scope: "branch:main", content: `<!-- tb:verified branch=main commit=abc -->\n## What\n${Array.from({ length: 160 }, (_, index) => `- [real] Defect: guard case ${index} at \`file.py:${index + 1}\`.`).join("\n")}` };
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => {
    if (action === "workspace.open") return Promise.resolve({ root: repository.local_path, branch: "main", head: "abcdef", revision: 0, state: { ...base, overlays: ["QA-1"], conversation: "QA-1" } });
    if (action === "application.ticket.get") return Promise.resolve({ project: { id: "project", key: "QA" }, ticket: { id: "QA-1" }, repositories: [repository], reports: [report] });
    return original(action, payload);
  });
  start();
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Discuss" })).toHaveLength(160));
  expect(verifiedStamp).toHaveBeenCalledTimes(vi.mocked(sourceNotes).mock.calls.length);
  const count = vi.mocked(verifiedStamp).mock.calls.length;
  for (let index = 0; index < 24; index++) fireEvent.change(screen.getByLabelText("Scale draft"), { target: { value: `typing ${index}` } });
  expect(verifiedStamp).toHaveBeenCalledTimes(count);
});
