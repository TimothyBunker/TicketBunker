import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { bootstrap, command, OFFLINE_MESSAGE, STALE_SERVICE_MESSAGE } from "./transport";

describe("browser transport", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    delete (window as { __TAURI__?: unknown }).__TAURI__;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("explains a stopped local service instead of leaking a fetch error", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const events: boolean[] = [];
    const listener = (event: Event) => events.push(Boolean((event as CustomEvent<{ offline: boolean }>).detail.offline));
    window.addEventListener("ticketbunker-service", listener);

    await expect(command("application.state")).rejects.toThrow(OFFLINE_MESSAGE);

    window.removeEventListener("ticketbunker-service", listener);
    expect(events).toEqual([true]);
  });

  it("names a service older than the page instead of an unavailable command", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ ok: false, code: "command_unavailable", message: "That command is not available to the desktop application." }),
    } as unknown as Response);

    await expect(command("application.project.link")).rejects.toThrow(STALE_SERVICE_MESSAGE);
  });

  it("passes typed rejections through unchanged", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ ok: false, message: "Choose a valid ticket page." }),
    } as unknown as Response);

    await expect(command("application.ticket.list", { page: "x" })).rejects.toThrow("Choose a valid ticket page.");
    expect(globalThis.fetch).toHaveBeenCalledOnce();
  });

  it("retries a transient ticket-list connection once with the exact same request", async () => {
    globalThis.fetch = vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue({
      ok: true, json: async () => ({ ok: true, tickets: [{ id: "QA-1" }] }),
    });
    await expect(command("application.ticket.list", { page: 2 })).resolves.toEqual({ ok: true, tickets: [{ id: "QA-1" }] });
    const calls = vi.mocked(globalThis.fetch).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0]).toEqual(calls[1]);
  });

  it("bounds failed list reads but never retries writes, materialization or synthesis", async () => {
    for (const action of ["application.ticket.list", "application.ticket.get", "workspace.file.save", "workspace.save", "application.project.sync", "synthesis.request"]) {
      globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
      await expect(command(action)).rejects.toThrow(OFFLINE_MESSAGE);
      expect(globalThis.fetch).toHaveBeenCalledTimes(action === "application.ticket.list" ? 2 : 1);
    }
  });

  it("explains unreadable or malformed responses without implying an action failed", async () => {
    for (const value of [null, [], "bad", {}]) {
      globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(value) });
      await expect(command("workspace.file.save")).rejects.toThrow("Your action may have completed");
    }
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, json: () => Promise.reject(new SyntaxError("Unexpected token <")) });
    await expect(command("workspace.file.save")).rejects.toThrow("Refresh the view");
  });

  it("never retries an unreadable list response or a request serialization failure", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => { throw new SyntaxError("Truncated JSON"); } });
    await expect(command("application.ticket.list")).rejects.toThrow("could not read");
    expect(globalThis.fetch).toHaveBeenCalledOnce();
    vi.mocked(globalThis.fetch).mockClear();
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    await expect(command("application.ticket.list", cyclic)).rejects.toThrow(TypeError);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it("bootstrap explains a malformed response instead of leaking a TypeError", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(null) });
    await expect(bootstrap()).rejects.toThrow("could not read");
  });
});
