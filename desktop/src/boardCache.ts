import { command } from "./transport";
import type { Board } from "./types";

type Listing = { boards: Board[]; notes: string[] };
const cache = new Map<string, { at: number; value: Listing }>();
const pending = new Map<string, Promise<Listing>>();
let generation = 0;
export function cachedBoards(key: string): Listing | undefined { return cache.get(key)?.value; }
export function clearBoardCache() { generation++; cache.clear(); pending.clear(); }
export async function listBoards(key: string, force = false): Promise<Listing> {
  const entry = cache.get(key);
  if (!force && entry && Date.now() - entry.at < 300_000) return entry.value;
  const running = pending.get(key);
  if (running) return running;
  const started = generation;
  const request = command<Listing>("application.tracker.boards", { provider: "github" }).then((value) => {
    if (started !== generation) throw new Error("Tracker connection changed. Refresh the board list.");
    cache.set(key, { at: Date.now(), value }); return value;
  }).finally(() => { if (pending.get(key) === request) pending.delete(key); });
  pending.set(key, request);
  return request;
}
