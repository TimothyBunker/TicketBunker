/**
 * TicketBunker pi extension — the thin adapter layer (DESIGN.md §3.1).
 *
 * Jobs, and only these jobs:
 *  1. Mark the harness session's start/end in the bunker ledger.
 *  2. Mirror every agent tool call into the bunker ledger (initiator: agent)
 *     — structurally, as a side effect of execution, never by the agent's
 *     own volition.
 *
 * Context injection (tb.md) is done by `tb run` via --append-system-prompt,
 * so the composition stays harness-neutral. The bunker path arrives via the
 * TB_BUNKER environment variable, set by `tb run`.
 *
 * Load with: pi -e harness/pi/tb.ts   (or install into .pi/extensions/)
 */

import { appendFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  const bunker = process.env.TB_BUNKER;
  const ledgerPath = bunker ? `${bunker}/ledger.jsonl` : null;

  const log = (entry: Record<string, unknown>) => {
    if (!ledgerPath) return;
    appendFileSync(
      ledgerPath,
      JSON.stringify({ ts: new Date().toISOString(), ...entry }) + "\n",
    );
  };

  pi.on("session_start", async () => {
    log({ type: "harness_session_start", harness: "pi", branch: process.env.TB_BRANCH ?? null });
  });

  pi.on("tool_call", async (event) => {
    log({
      type: "agent_tool_call",
      stage: "agent_session",
      initiator: "agent",
      tool: event.toolName,
      // For bash calls the command line is the interesting part; for
      // file tools, the path. Store what identifies the action.
      detail:
        (event.input && (event.input.command ?? event.input.path ?? event.input.file_path)) ?? null,
    });
  });

  pi.on("session_shutdown", async () => {
    log({ type: "harness_session_end", harness: "pi" });
  });
}
