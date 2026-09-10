import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RepositoryPathInput from "./RepositoryPathInput";

const transport = vi.hoisted(() => ({ command: vi.fn(), chooseGitDirectory: vi.fn() }));
vi.mock("./transport", () => transport);
const root = { path: "/simulation", parent: "/", locations: ["/simulation", "/"], directories: [{ name: "Navigation", path: "/simulation/Navigation" }], truncated: false };
function mount(initial = "", disabled = false) {
  const changed = vi.fn();
  function Harness() {
    const [value, setValue] = useState(initial);
    return <RepositoryPathInput value={value} onChange={(path) => { setValue(path); changed(path); }} disabled={disabled} />;
  }
  render(<Harness />);
  return changed;
}
beforeEach(() => {
  delete window.__TAURI__;
  transport.command.mockReset().mockResolvedValue(root);
  transport.chooseGitDirectory.mockReset().mockResolvedValue("/simulation/Navigation");
});
afterEach(() => { cleanup(); delete window.__TAURI__; });

describe("Repository folder selection", () => {
  it("always supports manual entry without starting a picker or attaching a repository", () => {
    const changed = mount();
    fireEvent.change(screen.getByLabelText("Local repository path"), { target: { value: "/simulation/manual" } });
    expect(changed).toHaveBeenLastCalledWith("/simulation/manual");
    expect(transport.command).not.toHaveBeenCalled();
    expect(transport.chooseGitDirectory).not.toHaveBeenCalled();
  });

  it("uses the native picker only on Browse and preserves the path on cancel", async () => {
    window.__TAURI__ = { core: { invoke: vi.fn() }, dialog: { open: vi.fn() } };
    const changed = mount("/simulation");
    expect(transport.chooseGitDirectory).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Browse folders" }));
    await waitFor(() => expect(changed).toHaveBeenCalledWith("/simulation/Navigation"));
    expect(transport.chooseGitDirectory).toHaveBeenCalledWith("/simulation");
    transport.chooseGitDirectory.mockResolvedValueOnce(null);
    fireEvent.click(screen.getByRole("button", { name: "Browse folders" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Browse folders" })).toBeEnabled());
    expect(screen.getByLabelText("Local repository path")).toHaveValue("/simulation/Navigation");
    expect(changed).toHaveBeenCalledTimes(1);
    expect(transport.command).not.toHaveBeenCalled();
  });

  it("browses folders inline in a browser and commits only the explicitly chosen folder", async () => {
    const changed = mount("/simulation");
    fireEvent.click(screen.getByRole("button", { name: "Browse folders" }));
    const picker = await screen.findByRole("region", { name: "Choose repository folder" });
    expect(transport.command).toHaveBeenCalledWith("application.directory.list", { path: "/simulation" });
    expect(changed).not.toHaveBeenCalled();
    transport.command.mockResolvedValueOnce({ ...root, path: "/simulation/Navigation", parent: "/simulation", directories: [] });
    fireEvent.click(within(picker).getByRole("button", { name: "Navigation" }));
    await screen.findByText("No visible subfolders.");
    fireEvent.click(screen.getByRole("button", { name: "Parent folder" }));
    await screen.findByRole("button", { name: "Navigation" });
    fireEvent.change(screen.getByRole("combobox", { name: "Folder location" }), { target: { value: "/" } });
    await waitFor(() => expect(transport.command).toHaveBeenLastCalledWith("application.directory.list", { path: "/" }));
    fireEvent.click(screen.getByRole("button", { name: "Use this folder" }));
    expect(changed).toHaveBeenLastCalledWith("/simulation");
    expect(screen.queryByRole("region", { name: "Choose repository folder" })).not.toBeInTheDocument();
    expect(transport.command.mock.calls.every(([action]) => action === "application.directory.list")).toBe(true);
  });

  it("does not reopen a cancelled browser listing when its pending request finishes", async () => {
    const changed = mount("/simulation");
    fireEvent.click(screen.getByRole("button", { name: "Browse folders" }));
    await screen.findByRole("region", { name: "Choose repository folder" });
    let finish!: (value: typeof root) => void;
    transport.command.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Navigation" }));
    expect(screen.getByRole("button", { name: "Use this folder" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => finish({ ...root, path: "/simulation/Navigation" }));
    expect(screen.queryByRole("region", { name: "Choose repository folder" })).not.toBeInTheDocument();
    expect(changed).not.toHaveBeenCalled();
  });

  it("offers inline browsing when the native picker cannot open", async () => {
    window.__TAURI__ = { core: { invoke: vi.fn() }, dialog: { open: vi.fn() } };
    transport.chooseGitDirectory.mockRejectedValueOnce(new Error("native unavailable"));
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Browse folders" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the path or use Browse here");
    fireEvent.click(screen.getByRole("button", { name: "Browse here" }));
    await screen.findByRole("region", { name: "Choose repository folder" });
    expect(transport.command).toHaveBeenCalledWith("application.directory.list", { path: "" });
  });

  it("retains manual input on folder errors and explains omitted large-folder entries", async () => {
    transport.command.mockRejectedValueOnce(new Error("This folder cannot be opened. Choose another folder."));
    mount("/simulation/missing");
    fireEvent.click(screen.getByRole("button", { name: "Browse folders" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose another folder");
    expect(screen.getByLabelText("Local repository path")).toHaveValue("/simulation/missing");
    transport.command.mockResolvedValueOnce({ ...root, truncated: true, path: "/", parent: "/" });
    fireEvent.click(screen.getByRole("button", { name: "Browse here" }));
    await screen.findByText(/some entries are omitted/);
    expect(screen.getByRole("button", { name: "Parent folder" })).toBeDisabled();
  });

  it("disables path entry and browsing during the owning operation", () => {
    mount("/simulation", true);
    expect(screen.getByLabelText("Local repository path")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Browse folders" })).toBeDisabled();
  });

  it("locks an already-open browser listing when repository attachment starts", async () => {
    const changed = vi.fn();
    const view = render(<RepositoryPathInput value="/simulation" onChange={changed} />);
    fireEvent.click(screen.getByRole("button", { name: "Browse folders" }));
    const picker = await screen.findByRole("region", { name: "Choose repository folder" });
    view.rerender(<RepositoryPathInput value="/simulation" onChange={changed} disabled />);
    for (const button of within(picker).getAllByRole("button")) expect(button).toBeDisabled();
    expect(within(picker).getByRole("combobox", { name: "Folder location" })).toBeDisabled();
    fireEvent.click(within(picker).getByRole("button", { name: "Use this folder" }));
    fireEvent.click(within(picker).getByRole("button", { name: "Navigation" }));
    expect(changed).not.toHaveBeenCalled();
    expect(transport.command).toHaveBeenCalledTimes(1);
    view.rerender(<RepositoryPathInput value="/simulation" onChange={changed} />);
    expect(within(picker).getByRole("button", { name: "Use this folder" })).toBeEnabled();
  });
});
