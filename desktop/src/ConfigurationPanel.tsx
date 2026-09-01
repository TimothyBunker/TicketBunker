import { useEffect, useMemo, useState } from "react";
import { command } from "./transport";
import type { Bunker, ConfigurationState, Integration, Project, TicketField } from "./types";

const FIELD_TYPES = ["string", "text", "number", "boolean", "date", "datetime", "user", "enum", "tags", "url"];

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export default function ConfigurationPanel({
  bunker,
  project,
  onClose,
  notify,
}: {
  bunker: Bunker;
  project?: Project;
  onClose: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [state, setState] = useState<ConfigurationState | null>(null);
  const [fields, setFields] = useState<TicketField[]>([]);
  const [providers, setProviders] = useState<Record<string, Integration>>({});
  const [tab, setTab] = useState<"fields" | "integrations">("fields");
  const [provider, setProvider] = useState("github");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setError("");
    command<ConfigurationState>("application.bunker.configuration.get", {
      bunker_id: bunker.id,
      project_id: project?.id || "",
    }).then((value) => {
      if (!active) return;
      setState(value);
      setFields(clone(value.documents["ticket-fields"]?.content.fields || []));
      const next = clone(value.documents.integrations?.content.providers || {});
      setProviders(next);
      setProvider((current) => current in next ? current : Object.keys(next)[0] || "");
    }).catch((reason) => active && setError(String(reason)));
    return () => { active = false; };
  }, [bunker.id, project?.id]);

  const canWrite = Boolean(state?.permission.can_write);
  const selected = providers[provider];
  const mappingRows = useMemo(() => Object.entries(selected?.fields || {}), [selected]);

  async function publish(kind: "ticket-fields" | "integrations") {
    const document = state?.documents[kind];
    if (!document) return setError("This configuration is not available in the current snapshot.");
    setBusy(true);
    setError("");
    try {
      const value = await command<ConfigurationState>("application.bunker.configuration.replace", {
        bunker_id: bunker.id,
        project_id: project?.id || "",
        kind,
        expected_revision: document.revision,
        content: kind === "ticket-fields" ? { fields } : { providers },
      });
      setState(value);
      setFields(clone(value.documents["ticket-fields"]?.content.fields || []));
      setProviders(clone(value.documents.integrations?.content.providers || {}));
      notify(kind === "ticket-fields" ? "Ticket fields published" : "Integration mappings published");
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  function updateField(index: number, patch: Partial<TicketField>) {
    setFields((values) => values.map((value, item) => item === index ? { ...value, ...patch } : value));
  }

  function updateProvider(patch: Partial<Integration>) {
    setProviders((values) => ({ ...values, [provider]: { ...values[provider], ...patch } }));
  }

  function updateMapping(index: number, ours: string, theirs: string) {
    const rows = mappingRows.map(([left, right], item) => item === index ? [ours, theirs] : [left, right]);
    updateProvider({ fields: Object.fromEntries(rows.filter(([left]) => left)) });
  }

  function addProvider() {
    let suffix = 1;
    let id = "custom";
    while (providers[id]) id = `custom-${++suffix}`;
    setProviders((values) => ({ ...values, [id]: { label: "Custom integration", template: "custom", fields: {} } }));
    setProvider(id);
  }

  function renameProvider(next: string) {
    const id = next.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^[^a-z]+|[-._]+$/g, "");
    if (!id || id === provider || providers[id]) return;
    const nextProviders = { ...providers };
    nextProviders[id] = nextProviders[provider];
    delete nextProviders[provider];
    setProviders(nextProviders);
    setProvider(id);
  }

  return (
    <aside className="configuration-drawer" aria-label="Configuration">
      <header>
        <div><p className="eyebrow">{project ? `${project.key} · pinned project setup` : "Bunker defaults · new projects"}</p><h2>Configuration</h2></div>
        <button className="icon-control" onClick={onClose} aria-label="Back">←</button>
      </header>
      <nav><button className={tab === "fields" ? "selected" : ""} onClick={() => setTab("fields")}>Ticket fields</button><button className={tab === "integrations" ? "selected" : ""} onClick={() => setTab("integrations")}>Integrations</button></nav>
      {!state && !error && <div className="ticket-loading">Loading configuration</div>}
      {state && !canWrite && <div className="permission-note"><strong>Read only</strong><span>{state.permission.reason}</span></div>}
      {error && <div className="inline-error">{error}</div>}

      {state && tab === "fields" && <section className="config-surface">
        <div className="config-heading"><div><h3>Ticket fields</h3><p>{project ? "This Project keeps this exact composition." : "New Projects copy these fields when created."}</p></div><button className="primary" disabled={!canWrite || busy} onClick={() => publish("ticket-fields")}>Publish fields</button></div>
        <div className="field-matrix">
          <div className="matrix-heading"><span>Label</span><span>Key</span><span>Type</span><span>Required</span><span /></div>
          {fields.map((field, index) => <div className="field-row" key={`${field.key}-${index}`}>
            <input aria-label={`Field ${index + 1} label`} value={field.label} disabled={!canWrite} onChange={(event) => updateField(index, { label: event.target.value })} />
            <input aria-label={`Field ${index + 1} key`} value={field.key} disabled={!canWrite} onChange={(event) => updateField(index, { key: event.target.value })} />
            <select aria-label={`Field ${index + 1} type`} value={field.type} disabled={!canWrite} onChange={(event) => updateField(index, { type: event.target.value })}>{FIELD_TYPES.map((value) => <option key={value}>{value}</option>)}</select>
            <input aria-label={`Field ${index + 1} required`} type="checkbox" checked={field.required} disabled={!canWrite} onChange={(event) => updateField(index, { required: event.target.checked })} />
            <button aria-label={`Remove ${field.label}`} disabled={!canWrite} onClick={() => setFields((values) => values.filter((_, item) => item !== index))}>−</button>
            {field.type === "enum" && <input className="enum-values" aria-label={`${field.label} values`} value={(field.values || []).join(", ")} disabled={!canWrite} placeholder="Comma-separated values" onChange={(event) => updateField(index, { values: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) })} />}
          </div>)}
        </div>
        <button disabled={!canWrite} onClick={() => setFields((values) => [...values, { key: `field_${values.length + 1}`, label: "New field", type: "string", required: false }])}>+ Add field</button>
      </section>}

      {state && tab === "integrations" && <section className="config-surface">
        <div className="config-heading"><div><h3>Field mappings</h3><p>Each row pairs one TicketBunker field with one external payload path.</p></div><button className="primary" disabled={!canWrite || busy || !selected} onClick={() => publish("integrations")}>Publish mappings</button></div>
        <div className="integration-selector"><select aria-label="Integration" value={provider} onChange={(event) => setProvider(event.target.value)}>{Object.entries(providers).map(([id, value]) => <option key={id} value={id}>{value.label}</option>)}</select><button disabled={!canWrite} onClick={addProvider}>+ Integration</button></div>
        {selected && <>
          <div className="integration-identity"><label>Name<input value={selected.label} disabled={!canWrite} onChange={(event) => updateProvider({ label: event.target.value })} /></label><label>Identifier<input key={provider} defaultValue={provider} disabled={!canWrite || selected.template !== "custom"} onBlur={(event) => renameProvider(event.target.value)} /></label></div>
          <div className="mapping-matrix">
            <div className="matrix-heading"><span>TicketBunker</span><span>External field</span><span /></div>
            {mappingRows.map(([ours, theirs], index) => <div className="mapping-row" key={`${ours}-${index}`}>
              <select aria-label={`Mapping ${index + 1} TicketBunker field`} value={ours} disabled={!canWrite} onChange={(event) => updateMapping(index, event.target.value, theirs)}><option value="">Choose field</option>{fields.map((field) => <option key={field.key} value={field.key} disabled={field.key !== ours && selected.fields[field.key] !== undefined}>{field.label} · {field.key}</option>)}</select>
              <input aria-label={`Mapping ${index + 1} external field`} value={theirs} disabled={!canWrite} onChange={(event) => updateMapping(index, ours, event.target.value)} />
              <button aria-label={`Remove mapping ${index + 1}`} disabled={!canWrite} onClick={() => updateProvider({ fields: Object.fromEntries(mappingRows.filter((_, item) => item !== index)) })}>−</button>
            </div>)}
          </div>
          <div className="integration-actions"><button disabled={!canWrite} onClick={() => updateProvider({ fields: { ...selected.fields, [fields.find((field) => !selected.fields[field.key])?.key || ""]: "" } })}>+ Add mapping</button>{selected.template === "custom" && <button className="danger-control" disabled={!canWrite} onClick={() => { const values = { ...providers }; delete values[provider]; setProviders(values); setProvider(Object.keys(values)[0] || ""); }}>Remove integration</button>}</div>
        </>}
      </section>}
    </aside>
  );
}
