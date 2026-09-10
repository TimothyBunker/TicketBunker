import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

type Editor = { dirty: boolean; busy: boolean; save: () => Promise<boolean | string>; discard: () => void | Promise<void>; description?: string; retained?: boolean };
type Guard = { register: (editor: Editor) => () => void; protect: (action: () => void, closing?: boolean) => void };
const Context = createContext<Guard | null>(null);

export function UnsavedChangesBoundary({ children }: { children: ReactNode }) {
  const inherited = useContext(Context);
  return inherited ? <>{children}</> : <Provider>{children}</Provider>;
}

function Provider({ children }: { children: ReactNode }) {
  const editor = useRef<Editor | null>(null);
  const editors = useRef(new Set<Editor>());
  const action = useRef<(() => void) | null>(null);
  const [pending, setPending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [closeError, setCloseError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const register = useCallback((value: Editor) => {
    editors.current.add(value);
    return () => { editors.current.delete(value); };
  }, []);
  const protect = useCallback((next: () => void, closing = false) => {
    const targets = [...editors.current].filter((value) => closing || !value.retained);
    if (action.current || targets.some((value) => value.busy)) return;
    const dirty = targets.filter((value) => value.dirty);
    if (!dirty.length) return next();
    editor.current = { dirty: true, busy: false, description: dirty.length === 1 ? dirty[0].description : "Save changes in your open workspaces before closing?",
      discard: () => { const results = dirty.map((value) => value.discard()); if (results.some(Boolean)) return Promise.all(results).then(() => {}); },
      save: async () => { for (const value of dirty) { const result = await value.save(); if (result !== true) return result; } return true; } };
    action.current = next;
    setError(""); setPending(true);
  }, []);
  const cancel = () => { action.current = null; setPending(false); setError(""); };
  const proceed = () => { const next = action.current; cancel(); next?.(); };
  async function save() {
    if (saving) return;
    setSaving(true); setError("");
    try {
      const result = await editor.current?.save();
      if (result === true) proceed();
      else setError(`${typeof result === "string" ? result : "Could not save."} Your draft is still open. Cancel to return to your changes, or retry Save.`);
    } catch (reason) { setError(String(reason)); }
    finally { setSaving(false); }
  }
  useEffect(() => {
    const native = window.__TAURI__?.window?.getCurrentWindow();
    if (!native) return;
    let disposed = false;
    let stop: (() => void) | undefined;
    native.onCloseRequested((event) => {
      if (![...editors.current].some((value) => value.dirty || value.busy) && !action.current) return;
      event.preventDefault();
      protect(() => { void native.destroy().catch(() => setCloseError("The window could not close. Your work is still open; try closing again.")); }, true);
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
        <button disabled={saving} onClick={() => { try { const result = editor.current?.discard(); if (result) { setSaving(true); void result.then(proceed).catch((reason) => setError(String(reason))).finally(() => setSaving(false)); } else proceed(); } catch (reason) { setError(String(reason)); } }}>Do not save</button>
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
