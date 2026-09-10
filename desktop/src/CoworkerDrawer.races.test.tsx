import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CoworkerDrawer, type Quote } from "./TicketWorkspace";
import type { TicketDetail } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
const detail = (id = "QA-1") => ({ ticket: { id }, project: { key: "QA", id: "qa" }, reports: [], repositories: [] }) as unknown as TicketDetail;
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const state = (text = "", payload: Record<string, unknown> = {}) => ({ ...payload, messages: text ? [{ role: "assistant", text }] : [], proposals: [] });
const props = () => ({ open: true, embedded: true, onOpen: vi.fn(), detail: detail(), bunkerId: "bunker", repoRoot: "/simulation/repo", scope: "dev/QA-1", quote: null as Quote | null, onQuote: vi.fn(), pending: null, onPendingSent: vi.fn(), openFile: "first.py", onReload: vi.fn(), notify: vi.fn() });
const send = async (text = "Explain this function") => {
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
};
afterEach(cleanup);
beforeEach(() => {
  transport.command.mockReset();
  transport.command.mockImplementation(async (_action, payload) => state("", payload));
});

it("does not show the old branch conversation while the new branch state is loading", async () => {
  const next = deferred<ReturnType<typeof state>>();
  transport.command.mockImplementation((_action, payload) => payload.scope === "dev/QA-2" ? next.promise : Promise.resolve(state("Old branch analysis", payload)));
  const input = props(); const rendered = render(<CoworkerDrawer {...input} />);
  await screen.findByText("Old branch analysis");
  rendered.rerender(<CoworkerDrawer {...input} scope="dev/QA-2" />);
  expect(screen.queryByText("Old branch analysis")).not.toBeInTheDocument();
  await act(async () => next.resolve(state("New branch analysis", { category: "file", path: "first.py", scope: "dev/QA-2" })));
  expect(screen.getByText("New branch analysis")).toBeInTheDocument();
});

