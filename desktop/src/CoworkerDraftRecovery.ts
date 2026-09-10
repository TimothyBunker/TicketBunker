import { useLayoutEffect, useRef, useState } from "react";
import type { Quote } from "./TicketWorkspace";

type Draft = { text: string; quote: Quote | null };
type Entries = Record<string, Draft>;
const PREFIX = "tb.coworker.drafts:";
const EVENT = "tb-coworker-drafts-changed";
const guidance = "Conversation draft recovery is unavailable. Keep this window open; send or copy your message before closing.";
const empty = (): Draft => ({ text: "", quote: null });
export const conversationDraftSaveGuidance = "Conversation drafts are not sent by Save. Cancel to send or copy them, or choose Do not save to discard.";

function validQuote(value: unknown, scope: string, category: string, path: string): value is Quote | null {
  if (value === null) return true;
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const quote = value as Quote;
  return typeof quote.text === "string" && quote.text.length <= 50000 && typeof quote.branch === "string"
    && (quote.scope === "all" ? "combined" : quote.scope) === scope && quote.category === category
    && (category !== "file" || quote.path === path)
    && (quote.path === undefined || typeof quote.path === "string")
    && (quote.kind === undefined || ["source", "synthesis"].includes(quote.kind))
    && (quote.unsaved === undefined || typeof quote.unsaved === "boolean")
    && [quote.line_start, quote.line_end].every((line) => line === undefined || Number.isSafeInteger(line) && line >= 1);
}

function read(identity: string): Entries {
  const raw = sessionStorage.getItem(PREFIX + identity);
  if (!raw) return {};
  if (raw.length > 1000000) throw new Error("Draft recovery is too large.");
  const value = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length > 128) throw new Error("Unreadable draft recovery.");
  for (const [route, item] of Object.entries(value)) {
    const parts = JSON.parse(route);
    const draft = item as Draft;
    if (!Array.isArray(parts) || parts.length !== 4 || parts[0] !== identity || !parts.every((part) => typeof part === "string")
      || !draft || typeof draft.text !== "string" || draft.text.length > 100000 || !validQuote(draft.quote, parts[1], parts[2], parts[3])) throw new Error("Unreadable draft recovery.");
  }
  return value;
}

function write(identity: string, entries: Entries) {
  const key = PREFIX + identity;
  const raw = JSON.stringify(entries);
  if (Object.keys(entries).length > 128 || raw.length > 1000000
    || Object.values(entries).some((draft) => draft.text.length > 100000 || (draft.quote?.text.length || 0) > 50000)) throw new Error("Draft recovery is full.");
  let size = raw.length;
  for (let index = 0; index < sessionStorage.length; index++) {
    const existing = sessionStorage.key(index);
    if (existing?.startsWith(PREFIX) && existing !== key) size += sessionStorage.getItem(existing)?.length || 0;
  }
  if (size > 2000000) throw new Error("Draft recovery is full.");
  if (Object.keys(entries).length) sessionStorage.setItem(key, raw);
  else sessionStorage.removeItem(key);
}

// The window guard also owns recovery for drawers that are no longer mounted.
// Malformed copies are never rewritten or deleted by this scan.
export function windowConversationDrafts(): string[] {
  const identities: string[] = [];
  try {
    for (let index = 0; index < sessionStorage.length; index++) {
      const key = sessionStorage.key(index);
      if (!key?.startsWith(PREFIX)) continue;
      const identity = key.slice(PREFIX.length);
      try { if (Object.values(read(identity)).some((draft) => Boolean(draft.text || draft.quote))) identities.push(identity); } catch { /* Keep unreadable raw recovery. */ }
    }
  } catch { /* Mounted drafts retain their own failure guidance and guard. */ }
  return identities;
}

export function discardWindowConversationDrafts() {
  for (const identity of windowConversationDrafts()) {
    sessionStorage.removeItem(PREFIX + identity);
    window.dispatchEvent(new CustomEvent(EVENT, { detail: identity }));
  }
}

/** Local per-window recovery only. Saving a draft never sends a message. */
export function useCoworkerDraftRecovery(identity: string) {
  const [state, setState] = useState<{ identity: string; entries: Entries; error: string }>({ identity: "", entries: {}, error: "" });
  const current = useRef(state); current.current = state;
  const unpersisted = useRef(new Map<string, Draft | undefined>());
  const pendingFor = () => [...unpersisted.current].filter(([route]) => JSON.parse(route)[0] === identity);
  const overlayPending = (entries: Entries) => {
    const merged = { ...entries };
    for (const [route, draft] of pendingFor()) { if (draft) merged[route] = draft; else delete merged[route]; }
    return merged;
  };
  const refresh = () => {
    try { const next = { identity, entries: overlayPending(read(identity)), error: pendingFor().length ? guidance : "" }; current.current = next; setState(next); }
    catch { const next = { identity, entries: current.current.identity === identity ? current.current.entries : {}, error: guidance }; current.current = next; setState(next); }
  };
  useLayoutEffect(() => {
    refresh();
    const changed = (event: Event) => { if ((event as CustomEvent).detail === identity) refresh(); };
    window.addEventListener(EVENT, changed);
    return () => window.removeEventListener(EVENT, changed);
  }, [identity]);
  function update(route: string, change: (draft: Draft) => Draft) {
    const local = current.current.identity === identity ? current.current.entries : {};
    let entries = local;
    let error = "";
    // Only unsaved local edits override storage. An old full snapshot must
    // never resurrect a route another mounted view has sent or discarded.
    try { entries = overlayPending(read(identity)); } catch { error = guidance; }
    const draft = change(entries[route] || empty());
    entries = { ...entries, [route]: draft };
    if (!draft.text && !draft.quote) delete entries[route];
    if (!error) { try { write(identity, entries); } catch { error = guidance; } }
    if (error) unpersisted.current.set(route, entries[route]);
    else for (const [pendingRoute] of pendingFor()) unpersisted.current.delete(pendingRoute);
    if (current.current.identity === identity) { const next = { identity, entries, error }; current.current = next; setState(next); }
    if (!error) window.dispatchEvent(new CustomEvent(EVENT, { detail: identity }));
  }
  function discard() {
    let error = "";
    try { read(identity); sessionStorage.removeItem(PREFIX + identity); } catch { error = guidance; }
    for (const [route] of pendingFor()) unpersisted.current.delete(route);
    const next = { identity, entries: {}, error }; current.current = next; setState(next);
    if (!error) window.dispatchEvent(new CustomEvent(EVENT, { detail: identity }));
  }
  const entries = state.identity === identity ? state.entries : {};
  return { entries, error: state.identity === identity ? state.error : "", update, discard };
}
