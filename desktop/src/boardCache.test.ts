import { beforeEach, expect, it, vi } from "vitest";
import { cachedBoards, clearBoardCache, listBoards } from "./boardCache";
const transport = vi.hoisted(() => ({ command: vi.fn() }));
vi.mock("./transport", () => transport);
const listing = { boards: [], notes: [] };
beforeEach(() => { clearBoardCache(); transport.command.mockReset(); transport.command.mockResolvedValue(listing); });
it("reuses the board list across tab changes and allows explicit refresh", async () => {
  await listBoards("bunker"); await listBoards("bunker");
  expect(transport.command).toHaveBeenCalledTimes(1);
  await listBoards("bunker", true);
  expect(transport.command).toHaveBeenCalledTimes(2);
});
it("deduplicates concurrent loads and isolates Bunkers", async () => {
  await Promise.all([listBoards("one"), listBoards("one")]);
  expect(transport.command).toHaveBeenCalledTimes(1);
  await listBoards("two"); expect(transport.command).toHaveBeenCalledTimes(2);
});
it("refreshes after five minutes without throwing away a previously visible list", async () => {
  const time = vi.spyOn(Date, "now").mockReturnValue(1000);
  try { await listBoards("bunker"); time.mockReturnValue(302000); expect(cachedBoards("bunker")).toEqual(listing); await listBoards("bunker"); expect(transport.command).toHaveBeenCalledTimes(2); }
  finally { time.mockRestore(); }
});
it("does not cache failures", async () => {
  transport.command.mockRejectedValueOnce(new Error("Offline"));
  await expect(listBoards("bunker")).rejects.toThrow("Offline");
  await listBoards("bunker"); expect(transport.command).toHaveBeenCalledTimes(2);
});
it("does not retain an old account's pending response after credentials change", async () => {
  let resolve!: (value: typeof listing) => void;
  transport.command.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
  const pending = listBoards("bunker"); clearBoardCache(); resolve(listing);
  await expect(pending).rejects.toThrow("Tracker connection changed");
  expect(cachedBoards("bunker")).toBeUndefined();
});
