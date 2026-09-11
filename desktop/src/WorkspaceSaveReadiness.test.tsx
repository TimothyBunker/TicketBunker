import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RepositoryWorkspace from "./RepositoryWorkspace";
import { UnsavedChangesBoundary } from "./UnsavedChanges";
import type { Bunker, Repository } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
vi.mock("./WorkspaceEditor", () => ({ default: ({ value, onChange, onSave }: { value: string; onChange: (value: string) => void; onSave: () => void }) =>
  <textarea aria-label="File draft" value={value} onChange={event => onChange(event.target.value)} onKeyDown={event => { if (event.ctrlKey && event.key === "s") { event.preventDefault(); onSave(); } }} /> }));
const repository = { id: "repo", project_id: "project", name: "Save lab", local_path: "/save-lab" } as Repository;
const bunker = { id: "lab", repositories: [repository], projects: [] } as unknown as Bunker;
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
let disk = "original";
beforeEach(() => {
  sessionStorage.clear(); transport.command.mockReset(); disk = "original";
  transport.command.mockImplementation(async (action, payload) => {
    if (action === "workspace.open") return { root: repository.local_path, branch: "main", head: "head", revision: 0, state: {} };
    if (action === "workspace.tree") return { entries: [{ path: "file.py", name: "file.py", directory: false }] };
    if (action === "workspace.find") return { paths: [] };
    if (action === "workspace.file.read") return { path: payload.path, content: disk, branch: "main", digest: disk, line: 1, settings: {} };
    if (action === "workspace.file.save") { expect(payload.expected_digest).toBe(disk); disk = payload.content; return { content: disk, digest: disk }; }
    if (action === "workspace.save") return { revision: payload.revision + 1 };
    throw new Error(action);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
async function start() {
  render(<UnsavedChangesBoundary><RepositoryWorkspace bunker={bunker} repository={repository} visible onTicket={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
  fireEvent.click(await screen.findByRole("button", { name: "file.py" }));
  return screen.findByLabelText("File draft");
}
const shortcut = (editor: HTMLElement) => fireEvent.keyDown(editor, { key: "s", ctrlKey: true });

it("saves a second explicit edit while the first save's recovery receipt is held", async () => {
  const first = deferred<{ revision: number }>();
  const second = deferred<{ revision: number }>();
  let recoveries = 0;
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.save" ? (++recoveries === 1 ? first.promise : second.promise) : original(action, payload));
  const editor = await start();
  fireEvent.change(editor, { target: { value: "first" } }); shortcut(editor);
  await waitFor(() => expect(disk).toBe("first"));
  await waitFor(() => expect(transport.command.mock.calls.some(([action]) => action === "workspace.save")).toBe(true));
  fireEvent.change(editor, { target: { value: "second" } });
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  shortcut(editor);
  await waitFor(() => expect(disk).toBe("second"));
  await act(async () => first.resolve({ revision: 1 }));
  await waitFor(() => expect(recoveries).toBe(2));
  // The first operation finishing cannot remove the second operation's close guard.
  const closing = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(closing);
  expect(closing.defaultPrevented).toBe(true);
  await act(async () => second.resolve({ revision: 2 }));
  const finished = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(finished);
  expect(finished.defaultPrevented).toBe(false);
  expect(transport.command.mock.calls.filter(([action]) => action === "workspace.file.save")).toHaveLength(2);
});

it("explains a shortcut during an actual write and never queues later edits implicitly", async () => {
  const held = deferred<{ content: string; digest: string }>();
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.file.save" ? held.promise : original(action, payload));
  const editor = await start();
  fireEvent.change(editor, { target: { value: "first" } }); shortcut(editor);
  fireEvent.change(editor, { target: { value: "newer" } }); shortcut(editor);
  expect(screen.getByRole("alert")).toHaveTextContent(/save.*in progress/i);
  expect(screen.getByRole("alert")).toHaveTextContent(/press Save again/i);
  await act(async () => held.resolve({ content: "first", digest: "first" }));
  expect(editor).toHaveValue("newer");
  expect(transport.command.mock.calls.filter(([action]) => action === "workspace.file.save")).toHaveLength(1);
});

it("retains newer edits after a rejected write and requires an explicit retry", async () => {
  const held = deferred<{ content: string; digest: string }>();
  const original = transport.command.getMockImplementation()!;
  let saves = 0;
  transport.command.mockImplementation((action, payload) => action === "workspace.file.save" && saves++ === 0 ? held.promise : original(action, payload));
  const editor = await start();
  shortcut(editor);
  expect(transport.command.mock.calls.filter(([action]) => action === "workspace.file.save")).toHaveLength(0);
  fireEvent.change(editor, { target: { value: "first" } }); shortcut(editor);
  fireEvent.change(editor, { target: { value: "newer" } });
  await act(async () => held.reject(new Error("Write receipt unavailable")));
  expect(editor).toHaveValue("newer");
  expect(disk).toBe("original");
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  expect(transport.command.mock.calls.filter(([action]) => action === "workspace.file.save")).toHaveLength(1);
  shortcut(editor);
  await waitFor(() => expect(disk).toBe("newer"));
});
