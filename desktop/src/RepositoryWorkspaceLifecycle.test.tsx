import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RepositoryWorkspace from "./RepositoryWorkspace";
import { UnsavedChangesBoundary } from "./UnsavedChanges";
import type { Bunker, Repository } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
vi.mock("./WorkspaceEditor", () => ({ default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => <textarea aria-label="Test editor" value={value} onChange={(event) => onChange(event.target.value)} /> }));
const repository = { id: "repo", project_id: "project", name: "Lab", local_path: "/lab/source" } as Repository;
const bunker = { id: "lab", repositories: [repository], projects: [] } as unknown as Bunker;
const draft = (path: string, content = "original") => ({ path, content, branch: "main", digest: content, line: 1, settings: {} });
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };
async function start() {
  render(<UnsavedChangesBoundary><RepositoryWorkspace bunker={bunker} repository={repository} visible onTicket={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
  await screen.findByLabelText("Find file or open path");
}
function open(path: string) {
  const input = screen.getByLabelText("Find file or open path");
  fireEvent.change(input, { target: { value: path } });
  fireEvent.submit(input.closest("form")!);
}
beforeEach(() => {
  sessionStorage.clear();
  transport.command.mockReset();
  transport.command.mockImplementation(async (action, payload) => {
    if (action === "workspace.open") return { root: "/lab/source", branch: "main", head: "head", revision: 0, state: {} };
    if (action === "workspace.tree") return { entries: [] };
    if (action === "workspace.find") return { paths: [] };
    if (action === "workspace.save") return { revision: payload.revision + 1 };
    if (action === "workspace.file.read") return draft(payload.path);
    if (action === "workspace.file.save") return { content: payload.content, digest: payload.content };
    throw new Error(action);
  });
});
afterEach(cleanup);

it("keeps each window's unsaved recovery through reload without changing source", async () => {
  await start(); open("file.py");
  fireEvent.change(await screen.findByLabelText("Test editor"), { target: { value: "window A draft" } });
  fireEvent(window, new Event("beforeunload"));
  cleanup();
  transport.command.mockClear();
  const original = transport.command.getMockImplementation()!;
  let durableDraft = "window B draft";
  transport.command.mockImplementation((action, payload) => {
    if (action === "workspace.open") return Promise.resolve({ root: "/lab/source", branch: "main", head: "head", revision: 2,
      state: { tabs: [{ ...draft("file.py"), saved: "original", content: durableDraft }], active: "file.py", bunkerId: "lab" } });
    if (action === "workspace.save") {
      if (payload.revision !== 2) return Promise.reject(new Error("Another window saved newer workspace state. Save or copy your draft before closing."));
      durableDraft = payload.state.tabs[0].content;
      return Promise.resolve({ revision: 3 });
    }
    return original(action, payload);
  });
  await start();
  expect(await screen.findByLabelText("Test editor")).toHaveValue("window A draft");
  await waitFor(() => expect(transport.command).toHaveBeenCalledWith("workspace.save", expect.objectContaining({ revision: 0 })));
  expect(await screen.findByRole("alert")).toHaveTextContent("Another window saved newer workspace state");
  expect(durableDraft).toBe("window B draft");
  expect(transport.command.mock.calls.some(([action]) => action === "workspace.file.save")).toBe(false);
});

it("moves focus onto the visible drawer control after hide and show", async () => {
  await start();
  for (const label of ["explorer", "ticket context"]) {
    const hide = screen.getByRole("button", { name: `Hide ${label}` });
    hide.focus(); fireEvent.click(hide);
    expect(screen.getByRole("button", { name: `Show ${label}` })).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: `Show ${label}` }));
    expect(screen.getByRole("button", { name: `Hide ${label}` })).toHaveFocus();
  }
});

it("does not steal an existing tab selection when a background file finishes opening", async () => {
  await start(); open("existing.py"); await screen.findByRole("tab", { name: "existing.py" });
  const slow = deferred<ReturnType<typeof draft>>();
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.file.read" ? slow.promise : original(action, payload));
  open("slow.py"); fireEvent.click(screen.getByRole("tab", { name: "existing.py" }));
  await act(async () => slow.resolve(draft("slow.py")));
  expect(screen.getByRole("tab", { name: "existing.py" })).toHaveAttribute("aria-selected", "true");
});

it("does not let an older file-open response steal the latest selected file", async () => {
  const first = deferred<ReturnType<typeof draft>>();
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.file.read" && payload.path === "slow.py" ? first.promise : original(action, payload));
  await start(); open("slow.py"); open("latest.py");
  await screen.findByRole("tab", { name: "latest.py", selected: true });
  await act(async () => first.resolve(draft("slow.py")));
  expect(screen.getByRole("tab", { name: "latest.py" })).toHaveAttribute("aria-selected", "true");
});

it("ignores a pre-save disk refresh that completes after a successful save", async () => {
  await start(); open("file.py"); await screen.findByLabelText("Test editor");
  const stale = deferred<ReturnType<typeof draft>>();
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.file.read" ? stale.promise : original(action, payload));
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace" }));
  await waitFor(() => expect(transport.command.mock.calls.filter(([action]) => action === "workspace.file.read")).toHaveLength(2));
  fireEvent.change(screen.getByLabelText("Test editor"), { target: { value: "saved newer work" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeDisabled());
  await act(async () => stale.resolve(draft("file.py")));
  expect(screen.getByLabelText("Test editor")).toHaveValue("saved newer work");
});

it("keeps edits typed while source saving is in flight", async () => {
  const saving = deferred<{ content: string; digest: string }>();
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.file.save" ? saving.promise : original(action, payload));
  await start(); open("file.py"); const editor = await screen.findByLabelText("Test editor");
  fireEvent.change(editor, { target: { value: "first draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  fireEvent.change(editor, { target: { value: "second draft" } });
  await act(async () => saving.resolve({ content: "first draft", digest: "saved" }));
  expect(editor).toHaveValue("second draft");
  expect(screen.getByRole("tab", { name: "file.py •" })).toBeInTheDocument();
});

it("reserves the open-file limit while reads are still pending", async () => {
  const reads: Array<{ path: string; resolve: (value: ReturnType<typeof draft>) => void }> = [];
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => {
    if (action !== "workspace.file.read") return original(action, payload);
    const pending = deferred<ReturnType<typeof draft>>(); reads.push({ path: payload.path, resolve: pending.resolve }); return pending.promise;
  });
  await start();
  for (let index = 0; index < 31; index++) open(`file-${index}.py`);
  await act(async () => { reads.forEach((read) => read.resolve(draft(read.path))); });
  expect(screen.getAllByRole("tab")).toHaveLength(30);
  expect(screen.getByRole("alert")).toHaveTextContent("30 open files");
});
