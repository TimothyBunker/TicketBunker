import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CoworkerDrawer, type Quote } from "./TicketWorkspace";
import { UnsavedChangesBoundary, useGuardedAction } from "./UnsavedChanges";
import type { TicketDetail } from "./types";

const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
const identity = JSON.stringify(["lab", "/lab/repo", "QA", "QA-1"]);
const key = `tb.coworker.drafts:${identity}`;
const route = (scope = "dev/QA-1", path = "src/worker Ω.py") => JSON.stringify([identity, scope, "file", path]);
const sourceQuote: Quote = { kind: "source", category: "file", scope: "dev/QA-1", branch: "dev/QA-1", path: "src/worker Ω.py", line_start: 7, line_end: 9, text: "    return selected_value", unsaved: true };
const closed = vi.fn();
function Exit() {
  const protect = useGuardedAction();
  return <button onClick={() => protect(closed, true)}>Close application</button>;
}
function Drawer({ initialQuote = null, scope = "dev/QA-1" }: { initialQuote?: Quote | null; scope?: string }) {
  const [quote, setQuote] = useState(initialQuote);
  return <CoworkerDrawer open embedded onOpen={vi.fn()} detail={{ ticket: { id: "QA-1" }, project: { key: "QA" }, reports: [], repositories: [] } as unknown as TicketDetail}
    bunkerId="lab" repoRoot="/lab/repo" scope={scope} quote={quote} onQuote={setQuote} pending={null} onPendingSent={vi.fn()}
    openFile="src/worker Ω.py" onReload={vi.fn()} notify={vi.fn()} />;
}
const mount = (initialQuote: Quote | null = null) => render(<UnsavedChangesBoundary><Exit /><Drawer initialQuote={initialQuote} /></UnsavedChangesBoundary>);
const message = () => screen.getByRole("textbox", { name: "Message to ticket coworker" });
const noSend = () => expect(transport.command.mock.calls.some(([action]) => action === "coworker.chat")).toBe(false);
beforeEach(() => {
  sessionStorage.clear(); closed.mockClear(); transport.command.mockReset();
  transport.command.mockImplementation(async () => ({ messages: [], proposals: [] }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("restores exact draft text and unsaved source quote after remount without sending anything", async () => {
  const rendered = mount(sourceQuote);
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "Why does this selected path matter?" } });
  rendered.unmount();
  mount();
  expect(await screen.findByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("Why does this selected path matter?");
  expect(screen.getByText(/return selected_value/)).toBeInTheDocument();
  noSend();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(transport.command).toHaveBeenCalledWith("coworker.chat", expect.objectContaining({ message: "Why does this selected path matter?", path: sourceQuote.path, reference: sourceQuote }), "/lab/repo"));
  await waitFor(() => expect(message()).toHaveValue(""));
  expect(screen.queryByRole("button", { name: "Remove quoted context" })).not.toBeInTheDocument();
  expect(sessionStorage.getItem(key)).toBeNull();
});

it("does not send from the close guard's Save and requires explicit discard", async () => {
  mount(sourceQuote);
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "Unsent question" } });
  fireEvent.click(screen.getByRole("button", { name: "Close application" }));
  const dialog = within(screen.getByRole("dialog"));
  fireEvent.click(dialog.getByRole("button", { name: "Save" }));
  expect(await dialog.findByRole("alert")).toHaveTextContent("Conversation drafts are not sent by Save");
  noSend(); expect(closed).not.toHaveBeenCalled();
  fireEvent.click(dialog.getByRole("button", { name: "Do not save" }));
  await waitFor(() => expect(closed).toHaveBeenCalledOnce());
  expect(sessionStorage.getItem(key)).toBeNull();
  expect(message()).toHaveValue("");
});

it("guards and explicitly discards recovery even after its drawer has unmounted", async () => {
  const app = (open: boolean) => <UnsavedChangesBoundary><Exit />{open && <Drawer />}</UnsavedChangesBoundary>;
  const rendered = render(app(true));
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "Hidden conversation draft" } });
  rendered.rerender(app(false));
  fireEvent.click(screen.getByRole("button", { name: "Close application" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("not sent by Save");
  noSend(); expect(closed).not.toHaveBeenCalled();
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Do not save" }));
  await waitFor(() => expect(closed).toHaveBeenCalledOnce());
  expect(sessionStorage.getItem(key)).toBeNull();
});

it.each([
  ["broken JSON", "{not json"],
  ["wrong text shape", JSON.stringify({ [route()]: { text: { invalid: true }, quote: null } })],
  ["quote from another branch", JSON.stringify({ [route()]: { text: "recovered", quote: { ...sourceQuote, scope: "other" } } })],
  ["quote from another file", JSON.stringify({ [route()]: { text: "recovered", quote: { ...sourceQuote, path: "another.py" } } })],
])("keeps malformed raw recovery untouched and provides copy guidance: %s", async (_label, raw) => {
  sessionStorage.setItem(key, raw);
  mount();
  expect(await screen.findByRole("alert")).toHaveTextContent("send or copy");
  expect(message()).toHaveValue("");
  fireEvent.change(message(), { target: { value: "Keep this new live draft" } });
  expect(message()).toHaveValue("Keep this new live draft");
  expect(sessionStorage.getItem(key)).toBe(raw);
  noSend();
});

it("keeps the latest live text and warns on close when storage quota is exhausted", async () => {
  sessionStorage.setItem(key, JSON.stringify({ [route()]: { text: "previously recovered", quote: null } }));
  const raw = sessionStorage.getItem(key);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Full", "QuotaExceededError"); });
  mount();
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "Latest live text, not saved to storage" } });
  expect(message()).toHaveValue("Latest live text, not saved to storage");
  expect(screen.getByRole("alert")).toHaveTextContent("Keep this window open");
  expect(sessionStorage.getItem(key)).toBe(raw);
  const event = new Event("beforeunload", { cancelable: true }); window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
  noSend();
});

