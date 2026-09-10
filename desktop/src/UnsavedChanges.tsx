import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { conversationDraftSaveGuidance, discardWindowConversationDrafts, windowConversationDrafts } from "./CoworkerDraftRecovery";

type Editor = { dirty: boolean; busy: boolean; save: () => Promise<boolean | string>; discard: () => void | Promise<void>; description?: string; retained?: boolean };
type Action = () => void | Promise<void>;
type Guard = { register: (editor: Editor) => () => void; protect: (action: Action, closing?: boolean) => void };
const Context = createContext<Guard | null>(null);

export function UnsavedChangesBoundary({ children }: { children: ReactNode }) {
  const inherited = useContext(Context);
  return inherited ? <>{children}</> : <Provider>{children}</Provider>;
}

function Provider({ children }: { children: ReactNode }) {
  const editor = useRef<Editor | null>(null);
  const editors = useRef(new Set<Editor>());
  const action = useRef<Action | null>(null);
  const closingScope = useRef(false);
  const operation = useRef(false);
  const [pending, setPending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [verification, setVerification] = useState<"save" | "discard" | null>(null);
  const [error, setError] = useState("");
  const [closeError, setCloseError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const register = useCallback((value: Editor) => {
    editors.current.add(value);
    return () => { editors.current.delete(value); };
  }, []);
  useEffect(() => register({
    get dirty() { return windowConversationDrafts().length > 0; }, busy: false, retained: true,
    description: "This window has unsent conversation drafts, including hidden ticket sessions.",
    save: async () => conversationDraftSaveGuidance,
    discard: discardWindowConversationDrafts,
  }), [register]);
  const protect = useCallback((next: Action, closing = false) => {
    const targets = [...editors.current].filter((value) => closing || !value.retained);
    if (action.current || targets.some((value) => value.busy)) return;
    const dirty = targets.filter((value) => value.dirty);
    if (!dirty.length) return next();
    editor.current = { dirty: true, busy: false, description: dirty.length === 1 ? dirty[0].description : "Save changes in your open workspaces before closing?",
      discard: () => { const results = [...editors.current].filter((value) => (closing || !value.retained) && value.dirty).map((value) => value.discard()); if (results.some(Boolean)) return Promise.all(results).then(() => {}); },
      save: async () => { for (const value of [...editors.current].filter((value) => (closing || !value.retained) && value.dirty)) { const result = await value.save(); if (result !== true) return result; } return true; } };
    action.current = next;
    closingScope.current = closing;
    setError(""); setPending(true);
  }, []);
  const cancel = () => { action.current = null; setPending(false); setError(""); setVerification(null); };
  async function proceed() {
    try { await action.current?.(); cancel(); }
    catch (reason) { setError(`Could not leave this view. ${String(reason)}`); }
    finally { operation.current = false; setSaving(false); }
  }
  useEffect(() => {
    if (!verification || !action.current) return;
    setVerification(null);
    // Check after React has committed the save/discard state, including any
    // editors registered or changed while that operation was in flight.
    if ([...editors.current].some((value) => (closingScope.current || !value.retained) && (value.dirty || value.busy))) {
      setError("New changes are still unsaved. Cancel to review them, or choose Save again.");
      operation.current = false; setSaving(false);
      return;
    }
    void proceed();
  }, [verification]);
  async function save() {
    if (operation.current) return;
    operation.current = true;
    setSaving(true); setError("");
    try {
      const result = await editor.current?.save();
      if (result === true) { setVerification("save"); return; }
      setError(`${typeof result === "string" ? result : "Could not save."} Your draft is still open. Cancel to return to your changes, or retry Save.`);
    } catch (reason) { setError(String(reason)); }
    operation.current = false; setSaving(false);
  }
  async function discard() {
    if (operation.current) return;
    operation.current = true; setSaving(true); setError("");
    try { const result = editor.current?.discard(); if (result) await result; setVerification("discard"); }
    catch (reason) { setError(String(reason)); operation.current = false; setSaving(false); }
  }
  useEffect(() => {
    const native = window.__TAURI__?.window?.getCurrentWindow();
    if (!native) return;
    let disposed = false;
    let stop: (() => void) | undefined;
    native.onCloseRequested((event) => {
      if (![...editors.current].some((value) => value.dirty || value.busy) && !action.current) return;
      event.preventDefault();
      protect(() => native.destroy().catch(() => setCloseError("The window could not close. Your work is still open; try closing again.")), true);
    }).then((unlisten) => { if (disposed) unlisten(); else stop = unlisten; })
      .catch(() => setCloseError("Close protection is unavailable. Save your file before closing this window."));
    return () => { disposed = true; stop?.(); };
  }, [protect]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if ([...editors.current].some((value) => value.dirty || value.busy)) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
  useEffect(() => {
    if (!pending) return;
    const focused = document.activeElement as HTMLElement | null;
    if (dialog.current?.showModal) dialog.current.showModal();
    else dialog.current?.setAttribute("open", "");
    return () => focused?.focus?.();
  }, [pending]);
  return <Context.Provider value={{ register, protect }}>
    {children}
    {closeError && <div role="alert" className="toast error">{closeError}<button onClick={() => setCloseError("")}>Dismiss</button></div>}
    {pending && <dialog ref={dialog} className="unsaved-dialog" aria-labelledby="unsaved-title" onCancel={(event) => { event.preventDefault(); if (!saving) cancel(); }}>
      <h2 id="unsaved-title">You have unsaved changes</h2>
      <p>{editor.current?.description || "Save your changes before leaving?"}</p>
      {error && <p role="alert">{error}</p>}
      <div className="dialog-actions">
        <button disabled={saving} onClick={cancel} autoFocus>Cancel</button>
        <button disabled={saving} onClick={discard}>Do not save</button>
        <button className="primary" disabled={saving} onClick={save}>{saving ? "Saving…" : "Save"}</button>
      </div>
    </dialog>}
  </Context.Provider>;
}

export function useGuardedAction() {
  const context = useContext(Context);
  if (!context) throw new Error("Missing unsaved-changes boundary");
  return context.protect;
}

export function useUnsavedEditor(value: Editor) {
  const context = useContext(Context);
  const register = context?.register;
  const latest = useRef(value);
  latest.current = value;
  useLayoutEffect(() => register?.({
    get dirty() { return latest.current.dirty; },
    get busy() { return latest.current.busy; },
    get retained() { return latest.current.retained; },
    get description() { return latest.current.description; },
    save: () => latest.current.save(), discard: () => latest.current.discard(),
  }), [register]);
}