it("keeps drafts separate across ticket and Bunker identity changes", async () => {
  const input = props(); const rendered = render(<CoworkerDrawer {...input} />);
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "QA-1 private draft" } });
  rendered.rerender(<CoworkerDrawer {...input} detail={detail("QA-2")} />);
  expect(screen.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("");
  rendered.rerender(<CoworkerDrawer {...input} bunkerId="another-bunker" />);
  expect(screen.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("");
  rendered.rerender(<CoworkerDrawer {...input} />);
  expect(screen.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("QA-1 private draft");
});

it("does not let an old state response replace a newer successful chat response", async () => {
  const initial = deferred<ReturnType<typeof state>>();
  transport.command.mockImplementation((action, payload) => action === "coworker.state" ? initial.promise : Promise.resolve(state("Fresh answer", payload)));
  render(<CoworkerDrawer {...props()} />);
  await send();
  await screen.findByText("Fresh answer");
  await act(async () => initial.resolve(state("Old saved history", { category: "file", path: "first.py" })));
  expect(screen.getByText("Fresh answer")).toBeInTheDocument();
  expect(screen.queryByText("Old saved history")).not.toBeInTheDocument();
});

it("keeps a late failure attached to its original file instead of the newly selected conversation", async () => {
  const reply = deferred<ReturnType<typeof state>>();
  transport.command.mockImplementation((action, payload) => action === "coworker.chat" ? reply.promise : Promise.resolve(state(`Context for ${payload.path}`, payload)));
  const input = props(); const rendered = render(<CoworkerDrawer {...input} />);
  await send("First file question");
  rendered.rerender(<CoworkerDrawer {...input} openFile="second.py" />);
  await screen.findByText("Context for second.py");
  await act(async () => reply.reject(new Error("First-file provider failure")));
  expect(screen.queryByText(/First-file provider failure/)).not.toBeInTheDocument();
  expect(screen.getByText("Context for second.py")).toBeInTheDocument();
  rendered.rerender(<CoworkerDrawer {...input} />);
  expect(screen.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("First file question");
});

it("never sends a selected source quote under a different branch scope", async () => {
  const input = props();
  const quote: Quote = { kind: "source", category: "file", scope: input.scope, branch: input.scope, path: "first.py", line_start: 2, line_end: 3, text: "unsaved new behavior", unsaved: true };
  const rendered = render(<CoworkerDrawer {...input} quote={quote} />);
  await screen.findByText(/unsaved new behavior/);
  rendered.rerender(<CoworkerDrawer {...input} quote={quote} scope="dev/QA-2" />);
  expect(screen.queryByText(/unsaved new behavior/)).not.toBeInTheDocument();
  await send();
  await waitFor(() => expect(transport.command).toHaveBeenCalledWith("coworker.chat", expect.objectContaining({ scope: "dev/QA-2", reference: null }), input.repoRoot));
});

it("does not erase a newer code selection when the previous question finishes", async () => {
  const response = deferred<ReturnType<typeof state>>();
  transport.command.mockImplementation((action, payload) => action === "coworker.chat" ? response.promise : Promise.resolve(state("", payload)));
  const input = props();
  const quote: Quote = { kind: "source", category: "file", scope: input.scope, branch: input.scope, path: "first.py", line_start: 2, line_end: 2, text: "first selection", unsaved: true };
  const rendered = render(<CoworkerDrawer {...input} quote={quote} />);
  await send();
  expect(transport.command).toHaveBeenCalledWith("coworker.chat", expect.objectContaining({ reference: quote }), input.repoRoot);
  const newer = { ...quote, line_start: 4, line_end: 5, text: "newer selection" };
  rendered.rerender(<CoworkerDrawer {...input} quote={newer} />);
  input.onQuote.mockClear();
  await act(async () => response.resolve(state("First selection answer", { category: "file", path: "first.py", scope: input.scope })));
  expect(input.onQuote).not.toHaveBeenCalledWith(null);
  expect(screen.getByText(/newer selection/)).toBeInTheDocument();
});

it("does not replace the new branch conversation when a prior branch proposal finishes accepting", async () => {
  const accepted = deferred<object>();
  transport.command.mockImplementation((action, payload) => action === "coworker.proposal.accept" ? accepted.promise : Promise.resolve({ ...state(payload.scope === "dev/QA-2" ? "New branch discussion" : "Old branch discussion", payload), proposals: [{ id: "proposal-1", status: "pending" }] }));
  const input = { ...props(), embedded: false, openFile: "" }; const rendered = render(<CoworkerDrawer {...input} />);
  fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
  rendered.rerender(<CoworkerDrawer {...input} scope="dev/QA-2" />);
  await screen.findByText("New branch discussion");
  await act(async () => accepted.resolve({}));
  expect(screen.getByText("New branch discussion")).toBeInTheDocument();
  expect(screen.queryByText("Old branch discussion")).not.toBeInTheDocument();
});

it("does not replace another file's conversation when a prior file finishes forgetting", async () => {
  const forgotten = deferred<object>();
  transport.command.mockImplementation((action, payload) => action === "coworker.file.forget" ? forgotten.promise : Promise.resolve(state(`Discussion of ${payload.path}`, payload)));
  const input = props(); const rendered = render(<CoworkerDrawer {...input} />);
  await screen.findByText("Discussion of first.py");
  fireEvent.click(screen.getByRole("button", { name: "Forget" }));
  rendered.rerender(<CoworkerDrawer {...input} openFile="second.py" />);
  await screen.findByText("Discussion of second.py");
  await act(async () => forgotten.resolve(state("Forgot first.py", { category: "standing", path: "" })));
  expect(screen.getByText("Discussion of second.py")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveAttribute("placeholder", "Ask about second.py");
});

it("shows a note-reply response when changing into its file topic initiates a competing state load", async () => {
  const reply = deferred<ReturnType<typeof state>>();
  transport.command.mockImplementation((action, payload) => action === "coworker.chat" ? reply.promise : Promise.resolve(state("Earlier history", payload)));
  const input = props();
  const quote: Quote = { kind: "source", category: "file", scope: input.scope, branch: input.scope, path: "second.py", line_start: 4, line_end: 5, text: "unsaved fix", unsaved: true };
  render(<CoworkerDrawer {...input} embedded={false} openFile="" pending={{ quote, message: "Does this fix it?", id: 1 }} />);
  await waitFor(() => expect(transport.command).toHaveBeenCalledWith("coworker.chat", expect.objectContaining({ path: "second.py", reference: quote }), input.repoRoot));
  await act(async () => reply.resolve(state("Your selected fix adds the guard", { category: "file", path: "second.py", scope: input.scope })));
  expect(screen.getByText("Your selected fix adds the guard")).toBeInTheDocument();
  expect(input.onPendingSent).toHaveBeenCalledOnce();
});
