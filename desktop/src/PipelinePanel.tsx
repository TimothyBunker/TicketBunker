import { useEffect, useRef, useState } from "react";
import CloseButton from "./CloseButton";
import IconButton from "./IconButton";
import { command } from "./transport";
import { relativeTime } from "./report";
import type { Bunker } from "./types";
import "./pipelines.css";

export type PipelineSnapshot = {
  bunker_id: string; authority: "service" | "device"; pending_count: number; operations_truncated: boolean;
  rows: Array<{ id: string; name: string; source: string; target: string; trigger: string;
    state: string; result: string; note?: string; recorded_at?: string; error?: string; action?: string; project_id?: string }>;
  operations: Array<{ id: string; state: string; created_at: string; accepted: number; total: number;
    error: string; same_destination: boolean; targets_truncated: boolean;
    targets: Array<{ kind: string; record_id: string; ticket_id: string; project_id: string; scope: string; repository_id: string }> }>;
};

export function PipelineIndicator({ bunker, onClick, busy, issue, label = "Bunker pipelines" }: {
  bunker: Bunker; onClick: () => void; busy?: boolean; issue?: string; label?: string;
}) {
  return <IconButton icon={busy ? "sync" : bunker.remote ? "shared" : "device"} label={label} loading={busy}
    hint={bunker.remote ? "Shared Bunker with a local copy. Inspect transfers and saved receipts." : "Stored on this device. Inspect Bunker pipelines."}
    issue={issue || (bunker.pending_saves ? `${bunker.pending_saves} unfinished shared saves` : bunker.remote && bunker.remote.state !== "ready" ? "Shared connection needs attention" : undefined)} onClick={onClick} />;
}

const operationLabels: Record<string, string> = {
  prepared: "Awaiting confirmation", unknown: "Outcome unknown", accepted: "Accepted; local refresh needed",
  conflict: "Review required", complete: "Saved", superseded: "Replaced by a later save",
};

