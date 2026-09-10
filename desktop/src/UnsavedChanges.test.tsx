import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UnsavedChangesBoundary, useGuardedAction, useUnsavedEditor } from "./UnsavedChanges";

function Editor({ save, leave, name = "Draft", retained = false }: { save: () => Promise<boolean>; leave: () => void; name?: string; retained?: boolean }) {
  const [draft, setDraft] = useState("");
  const [saved, setSaved] = useState("");
  const protect = useGuardedAction();
  useUnsavedEditor({ dirty: draft !== saved, busy: false, retained, save: async () => { const result = await save(); if (result) setSaved(draft); return result; }, discard: () => setDraft(saved) });
  return <><input aria-label={name} value={draft} onChange={(e) => setDraft(e.target.value)} /><button onClick={() => protect(leave)}>Leave</button></>;
}
function setup(save = vi.fn().mockResolvedValue(true)) {
  const leave = vi.fn();
  render(<UnsavedChangesBoundary><Editor save={save} leave={leave} /></UnsavedChangesBoundary>);
  fireEvent.change(screen.getByLabelText("Draft"), { target: { value: "Keep my work" } });
  fireEvent.click(screen.getByText("Leave"));
  return { save, leave };
}
afterEach(() => { cleanup(); delete window.__TAURI__; });
describe("Unsaved changes", () => {
  it("cancels navigation without losing the draft", () => {
    const { save, leave } = setup();
    expect(screen.getByRole("dialog")).toHaveAccessibleName("You have unsaved changes");
    fireEvent.click(screen.getByText("Cancel"));
    expect(screen.getByLabelText("Draft")).toHaveValue("Keep my work");
    expect(leave).not.toHaveBeenCalled(); expect(save).not.toHaveBeenCalled();
  });
  it("discards only after explicit confirmation", async () => {
    const { save, leave } = setup();
    fireEvent.click(screen.getByText("Do not save"));
    await waitFor(() => expect(leave).toHaveBeenCalledOnce()); expect(save).not.toHaveBeenCalled();
  });
  it("waits for a successful save before navigating", async () => {
    let resolve!: (value: boolean) => void;
    const { leave } = setup(vi.fn(() => new Promise<boolean>((done) => { resolve = done; })));
    fireEvent.click(screen.getByText("Save"));
    expect(leave).not.toHaveBeenCalled();
    expect(screen.getByText("Do not save")).toBeDisabled();
    resolve(true);
    await waitFor(() => expect(leave).toHaveBeenCalledOnce());
  });
  it("retains the draft and dialog after a failed save", async () => {
    const { leave } = setup(vi.fn().mockResolvedValue(false));
    fireEvent.click(screen.getByText("Save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your draft is still open");
    expect(leave).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Draft")).toHaveValue("Keep my work");
  });
  it("guards browser reload", () => {
    setup();
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });
  it("guards the native close request and closes only after discard", async () => {
    let close!: (event: { preventDefault(): void }) => void;
    const destroy = vi.fn().mockResolvedValue(undefined);
    window.__TAURI__ = { core: { invoke: vi.fn() }, window: { getCurrentWindow: () => ({
      destroy, onCloseRequested: vi.fn((callback) => { close = callback; return Promise.resolve(vi.fn()); }),
    }) } };
    setup();
    fireEvent.click(screen.getByText("Cancel"));
    const preventDefault = vi.fn();
    await waitFor(() => expect(close).toBeDefined());
    fireEvent(window, new Event("focus"));
    const { act } = await import("@testing-library/react");
    act(() => close({ preventDefault }));
    expect(preventDefault).toHaveBeenCalledOnce(); expect(destroy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Do not save"));
    await waitFor(() => expect(destroy).toHaveBeenCalledOnce());
  });

  it("includes editors dirtied after the confirmation opened in the save operation", async () => {
    const first = vi.fn().mockResolvedValue(true), second = vi.fn().mockResolvedValue(true), leave = vi.fn();
    render(<UnsavedChangesBoundary><Editor save={first} leave={leave} name="First draft" /><Editor save={second} leave={leave} name="Second draft" /></UnsavedChangesBoundary>);
    fireEvent.change(screen.getByLabelText("First draft"), { target: { value: "First change" } });
    fireEvent.click(screen.getAllByText("Leave")[0]);
    fireEvent.change(screen.getByLabelText("Second draft"), { target: { value: "Arrived after confirmation" } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(leave).toHaveBeenCalledOnce());
    expect(first).toHaveBeenCalledOnce(); expect(second).toHaveBeenCalledOnce();
  });

  it("does not run concurrent saves after repeated confirmation clicks", async () => {
    let resolve!: (value: boolean) => void;
    const save = vi.fn(() => new Promise<boolean>((done) => { resolve = done; }));
    const { leave } = setup(save);
    const button = screen.getByText("Save");
    fireEvent.click(button); fireEvent.click(button);
    expect(save).toHaveBeenCalledOnce();
    await act(async () => resolve(true));
    expect(leave).toHaveBeenCalledOnce();
  });

  it("keeps retained workspace drafts while allowing ordinary navigation", () => {
    const leave = vi.fn(), save = vi.fn().mockResolvedValue(true);
    render(<UnsavedChangesBoundary><Editor save={save} leave={leave} retained /></UnsavedChangesBoundary>);
    fireEvent.change(screen.getByLabelText("Draft"), { target: { value: "Retained source" } });
    fireEvent.click(screen.getByText("Leave"));
    expect(leave).toHaveBeenCalledOnce(); expect(save).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Draft")).toHaveValue("Retained source");
  });

  it("keeps the native dialog locked while destroy is pending and prevents a second close", async () => {
    let close!: (event: { preventDefault(): void }) => void, resolve!: () => void;
    const destroy = vi.fn(() => new Promise<void>((done) => { resolve = done; }));
    window.__TAURI__ = { core: { invoke: vi.fn() }, window: { getCurrentWindow: () => ({ destroy,
      onCloseRequested: (callback) => { close = callback; return Promise.resolve(vi.fn()); },
    }) } };
    setup(); fireEvent.click(screen.getByText("Cancel"));
    act(() => close({ preventDefault: vi.fn() }));
    fireEvent.click(screen.getByText("Do not save"));
    await waitFor(() => expect(destroy).toHaveBeenCalledOnce());
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    const preventDefault = vi.fn(); act(() => close({ preventDefault }));
    expect(preventDefault).toHaveBeenCalledOnce(); expect(destroy).toHaveBeenCalledOnce();
    await act(async () => resolve());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("reports rejected native destroy without claiming that the window closed", async () => {
    let close!: (event: { preventDefault(): void }) => void;
    window.__TAURI__ = { core: { invoke: vi.fn() }, window: { getCurrentWindow: () => ({
      destroy: vi.fn().mockRejectedValue(new Error("native bridge offline")),
      onCloseRequested: (callback) => { close = callback; return Promise.resolve(vi.fn()); },
    }) } };
    setup(); fireEvent.click(screen.getByText("Cancel")); act(() => close({ preventDefault: vi.fn() }));
    fireEvent.click(screen.getByText("Do not save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The window could not close");
    expect(screen.getByLabelText("Draft")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("warns when native close registration fails and retains browser unload protection", async () => {
    window.__TAURI__ = { core: { invoke: vi.fn() }, window: { getCurrentWindow: () => ({
      destroy: vi.fn().mockResolvedValue(undefined), onCloseRequested: vi.fn().mockRejectedValue(new Error("bridge unavailable")),
    }) } };
    setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("Close protection is unavailable");
    const event = new Event("beforeunload", { cancelable: true }); fireEvent(window, event);
    expect(event.defaultPrevented).toBe(true);
  });

  it("unregisters a late native listener after the boundary unmounts", async () => {
    let resolve!: (unlisten: () => void) => void;
    const unlisten = vi.fn();
    window.__TAURI__ = { core: { invoke: vi.fn() }, window: { getCurrentWindow: () => ({
      destroy: vi.fn().mockResolvedValue(undefined), onCloseRequested: () => new Promise((done) => { resolve = done; }),
    }) } };
    const rendered = render(<UnsavedChangesBoundary><span>Window</span></UnsavedChangesBoundary>);
    rendered.unmount(); await act(async () => resolve(unlisten));
    expect(unlisten).toHaveBeenCalledOnce();
  });
});
