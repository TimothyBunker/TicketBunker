import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RepositoryWorkspace from "./RepositoryWorkspace";
import { UnsavedChangesBoundary } from "./UnsavedChanges";
import type { Bunker, Repository, TicketDetail } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
vi.mock("./WorkspaceEditor", () => ({ default: ({ value }: { value: string }) => <textarea aria-label="Test editor" value={value} readOnly /> }));
vi.mock("./TicketWorkspace", () => ({ CoworkerDrawer: ({ detail }: { detail: TicketDetail }) => <p>Session: {detail.ticket.title}</p> }));
const repository = { id: "repo", project_id: "project", name: "Lab", local_path: "/lab/source" } as Repository;
const bunker = { id: "lab", repositories: [repository], projects: [] } as unknown as Bunker;
const project = { id: "project", key: "LAB", name: "Lab" };
const detail = (title = "Original ticket", id = "LAB-1") => ({ project, ticket: { id, title }, repositories: [repository], reports: [] });
const pending = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };
function start() {
  return render(<UnsavedChangesBoundary><RepositoryWorkspace bunker={bunker} repository={repository} visible onTicket={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
}
beforeEach(() => {
  sessionStorage.clear();
  transport.command.mockReset();
  transport.command.mockImplementation(async (action, payload) => {
    if (action === "workspace.open") return { root: repository.local_path, branch: "main", head: "head", revision: 0, state: { bunkerId: bunker.id, overlays: ["LAB-1"], conversation: "LAB-1" } };
    if (action === "workspace.tree") return { entries: [] };
    if (action === "workspace.find") return { paths: [] };
    if (action === "workspace.save") return { revision: payload.revision + 1 };
    if (action === "application.ticket.get") return detail();
    if (action === "application.ticket.list") return { tickets: [] };
    throw new Error(action);
  });
});
afterEach(cleanup);

it("can retry the initial workspace load after the service becomes available", async () => {
  const original = transport.command.getMockImplementation()!;
  let unavailable = true;
  transport.command.mockImplementation((action, payload) => action === "workspace.open" && unavailable ? Promise.reject(new Error("Service unavailable")) : original(action, payload));
  start();
  expect(await screen.findByRole("alert")).toHaveTextContent("Service unavailable");
  unavailable = false;
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace" }));
  expect(await screen.findByRole("navigation", { name: "Repository files" })).toBeVisible();
  expect(screen.queryByText("Loading workspace…")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("clears an ordinary refresh failure after retry without clearing unrelated recovery failures", async () => {
  start(); await screen.findByRole("navigation", { name: "Repository files" });
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.open" ? Promise.reject(new Error("Refresh interrupted")) : original(action, payload));
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Refresh interrupted");
  transport.command.mockImplementation(original);
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace" }));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

it("refreshes the selected ticket metadata even when the Git branch has not changed", async () => {
  start(); fireEvent.click(await screen.findByRole("button", { name: "Session" }));
  await screen.findByText("Session: Original ticket");
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "application.ticket.get" ? Promise.resolve(detail("Updated ticket")) : original(action, payload));
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace" }));
  expect(await screen.findByText("Session: Updated ticket")).toBeVisible();
});

it("removes obsolete ticket context after a refreshed ticket no longer uses the repository", async () => {
  start(); fireEvent.click(await screen.findByRole("button", { name: "Session" }));
  await screen.findByText("Session: Original ticket");
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => {
    if (action === "workspace.open") return Promise.resolve({ root: repository.local_path, branch: "next", head: "next", revision: 0, state: {} });
    if (action === "application.ticket.get") return Promise.resolve({ ...detail(), repositories: [] });
    return original(action, payload);
  });
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("does not use this repository");
  expect(screen.queryByText("Session: Original ticket")).not.toBeInTheDocument();
});

it("ignores an older ticket context response after a later refresh completed", async () => {
  const slow = pending<ReturnType<typeof detail>>();
  let reads = 0;
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "application.ticket.get" ? (++reads === 1 ? slow.promise : Promise.resolve(detail("Latest ticket"))) : original(action, payload));
  start(); fireEvent.click(await screen.findByRole("button", { name: "Session" }));
  await waitFor(() => expect(reads).toBe(1));
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace" }));
  await screen.findByText("Session: Latest ticket");
  await act(async () => slow.resolve(detail("Old response")));
  expect(screen.getByText("Session: Latest ticket")).toBeVisible();
  expect(screen.queryByText("Session: Old response")).not.toBeInTheDocument();
});

it("clears a ticket-load error when retrying its context succeeds", async () => {
  let failing = true;
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "application.ticket.get" && failing ? Promise.reject(new Error("Ticket temporarily unavailable")) : original(action, payload));
  start();
  expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
  failing = false;
  fireEvent.click(screen.getByRole("button", { name: "Refresh workspace" }));
  fireEvent.click(screen.getByRole("button", { name: "Session" }));
  await screen.findByText("Session: Original ticket");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("refuses a 33rd overlay before making recovery unsaveable", async () => {
  const overlays = Array.from({ length: 32 }, (_, index) => `LAB-${index + 1}`);
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => {
    if (action === "workspace.open") return Promise.resolve({ root: repository.local_path, branch: "main", head: "head", revision: 0, state: { bunkerId: bunker.id, overlays, conversation: "LAB-1" } });
    if (action === "application.ticket.get") return Promise.resolve(detail(payload.ticket_id, payload.ticket_id));
    if (action === "application.ticket.list") return Promise.resolve({ tickets: [{ id: "LAB-33", title: "Extra ticket" }] });
    return original(action, payload);
  });
  start(); fireEvent.click(await screen.findByRole("button", { name: "Add ticket overlay" }));
  fireEvent.click(await screen.findByRole("button", { name: "LAB-33 Extra ticket" }));
  expect(screen.getByRole("alert")).toHaveTextContent("32 ticket overlays");
  expect(screen.queryByRole("button", { name: "Hide LAB-33 overlay" })).not.toBeInTheDocument();
});

it("recognizes an already saved draft after its successful save receipt was lost", async () => {
  const original = transport.command.getMockImplementation()!;
  const restored = { path: "file.py", branch: "main", content: "already on disk", saved: "old text", digest: "old", line: 1, settings: {} };
  transport.command.mockImplementation((action, payload) => {
    if (action === "workspace.open") return Promise.resolve({ root: repository.local_path, branch: "main", head: "head", revision: 0, state: { tabs: [restored], active: "file.py", bunkerId: bunker.id } });
    if (action === "workspace.file.read") return Promise.resolve({ ...restored, content: "already on disk", digest: "new" });
    return original(action, payload);
  });
  start();
  await screen.findByLabelText("Test editor");
  await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeDisabled());
  expect(screen.getByRole("tab", { name: "file.py" })).toBeInTheDocument();
  expect(transport.command.mock.calls.some(([action]) => action === "workspace.file.save")).toBe(false);
});