export default function PipelinePanel({ bunker, initialRow, ticketId, onClose, onChanged, onReview, onResult, notify, externalError = "" }: {
  bunker: Bunker; initialRow?: string; ticketId?: string; onClose: () => void;
  onChanged: (bunkers: Bunker[]) => void;
  onResult?: (action: string, projectId: string | undefined, error: string) => void;
  onReview?: (target: { ticketId?: string; projectId?: string }) => void;
  notify: (message: string, error?: boolean) => void; externalError?: string;
}) {
  const [snapshot, setSnapshot] = useState<PipelineSnapshot | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [version, setVersion] = useState(0);
  const [expanded, setExpanded] = useState(initialRow || (bunker.remote ? "shared-refresh" : "local"));
  const panel = useRef<HTMLElement>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const trigger = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    return () => { mounted.current = false; if (trigger?.isConnected) trigger.focus(); };
  }, []);
  useEffect(() => {
    let active = true;
    setSnapshot(null);
    command<PipelineSnapshot>("application.bunker.pipelines", { bunker_id: bunker.id })
      .then((value) => { if (active) setSnapshot(value); })
      .catch((reason) => { if (active) setError(String(reason)); });
    return () => { active = false; };
  }, [bunker.id, version]);

  async function run(action: string, projectId?: string) {
    if (busy) return;
    setBusy(action === "tracker" ? `tracker:${projectId}` : action); setError("");
    try {
      const result = await command<{ bunkers: Bunker[]; warning?: string }>(
        action === "sync" ? "application.bunker.sync" : "application.project.sync",
        { bunker_id: bunker.id, ...(projectId ? { project_id: projectId } : {}) });
      if (!mounted.current) return;
      onChanged(result.bunkers);
      onResult?.(action, projectId, result.warning || "");
      setError(result.warning || "");
      notify(result.warning || (action === "sync" ? "Shared snapshot downloaded" : "Tickets imported"), Boolean(result.warning));
    } catch (reason) {
      if (mounted.current) { setError(String(reason)); onResult?.(action, projectId, String(reason)); notify(String(reason), true); }
    } finally {
      if (mounted.current) { setBusy(""); setVersion((value) => value + 1); }
    }
  }

  const operations = snapshot?.operations.filter((item) => !ticketId || item.targets.some((target) => target.ticket_id === ticketId)) || [];
  return <aside ref={panel} tabIndex={-1} className="pipeline-panel" aria-label="Bunker pipelines" onKeyDown={(event) => {
    if (event.key === "Escape" && !busy) { event.stopPropagation(); onClose(); }
  }}>
    <header><div><p className="eyebrow">{bunker.name}</p><h2>Pipelines</h2></div><CloseButton label="Close pipelines" disabled={Boolean(busy)} onClick={onClose} /></header>
    <div className="pipeline-authority"><span>{bunker.remote ? "Shared authority → local copy" : "Local authority"}</span>
      <IconButton icon="sync" label="Refresh pipeline status" hint="Reread local receipts only; no transfers" disabled={Boolean(busy)} onClick={() => { setError(""); setVersion((value) => value + 1); }} /></div>
    {(error || externalError) && <p role="alert" className="inline-error">{error || externalError}</p>}
    {!snapshot && <p role="status">{error ? "Pipeline status unavailable. Refresh to retry." : "Reading local receipts…"}</p>}
    {snapshot?.rows.map((row) => <details key={row.id} open={expanded === row.id} className="pipeline-row">
      <summary onClick={(event) => { event.preventDefault(); setExpanded((current) => current === row.id ? "" : row.id); }}><span><strong>{row.name}</strong><small>{row.source} → {row.target}</small></span><span className={`pipeline-state ${row.state}`}>
        {busy && ((busy === "sync" && ["shared-refresh", "shared-saves"].includes(row.id)) || busy === row.id) ? "Transferring…" : row.result}
      </span></summary>
      <div className="pipeline-detail" hidden={expanded !== row.id}><dl><dt>When</dt><dd>{row.trigger}</dd>
        {row.recorded_at && <><dt>Last recorded</dt><dd><time dateTime={row.recorded_at} title={row.recorded_at}>{relativeTime(row.recorded_at)}</time></dd></>}
      </dl>{row.note && <p>{row.note}</p>}{row.error && <p className="inline-error">{row.error}</p>}
        {row.action && <button disabled={Boolean(busy)} onClick={() => run(row.action!, row.project_id)}>{row.action === "sync" ? "Sync Bunker" : "Sync tickets"}</button>}
        {row.id === "reports" && bunker.remote && <button onClick={() => setExpanded("shared-saves")}>View save receipts</button>}
        {row.id === "shared-saves" && <>
          <h3>{ticketId ? `${ticketId} saves` : "Recent saves"}</h3>
          {ticketId && <p>Filtered to this ticket. Bunker sync still reconciles all previously requested saves.</p>}
          {!operations.length && <p>No save receipts{ticketId ? " for this ticket" : ""} in the displayed history.</p>}
          <ol className="pipeline-operations">{operations.map((item) => <li key={item.id}>
            <strong>{!item.same_destination && item.state !== "complete" ? "Different service connection" : operationLabels[item.state] || item.state}</strong>
            <small>{item.accepted}/{item.total} changes confirmed · Started <time title={item.created_at}>{relativeTime(item.created_at)}</time></small>
            <ul>{item.targets.map((target, index) => <li key={index}><span>{target.kind}: {target.ticket_id || target.record_id}{target.scope ? ` · ${target.scope}` : ""}</span>
              {item.state === "conflict" && onReview && <button onClick={() => onReview({ ticketId: target.ticket_id || undefined, projectId: target.project_id || undefined })}>Review</button>}
            </li>)}</ul>
            {item.targets_truncated && <small>First 20 affected records shown.</small>}
            {item.error && <p className="inline-error">{item.error}</p>}
            {item.state === "conflict" && <p>Review the affected ticket or configuration and save again. Sync will not overwrite the conflict.</p>}
          </li>)}</ol>
          {snapshot.operations_truncated && <p>Showing up to 50 recent saves, unfinished first. {snapshot.pending_count} unfinished in this Bunker.</p>}
        </>}
      </div>
    </details>)}
  </aside>;
}
