import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RepositoryWorkspace from "./RepositoryWorkspace";
import { UnsavedChangesBoundary } from "./UnsavedChanges";
import type { Bunker, Repository } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
vi.mock("./WorkspaceEditor", () => ({ default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => <textarea aria-label="Workspace draft" value={value} onChange={(event) => onChange(event.target.value)} /> }));
const repository = { id: "repo", project_id: "project", name: "Close lab", local_path: "/close-lab/source" } as Repository;
const bunker = { id: "lab", repositories: [repository], projects: [] } as unknown as Bunker;
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
let close!: (event: { preventDefault(): void }) => void;
let disk = "original";
const destroy = vi.fn();
beforeEach(() => {
  sessionStorage.clear(); disk = "original"; destroy.mockReset().mockResolvedValue(undefined);
  window.__TAURI__ = { core: { invoke: vi.fn() }, window: { getCurrentWindow: () => ({ destroy, onCloseRequested: (callback) => { close = callback; return Promise.resolve(vi.fn()); } }) } };
  transport.command.mockReset();
  transport.command.mockImplementation(async (action, payload) => {
    if (action === "workspace.open") return { root: repository.local_path, branch: "main", head: "head", revision: 0, state: {} };
    if (action === "workspace.tree") return { entries: [{ path: "file.py", name: "file.py", directory: false }] };
    if (action === "workspace.find") return { paths: [] };
    if (action === "workspace.save") return { revision: payload.revision + 1 };
    if (action === "workspace.file.read") return { path: payload.path, content: disk, branch: "main", digest: disk, line: 1, settings: {} };
    if (action === "workspace.file.save") { disk = payload.content; return { content: disk, digest: disk }; }
    throw new Error(action);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); delete window.__TAURI__; });
async function start() {
  render(<UnsavedChangesBoundary><RepositoryWorkspace bunker={bunker} repository={repository} visible onTicket={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
  fireEvent.click(await screen.findByRole("button", { name: "file.py" }));
  const editor = await screen.findByLabelText("Workspace draft");
  fireEvent.change(editor, { target: { value: "first change" } });
  return editor;
}
function requestClose() { const preventDefault = vi.fn(); act(() => close({ preventDefault })); return preventDefault; }

it("does not destroy the native window if more edits arrive while Save All is in flight", async () => {
  const saved = deferred<{ content: string; digest: string }>();
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.file.save" ? saved.promise : original(action, payload));
  const editor = await start();
  expect(requestClose()).toHaveBeenCalledOnce();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }));
  fireEvent.change(editor, { target: { value: "newer change while saving" } });
  await act(async () => saved.resolve({ content: "first change", digest: "saved" }));
  expect(editor).toHaveValue("newer change while saving");
  expect(destroy).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent(/new|unsaved|changed/i);
});

it("treats successful source saving separately from unavailable workspace recovery", async () => {
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.save" ? Promise.reject(new Error("Recovery disk is full")) : original(action, payload));
  await start(); requestClose();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(disk).toBe("first change"));
  await waitFor(() => expect(destroy).toHaveBeenCalledOnce());
  expect(transport.command.mock.calls.filter(([action]) => action === "workspace.file.save")).toHaveLength(1);
});

it("honors explicit discard even if another window owns durable recovery", async () => {
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.save" ? Promise.reject(new Error("Another window saved newer recovery")) : original(action, payload));
  await start(); fireEvent(window, new Event("beforeunload"));
  expect(sessionStorage.length).toBe(1);
  requestClose(); fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Do not save" }));
  await waitFor(() => expect(destroy).toHaveBeenCalledOnce());
  expect(disk).toBe("original");
  expect(sessionStorage.length).toBe(0);
  expect(transport.command.mock.calls.some(([action]) => action === "workspace.file.save")).toBe(false);
});

it("keeps text reachable and reports quota failure when both recovery paths are unavailable", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Quota exceeded", "QuotaExceededError"); });
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.save" ? Promise.reject(new Error("Recovery disk is full")) : original(action, payload));
  const editor = await start();
  fireEvent(window, new Event("beforeunload"));
  expect(await screen.findByRole("alert")).toHaveTextContent(/Save your files or copy/i);
  expect(editor).toHaveValue("first change");
  requestClose(); fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
  expect(destroy).not.toHaveBeenCalled();
  expect(editor).toHaveValue("first change");
});

it("does not close a file tab after a delayed save leaves newer unsaved changes", async () => {
  const saved = deferred<{ content: string; digest: string }>();
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.file.save" ? saved.promise : original(action, payload));
  const editor = await start(); fireEvent.click(screen.getByRole("button", { name: "Close file.py" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }));
  fireEvent.change(editor, { target: { value: "updated while tab save pending" } });
  await act(async () => saved.resolve({ content: "first change", digest: "saved" }));
  expect(screen.getByLabelText("Workspace draft")).toHaveValue("updated while tab save pending");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("preserves a newer draft when source saving succeeds but both recovery writes fail", async () => {
  const saved = deferred<{ content: string; digest: string }>();
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => {
    if (action === "workspace.file.save") return saved.promise.then((result) => { disk = result.content; return result; });
    if (action === "workspace.save") return Promise.reject(new Error("Recovery disk is full"));
    return original(action, payload);
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Storage blocked", "SecurityError"); });
  const editor = await start(); requestClose();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }));
  fireEvent.change(editor, { target: { value: "new work still only in memory" } });
  await act(async () => saved.resolve({ content: "first change", digest: "saved" }));
  expect(disk).toBe("first change");
  expect(editor).toHaveValue("new work still only in memory");
  expect(destroy).not.toHaveBeenCalled();
  expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("New changes are still unsaved");
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("alert")).toHaveTextContent(/Saved file.py.*copy unsaved text/);
});

it("keeps the native dialog and draft when source saving itself fails", async () => {
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.file.save" ? Promise.reject(new Error("File is read-only")) : original(action, payload));
  const editor = await start(); requestClose();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent("Your draft is still open"));
  expect(destroy).not.toHaveBeenCalled(); expect(disk).toBe("original");
  expect(editor).toHaveValue("first change");
});

it("recovers the newest draft after repository detachment unmounts before the debounce", async () => {
  render(<UnsavedChangesBoundary><RepositoryWorkspace bunker={bunker} repository={repository} visible onTicket={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
  fireEvent.click(await screen.findByRole("button", { name: "file.py" }));
  const editor = await screen.findByLabelText("Workspace draft");
  fireEvent.change(editor, { target: { value: "Last keystroke before remote detachment" } });
  // An incoming configuration update can remove this component without a
  // browser unload event, before its 350ms recovery timer has run.
  cleanup();
  render(<UnsavedChangesBoundary><RepositoryWorkspace bunker={bunker} repository={repository} visible onTicket={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
  expect(await screen.findByLabelText("Workspace draft")).toHaveValue("Last keystroke before remote detachment");
  expect(disk).toBe("original");
  expect(transport.command.mock.calls.some(([action]) => action === "workspace.file.save")).toBe(false);
});
