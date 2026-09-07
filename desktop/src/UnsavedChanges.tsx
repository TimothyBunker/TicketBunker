import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

type Editor = { dirty: boolean; busy: boolean; save: () => Promise<boolean | string>; discard: () => void; description?: string };
type Guard = { register: (editor: Editor) => () => void; protect: (action: () => void) => void };
const Context = createContext<Guard | null>(null);

export function UnsavedChangesBoundary({ children }: { children: ReactNode }) {
  const inherited = useContext(Context);
  return inherited ? <>{children}</> : <Provider>{children}</Provider>;
}

function Provider({ children }: { children: ReactNode }) {
  const editor = useRef<Editor | null>(null);
  const action = useRef<(() => void) | null>(null);
  const [pending, setPending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [closeError, setCloseError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const register = useCallback((value: Editor) => {
    editor.current = value;
    return () => { if (editor.current === value) editor.current = null; };
  }, []);
  const protect = useCallback((next: () => void) => {
    if (action.current || editor.current?.busy) return;
    if (!editor.current?.dirty) return next();
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
      if (!editor.current?.dirty && !editor.current?.busy && !action.current) return;
      event.preventDefault();
      protect(() => { void native.destroy().catch(() => setCloseError("The window could not close. Your work is still open; try closing again.")); });
    }).then((unlisten) => { if (disposed) unlisten(); else stop = unlisten; })
      .catch(() => setCloseError("Close protection is unavailable. Save your file before closing this window."));
    return () => { disposed = true; stop?.(); };
  }, [protect]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (editor.current?.dirty || editor.current?.busy) { event.preventDefault(); event.returnValue = ""; }
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
        <button disabled={saving} onClick={() => { editor.current?.discard(); proceed(); }}>Do not save</button>
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
  useLayoutEffect(() => register?.(value), [register, value.dirty, value.busy, value.save, value.discard, value.description]);
}
