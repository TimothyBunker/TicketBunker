import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { command, OFFLINE_MESSAGE } from "./transport";

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

  it("passes typed rejections through unchanged", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ ok: false, message: "Choose a valid ticket page." }),
    } as unknown as Response);

    await expect(command("application.ticket.list", { page: "x" })).rejects.toThrow("Choose a valid ticket page.");
  });
});
