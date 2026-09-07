import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UnsavedChangesBoundary, useGuardedAction, useUnsavedEditor } from "./UnsavedChanges";

function Editor({ save, leave }: { save: () => Promise<boolean>; leave: () => void }) {
  const [draft, setDraft] = useState("");
  const protect = useGuardedAction();
  useUnsavedEditor({ dirty: !!draft, busy: false, save, discard: () => setDraft("") });
  return <><input aria-label="Draft" value={draft} onChange={(e) => setDraft(e.target.value)} /><button onClick={() => protect(leave)}>Leave</button></>;
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
  it("discards only after explicit confirmation", () => {
    const { save, leave } = setup();
    fireEvent.click(screen.getByText("Do not save"));
    expect(leave).toHaveBeenCalledOnce(); expect(save).not.toHaveBeenCalled();
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
});