it("bounds stored text without truncating the live draft or evicting older recovery", async () => {
  const raw = JSON.stringify({ [route("other-branch")]: { text: "Keep the older branch draft", quote: null } });
  sessionStorage.setItem(key, raw);
  mount();
  const large = "x".repeat(100001);
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: large } });
  expect(message()).toHaveValue(large);
  expect(screen.getByRole("alert")).toHaveTextContent("send or copy");
  expect(sessionStorage.getItem(key)).toBe(raw);
});

it("keeps text and quotes separate across branch routes", async () => {
  sessionStorage.setItem(key, JSON.stringify({ [route()]: { text: "First branch", quote: sourceQuote }, [route("other-branch")]: { text: "Second branch", quote: null } }));
  const rendered = render(<Drawer />);
  expect(await screen.findByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("First branch");
  rendered.rerender(<Drawer scope="other-branch" />);
  expect(message()).toHaveValue("Second branch");
  expect(screen.queryByRole("button", { name: "Remove quoted context" })).not.toBeInTheDocument();
  rendered.rerender(<Drawer />);
  expect(message()).toHaveValue("First branch");
  expect(screen.getByText(/return selected_value/)).toBeInTheDocument();
  noSend();
});

it("does not resurrect a sent draft from a second mounted view of the same ticket", async () => {
  render(<UnsavedChangesBoundary><section aria-label="First view"><Drawer /></section><section aria-label="Second view"><Drawer /></section></UnsavedChangesBoundary>);
  const first = within(screen.getByRole("region", { name: "First view" }));
  const second = within(screen.getByRole("region", { name: "Second view" }));
  fireEvent.change(await first.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "One shared draft" } });
  expect(second.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("One shared draft");
  fireEvent.click(first.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(second.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue(""));
  fireEvent.change(second.getByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "A new draft" } });
  expect(first.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("A new draft");
  expect(sessionStorage.getItem(key)).not.toContain("One shared draft");
});

it("retains quoted context when new draft text arrives during an earlier send", async () => {
  let resolve!: (value: unknown) => void;
  const reply = new Promise((done) => { resolve = done; });
  transport.command.mockImplementation(async (action) => action === "coworker.chat" ? reply : { messages: [], proposals: [] });
  mount(sourceQuote);
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "First question" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  fireEvent.change(message(), { target: { value: "Follow-up on the same selected code" } });
  await act(async () => resolve({ messages: [], proposals: [] }));
  expect(message()).toHaveValue("Follow-up on the same selected code");
  expect(screen.getByText(/return selected_value/)).toBeInTheDocument();
  expect(JSON.parse(sessionStorage.getItem(key)!)[route()].quote).toEqual(sourceQuote);
});

it("preserves malformed raw recovery when explicitly discarding a new live draft", async () => {
  const raw = "{unreadable original recovery";
  sessionStorage.setItem(key, raw);
  mount();
  fireEvent.change(await screen.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "Discard only this live text" } });
  fireEvent.click(screen.getByRole("button", { name: "Close application" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Do not save" }));
  await waitFor(() => expect(closed).toHaveBeenCalledOnce());
  expect(sessionStorage.getItem(key)).toBe(raw);
});

it("does not resurrect another view's sent route when retrying after a quota failure", async () => {
  sessionStorage.setItem(key, JSON.stringify({ [route("other-branch")]: { text: "Other branch message", quote: null } }));
  render(<UnsavedChangesBoundary><section aria-label="First view"><Drawer /></section><section aria-label="Second view"><Drawer scope="other-branch" /></section></UnsavedChangesBoundary>);
  const first = within(screen.getByRole("region", { name: "First view" }));
  const second = within(screen.getByRole("region", { name: "Second view" }));
  const write = vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => { throw new DOMException("Full", "QuotaExceededError"); });
  fireEvent.change(await first.findByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "Keep this failed-storage draft" } });
  expect(first.getByRole("alert")).toHaveTextContent("recovery is unavailable");
  fireEvent.click(second.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(second.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue(""));
  fireEvent.change(first.getByRole("textbox", { name: "Message to ticket coworker" }), { target: { value: "Retry first draft storage" } });
  expect(JSON.parse(sessionStorage.getItem(key)!)[route("other-branch")]).toBeUndefined();
  expect(second.getByRole("textbox", { name: "Message to ticket coworker" })).toHaveValue("");
  write.mockRestore();
});
