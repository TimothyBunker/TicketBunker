import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { discardWindowConversationDrafts, useCoworkerDraftRecovery, windowConversationDrafts } from "./CoworkerDraftRecovery";
import { UnsavedChangesBoundary, useGuardedAction } from "./UnsavedChanges";

const identity = (ticket = "QA-1") => JSON.stringify(["scale", "/scale/repo", "QA", ticket]);
const route = (owner: string, index = 0) => JSON.stringify([owner, "main", "file", `src/file-${index}.py`]);
const key = (owner: string) => `tb.coworker.drafts:${owner}`;
function seeded(owner: string, size = 995000) {
  const entries = Object.fromEntries(Array.from({ length: 128 }, (_, index) => [route(owner, index), { text: index ? "x".repeat(7000) : "", quote: null }]));
  const extra = size - JSON.stringify(entries).length;
  expect(extra).toBeGreaterThan(0);
  entries[route(owner, 127)].text += "x".repeat(extra);
  const raw = JSON.stringify(entries);
  expect(raw).toHaveLength(size);
  sessionStorage.setItem(key(owner), raw);
  return raw;
}
function Draft({ owner = identity(), index = 0, label = "Draft", rendered }: { owner?: string; index?: number; label?: string; rendered?: () => void }) {
  const recovery = useCoworkerDraftRecovery(owner);
  const selected = route(owner, index);
  rendered?.();
  return <section aria-label={label}><textarea aria-label={label} value={recovery.entries[selected]?.text || ""} onChange={(event) => { const text = event.target.value; recovery.update(selected, (draft) => ({ ...draft, text })); }} />
    <button onClick={() => recovery.update(selected, () => ({ text: "", quote: null }))}>Clear draft</button>
    {recovery.error && <p role="alert">{recovery.error}</p>}</section>;
}
beforeEach(() => sessionStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("types 24 characters near the size limits without parsing unrelated ticket recovery or losing actions", () => {
  const owner = identity(); const other = identity("QA-2");
  seeded(owner); const otherRaw = seeded(other);
  const rendered = vi.fn();
  render(<Draft rendered={rendered} />);
  const parse = vi.spyOn(JSON, "parse"); const write = vi.spyOn(Storage.prototype, "setItem");
  const started = performance.now();
  for (let count = 1; count <= 24; count++) fireEvent.change(screen.getByRole("textbox", { name: "Draft" }), { target: { value: "q".repeat(count) } });
  const elapsed = performance.now() - started;
  const bodyParses = parse.mock.calls.filter(([raw]) => typeof raw === "string" && raw.startsWith("{")).length;
  const unrelatedParses = parse.mock.calls.filter(([raw]) => raw === otherRaw).length;
  expect(unrelatedParses).toBe(0);
  expect(write).toHaveBeenCalledTimes(24);
  expect(screen.getByRole("textbox", { name: "Draft" })).toHaveValue("q".repeat(24));
  expect(sessionStorage.getItem(key(other))).toBe(otherRaw);
  const saved = JSON.parse(sessionStorage.getItem(key(owner))!);
  expect(Object.keys(saved)).toHaveLength(128);
  expect(saved[route(owner)].text).toBe("q".repeat(24));
  console.info("draft-scale typing", { characters: 24, currentTicketPayloadParses: bodyParses, unrelatedParses, writes: write.mock.calls.length, renders: rendered.mock.calls.length, elapsedMs: Math.round(elapsed) });
  fireEvent.click(screen.getByRole("button", { name: "Clear draft" }));
  expect(screen.getByRole("textbox", { name: "Draft" })).toHaveValue("");
  expect(Object.keys(JSON.parse(sessionStorage.getItem(key(owner))!))).toHaveLength(127);
  expect(sessionStorage.getItem(key(other))).toBe(otherRaw);
});

it("keeps a 129th route live without evicting any of the 128 recovered routes", () => {
  const owner = identity(); const raw = seeded(owner);
  render(<Draft index={128} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Draft" }), { target: { value: "Overflow route still visible" } });
  expect(screen.getByRole("textbox", { name: "Draft" })).toHaveValue("Overflow route still visible");
  expect(screen.getByRole("alert")).toHaveTextContent("send or copy");
  expect(sessionStorage.getItem(key(owner))).toBe(raw);
});

it("keeps the latest input when crossing the two-million-character window limit", () => {
  const owner = identity(); const other = identity("QA-2");
  seeded(owner, 990000); const otherRaw = seeded(other, 990000);
  const third = identity("QA-3");
  sessionStorage.setItem(key(third), JSON.stringify({ [route(third)]: { text: "x".repeat(19000), quote: null } }));
  render(<Draft />);
  fireEvent.change(screen.getByRole("textbox", { name: "Draft" }), { target: { value: "q".repeat(1024) } });
  expect(screen.getByRole("textbox", { name: "Draft" })).toHaveValue("q".repeat(1024));
  expect(screen.getByRole("alert")).toHaveTextContent("send or copy");
  expect(sessionStorage.getItem(key(other))).toBe(otherRaw);
  expect(JSON.parse(sessionStorage.getItem(key(owner))!)[route(owner)].text).toBe("");
});

it("keeps the latest input when crossing the one-million-character ticket limit", () => {
  const owner = identity(); const raw = seeded(owner, 999990);
  render(<Draft />);
  fireEvent.change(screen.getByRole("textbox", { name: "Draft" }), { target: { value: "q".repeat(24) } });
  expect(screen.getByRole("textbox", { name: "Draft" })).toHaveValue("q".repeat(24));
  expect(screen.getByRole("alert")).toHaveTextContent("send or copy");
  expect(sessionStorage.getItem(key(owner))).toBe(raw);
});

it("maintains one active event subscription per mounted hook under Strict Mode", () => {
  const add = vi.spyOn(window, "addEventListener"); const remove = vi.spyOn(window, "removeEventListener");
  const rendered = render(<StrictMode><Draft label="First" /><Draft label="Second" /></StrictMode>);
  const count = (calls: unknown[][]) => calls.filter(([name]) => name === "tb-coworker-drafts-changed").length;
  expect(count(add.mock.calls) - count(remove.mock.calls)).toBe(2);
  const write = vi.spyOn(Storage.prototype, "setItem");
  fireEvent.change(screen.getByRole("textbox", { name: "First" }), { target: { value: "Shared live draft" } });
  expect(screen.getByRole("textbox", { name: "Second" })).toHaveValue("Shared live draft");
  expect(write).toHaveBeenCalledOnce();
  fireEvent.click(within(screen.getByRole("region", { name: "Second" })).getByRole("button", { name: "Clear draft" }));
  expect(screen.getByRole("textbox", { name: "First" })).toHaveValue("");
  rendered.unmount();
  expect(count(add.mock.calls)).toBe(count(remove.mock.calls));
  const read = vi.spyOn(Storage.prototype, "getItem");
  act(() => window.dispatchEvent(new CustomEvent("tb-coworker-drafts-changed", { detail: identity() })));
  expect(read).not.toHaveBeenCalled();
});

it("does not invoke unrelated mounted ticket recovery callbacks when another ticket changes", () => {
  seeded(identity()); seeded(identity("QA-2"));
  const first = vi.fn(); const second = vi.fn();
  render(<Draft rendered={first} label="First" />);
  render(<Draft owner={identity("QA-2")} rendered={second} label="Second" />);
  second.mockClear();
  fireEvent.change(screen.getByRole("textbox", { name: "First" }), { target: { value: "Changed only first ticket" } });
  expect(second).not.toHaveBeenCalled();
  expect(screen.getByRole("textbox", { name: "Second" })).toHaveValue("");
});

it("scans and discards valid window recovery while preserving malformed siblings", () => {
  const owner = identity(); seeded(owner); const other = identity("QA-2"); seeded(other);
  const malformedKey = key(identity("QA-broken")); const raw = "{broken recovery";
  sessionStorage.setItem(malformedKey, raw);
  expect(windowConversationDrafts()).toEqual([owner, other]);
  discardWindowConversationDrafts();
  expect(windowConversationDrafts()).toEqual([]);
  expect(sessionStorage.getItem(malformedKey)).toBe(raw);
});

it("keeps a refused window discard open if storage removal fails", async () => {
  seeded(identity());
  const close = vi.fn();
  function Exit() { const protect = useGuardedAction(); return <button onClick={() => protect(close, true)}>Close</button>; }
  render(<UnsavedChangesBoundary><Exit /></UnsavedChangesBoundary>);
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("Storage denied"); });
  fireEvent.click(screen.getByRole("button", { name: "Do not save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Storage denied");
  expect(close).not.toHaveBeenCalled();
  expect(sessionStorage.getItem(key(identity()))).not.toBeNull();
});
