import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RepositoryWorkspace from "./RepositoryWorkspace";
import { UnsavedChangesBoundary } from "./UnsavedChanges";
import type { Bunker, Repository, TicketReference } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
vi.mock("./WorkspaceEditor", () => ({ default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => <textarea aria-label="Test editor" value={value} onChange={(event) => onChange(event.target.value)} /> }));
const repositories: Repository[] = ["one", "two"].map((id) => ({ id, project_id: "project", name: id, local_path: `/work/${id}` }));
const bunker = { id: "bunker", repositories, projects: [], name: "Demo" } as unknown as Bunker;
afterEach(cleanup);
beforeEach(() => {
  transport.command.mockReset();
  transport.command.mockImplementation(async (action, payload) => {
    if (action === "workspace.open") return { root: `/work/${payload.repository_id}`, branch: "main", head: "abc", revision: 0, state: {} };
    if (action === "workspace.tree") return { entries: [{ path: "file.py", name: "file.py", directory: false }] };
    if (action === "workspace.find") return { paths: [] };
    if (action === "workspace.save") return { revision: payload.revision + 1 };
    if (action === "workspace.file.read") return { path: payload.path, line: 1, branch: "main", content: "original", digest: "a".repeat(64), settings: {} };
    if (action === "workspace.file.save") return { content: payload.content, digest: "b".repeat(64) };
    throw new Error(`Unexpected ${action}`);
  });
});

it("retains independent file drafts when switching repository views", async () => {
  const props = { bunker, onTicket: vi.fn(), notify: vi.fn() };
  const app = (second = false) => <UnsavedChangesBoundary>{repositories.map((repository, index) => <div key={repository.id} hidden={second !== Boolean(index)}><RepositoryWorkspace {...props} repository={repository} visible={second === Boolean(index)} /></div>)}</UnsavedChangesBoundary>;
  const rendered = render(app());
  await screen.findByRole("button", { name: "file.py" });
  fireEvent.click(screen.getByRole("button", { name: "file.py" }));
  fireEvent.change(await screen.findByLabelText("Test editor"), { target: { value: "first draft" } });
  rendered.rerender(app(true));
  fireEvent.click(await screen.findByRole("button", { name: "file.py" }));
  const second = within(screen.getByRole("region", { name: "two workspace" }));
  expect(await second.findByLabelText("Test editor")).toHaveValue("original");
  fireEvent.change(second.getByLabelText("Test editor"), { target: { value: "second draft" } });
  rendered.rerender(app());
  expect(within(screen.getByRole("region", { name: "one workspace" })).getByLabelText("Test editor")).toHaveValue("first draft");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await waitFor(() => expect(transport.command).toHaveBeenCalledWith("workspace.save", expect.objectContaining({ repository_id: "one", state: expect.objectContaining({ tabs: [expect.objectContaining({ content: "first draft" })] }) })));
});

it("keeps a rejected save and requires confirmation to discard its tab", async () => {
  const original = transport.command.getMockImplementation()!;
  transport.command.mockImplementation((action, payload) => action === "workspace.file.save" ? Promise.reject(new Error("The checkout changed")) : original(action, payload));
  render(<UnsavedChangesBoundary><RepositoryWorkspace bunker={bunker} repository={repositories[0]} visible onTicket={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
  fireEvent.click(await screen.findByRole("button", { name: "file.py" }));
  fireEvent.change(await screen.findByLabelText("Test editor"), { target: { value: "keep me" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("checkout changed");
  expect(screen.getByLabelText("Test editor")).toHaveValue("keep me");
  fireEvent.click(screen.getByRole("button", { name: "Close file.py" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByText("Cancel"));
  expect(screen.getByLabelText("Test editor")).toHaveValue("keep me");
  fireEvent.click(screen.getByRole("button", { name: "Close file.py" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByText("Do not save"));
  expect(screen.queryByLabelText("Test editor")).not.toBeInTheDocument();
});

it("keeps ticket and file chat drafts separate while overlays share the checkout", async () => {
  const original = transport.command.getMockImplementation()!;
  const project = { id: "project", key: "DEMO", name: "Demo" };
  const tickets = ["DEMO-1", "DEMO-2"].map((id) => ({ id, title: id, project })) as TicketReference[];
  transport.command.mockImplementation((action, payload) => {
    if (action === "application.ticket.list") return Promise.resolve({ tickets });
    if (action === "application.ticket.get") return Promise.resolve({ project, ticket: tickets.find((ticket) => ticket.id === payload.ticket_id), repositories, reports: [] });
    if (action === "coworker.state" || action === "coworker.chat") return Promise.resolve({ messages: [], proposals: [], category: payload.category, path: payload.path });
    return original(action, payload);
  });
  render(<UnsavedChangesBoundary><RepositoryWorkspace bunker={bunker} repository={repositories[0]} visible onTicket={vi.fn()} notify={vi.fn()} /></UnsavedChangesBoundary>);
  fireEvent.click(await screen.findByRole("button", { name: "file.py" }));
  await screen.findByLabelText("Test editor");
  fireEvent.click(screen.getByRole("button", { name: "Add ticket overlay" }));
  fireEvent.click(await screen.findByRole("button", { name: "DEMO-1 DEMO-1" }));
  fireEvent.click(screen.getByRole("button", { name: "Session" }));
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "first ticket question" } });
  fireEvent.click(screen.getByRole("button", { name: "Comments" }));
  fireEvent.click(screen.getByRole("button", { name: "Session" }));
  expect(screen.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("first ticket question");
  fireEvent.click(screen.getByRole("button", { name: "Add ticket overlay" }));
  fireEvent.click(await screen.findByRole("button", { name: "DEMO-2 DEMO-2" }));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue(""));
  fireEvent.change(screen.getByLabelText("Active ticket session"), { target: { value: "DEMO-1" } });
  expect(screen.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("first ticket question");
  const input = screen.getByLabelText("Find file or open path");
  fireEvent.change(input, { target: { value: "other.py" } });
  fireEvent.submit(input.closest("form")!);
  await screen.findByRole("tab", { name: "other.py" });
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue(""));
  fireEvent.click(screen.getByRole("tab", { name: "file.py" }));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("first ticket question"));
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(transport.command).toHaveBeenCalledWith("coworker.chat", expect.objectContaining({ ticket: "DEMO-1", path: "file.py", category: "file", scope: "main", message: "first ticket question" }), "/work/one"));
  expect(transport.command.mock.calls.some(([action]) => action === "workflow.start" || action === "truth.synthesize")).toBe(false);
});
