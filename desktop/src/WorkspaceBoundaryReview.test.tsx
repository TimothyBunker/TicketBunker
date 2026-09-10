import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RepositoryWorkspace from "./RepositoryWorkspace";
import { UnsavedChangesBoundary } from "./UnsavedChanges";
import type { Bunker, Repository } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
vi.mock("./WorkspaceEditor", () => ({ default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => <textarea aria-label="Boundary editor" value={value} onChange={(event) => onChange(event.target.value)} /> }));
const repositories = ["one", "two"].map((id) => ({ id, project_id: "project", name: id, local_path: `/boundary/${id}` })) as Repository[];
const bunker = { id: "lab", repositories, projects: [] } as unknown as Bunker;
const tab = { path: "file.py", content: "original", saved: "original", branch: "main", head: "aaa", digest: "a".repeat(64), line: 1, settings: {} };
const base = { tabs: [tab], active: tab.path, overlays: ["QA-1"], conversation: "QA-1", bunkerId: bunker.id };
const rescueKey = `tb.workspace.rescue:${JSON.stringify({ bunker_id: bunker.id, repository_id: "one", expected_root: repositories[0].local_path })}`;
const pending = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };
const props = { bunker, onTicket: vi.fn(), notify: vi.fn() };
function start(visible = true) { return render(<UnsavedChangesBoundary><RepositoryWorkspace {...props} repository={repositories[0]} visible={visible} /></UnsavedChangesBoundary>); }
beforeEach(() => {
  sessionStorage.clear(); transport.command.mockReset();
  transport.command.mockImplementation(async (action, payload) => {
    if (action === "workspace.open") return { root: payload.expected_root, branch: "main", head: "aaa", revision: 4, state: base };
    if (action === "workspace.tree") return { entries: [{ name: "next.py", path: "next.py", directory: false }] };
    if (action === "workspace.find") return { paths: [] };
    if (action === "workspace.save") return { revision: payload.revision + 1 };
    if (action === "workspace.file.read") return { ...tab, path: payload.path };
    if (action === "application.ticket.get") return { project: { id: "project", key: "QA" }, ticket: { id: payload.ticket_id, title: "Boundary ticket" }, repositories, reports: [] };
    if (action === "application.ticket.list") return { tickets: [{ id: "QA-1", title: "Boundary ticket" }] };
    if (action === "coworker.state") return { messages: [], proposals: [], path: payload.path, category: payload.category };
    throw new Error(action);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("preserves unsent chat when an ordinary repository switch only hides its workspace", async () => {
  const app = (selected: number) => <UnsavedChangesBoundary>{repositories.map((repository, index) => <div key={repository.id} hidden={index !== selected}><RepositoryWorkspace {...props} repository={repository} visible={index === selected} /></div>)}</UnsavedChangesBoundary>;
  const rendered = render(app(0));
  const first = within(await screen.findByRole("region", { name: "one workspace" }));
  fireEvent.click(await first.findByRole("button", { name: "Session" }));
  fireEvent.change(await first.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "Unsent on repository one" } });
  rendered.rerender(app(1));
  const second = within(screen.getByRole("region", { name: "two workspace" }));
  fireEvent.click(await second.findByRole("button", { name: "Session" }));
  expect(await second.findByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("");
  rendered.rerender(app(0));
  expect(first.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("Unsent on repository one");
});

it("warns on closing a window with an unsent chat draft", async () => {
  start(); fireEvent.click(await screen.findByRole("button", { name: "Session" }));
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "I have not sent this explanation" } });
  const closing = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(closing);
  expect(closing.defaultPrevented).toBe(true);
});

it("retains a chat draft when its overlay is hidden then restored", async () => {
  start(); fireEvent.click(await screen.findByRole("button", { name: "Session" }));
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "Overlay draft to retain" } });
  fireEvent.click(screen.getByRole("button", { name: "Hide QA-1 overlay" }));
  fireEvent.click(screen.getByRole("button", { name: "Add ticket overlay" }));
  fireEvent.click(await screen.findByRole("button", { name: "QA-1 Boundary ticket" }));
  expect(await screen.findByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("Overlay draft to retain");
});

it("still warns before window close after a draft's overlay was removed", async () => {
  start(); fireEvent.click(await screen.findByRole("button", { name: "Session" }));
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "Draft from a now hidden overlay" } });
  fireEvent.click(screen.getByRole("button", { name: "Hide QA-1 overlay" }));
  const closing = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(closing);
  expect(closing.defaultPrevented).toBe(true);
});

it("preserves unsent chat through forced workspace unmount and reattachment", async () => {
  const rendered = start(); fireEvent.click(await screen.findByRole("button", { name: "Session" }));
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "Draft before binding update" } });
  rendered.unmount();
  start(); fireEvent.click(await screen.findByRole("button", { name: "Session" }));
  expect(await screen.findByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("Draft before binding update");
});

it("rejects duplicate rescue tab identities without overwriting the raw recovery", async () => {
  const raw = JSON.stringify({ state: { ...base, tabs: [{ ...tab, content: "first recovered draft" }, { ...tab, content: "second recovered draft" }] }, revision: 0 });
  sessionStorage.setItem(rescueKey, raw);
  vi.spyOn(console, "error").mockImplementation(() => {});
  start();
  expect(await screen.findByLabelText("Boundary editor")).toHaveValue("original");
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be restored");
  expect(sessionStorage.getItem(rescueKey)).toBe(raw);
});

it("does not acknowledge a detached-head draft against a different commit with identical file text", async () => {
  const restored = { ...tab, branch: "", head: "old-head", content: "draft identical to next commit" };
  sessionStorage.setItem(rescueKey, JSON.stringify({ state: { ...base, tabs: [restored] }, revision: 0 }));
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => {
    if (action === "workspace.open") return Promise.resolve({ root: payload.expected_root, branch: "", head: "new-head", revision: 4, state: base });
    if (action === "workspace.file.read") return Promise.resolve({ ...restored, head: "new-head", digest: "b".repeat(64) });
    return original(action, payload);
  });
  start();
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Changed on disk or branch"));
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  expect(screen.getByLabelText("Boundary editor")).toHaveValue(restored.content);
});

it("marks a newly opened file stale if its delayed read belongs to the previous checkout", async () => {
  const read = pending<typeof tab>();
  const original = transport.command.getMockImplementation()!;
  let branch = "main";
  transport.command.mockImplementation((action, payload) => {
    if (action === "workspace.open") return Promise.resolve({ root: payload.expected_root, branch, head: branch === "main" ? "aaa" : "bbb", revision: 4, state: base });
    if (action === "workspace.file.read" && payload.path === "next.py") return read.promise;
    return original(action, payload);
  });
  start(); await screen.findByLabelText("Boundary editor");
  fireEvent.click(await screen.findByRole("button", { name: "next.py" }));
  await waitFor(() => expect(transport.command).toHaveBeenCalledWith("workspace.file.read", expect.objectContaining({ path: "next.py" })));
  branch = "feature";
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace" }));
  await screen.findByText("⎇ feature");
  await act(async () => read.resolve({ ...tab, path: "next.py", content: "old checkout content" }));
  expect(screen.getByLabelText("Boundary editor")).toHaveValue("old checkout content");
  expect(screen.getByRole("status")).toHaveTextContent("Changed on disk or branch");
});
