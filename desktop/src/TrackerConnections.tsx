import { FormEvent, useState } from "react";
import { command } from "./transport";
import type { Bunker } from "./types";

export const TRACKER_LABELS: Record<string, string> = { github: "GitHub", gitlab: "GitLab", jira: "Jira" };

export type Connection = { provider: string; account: string; name?: string; recorded?: boolean; note?: string };

export default function TrackerConnections({
  trackers,
  connections,
  bunkerId,
  onConnected,
  onMessage,
  providers = Object.keys(TRACKER_LABELS),
  compact = false,
}: {
  trackers?: Record<string, boolean>;
  connections?: Record<string, { account: string; name?: string }>;
  bunkerId?: string;
  onConnected: (trackers: Record<string, boolean>, connection: Connection, bunker?: Bunker) => void;
  onMessage: (message: string, error?: boolean) => void;
  providers?: string[];
  compact?: boolean;
}) {
  const [busy, setBusy] = useState("");

  async function connect(provider: string, event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const token = String(new FormData(form).get("token") || "");
    setBusy(provider);
    try {
      const result = await command<{ trackers: Record<string, boolean>; connection: Connection; bunker?: Bunker }>(
        "application.tracker.connect",
        { provider, token, bunker_id: bunkerId || "" },
      );
      form.reset();
      onConnected(result.trackers, result.connection, result.bunker);
      const label = TRACKER_LABELS[provider] || provider;
      onMessage(result.connection.account ? `${label} connected as ${result.connection.account} on this computer` : `${label} token saved on this computer`);
    } catch (reason) {
      onMessage(String(reason), true);
    } finally {
      setBusy("");
    }
  }

  return (
    <div className={compact ? "tracker-connections compact" : "tracker-connections"}>
      {providers.map((provider) => {
        const label = TRACKER_LABELS[provider] || provider;
        const connected = Boolean(trackers?.[provider]);
        const shared = connections?.[provider];
        return (
          <form key={provider} className="tracker-connection" onSubmit={(event) => connect(provider, event)}>
            <div className="token-row">
              <i className={connected ? "state-dot ready" : "state-dot"} />
              <strong>{label}</strong>
              <span>{connected ? "Connected on this computer" : "Not connected on this computer"}{shared?.account ? ` · Bunker account ${shared.account}` : ""}</span>
            </div>
            <label>{label} token<input name="token" type="password" autoComplete="off" placeholder={connected ? "Paste a new token to replace it" : "Paste a personal access token"} required /></label>
            <button className="primary" disabled={busy === provider}>{busy === provider ? "Checking…" : connected ? `Replace ${label} token` : `Connect ${label}`}</button>
            {provider !== "github" && <small className="field-note">Saved for later: {label} boards do not sync in this version.</small>}
          </form>
        );
      })}
    </div>
  );
}
