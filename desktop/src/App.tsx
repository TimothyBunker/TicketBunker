import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import BunkerHub from "./BunkerHub";
import TicketWorkspace from "./TicketWorkspace";
import TrackerConnections from "./TrackerConnections";
import { bootstrap, command, isNative, listenNative, nativeInvoke, openExternal, OFFLINE_MESSAGE } from "./transport";
import type { AppState, SourceDraft, TicketReference } from "./types";

type Notice = { message: string; error: boolean } | null;
type Selection = { bunkerId: string; projectId?: string } | null;

function Loading() {
  return <main className="boot-screen"><span className="spinner" /><p>Opening TicketBunker</p></main>;
}

function ProfileSetup({
  state,
  onState,
}: {
  state: AppState;
  onState: (value: AppState) => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const payload = Object.fromEntries(new FormData(event.currentTarget));
      const result = await command<{ profile: AppState["profile"] }>("application.profile.update", payload);
      onState({ ...state, profile: result.profile });
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="first-run">
      <section>
        <div className="brand large"><span className="brand-mark">TB</span><span>TicketBunker</span></div>
        <p className="eyebrow">First run</p>
        <h1>Your local identity</h1>
        <p>Used for ticket branches and coworker sessions on this computer.</p>
        <form onSubmit={save}>
          <label>Name<input name="name" defaultValue={state.profile.name} autoComplete="name" autoFocus required /></label>
          <label>Email<input name="email" type="email" defaultValue={state.profile.email} autoComplete="email" required /></label>
          <label>Branch name<input name="branch_user" defaultValue={state.profile.branch_user} autoComplete="username" required /></label>
          {error && <div className="inline-error">{error}</div>}
          <button className="primary" disabled={busy}>{busy ? "Saving…" : "Continue"}</button>
        </form>
      </section>
    </main>
  );
}

function DirectEditor({
  projectRoot,
  draft,
  onBack,
  notify,
}: {
  projectRoot: string;
  draft: SourceDraft;
  onBack: () => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [content, setContent] = useState(draft.content);
  const [digest, setDigest] = useState(draft.digest);
  const [saved, setSaved] = useState(draft.content);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      const result = await command<{ content: string; digest: string; saved: boolean }>("editor.save", { path: draft.path, content, expected_digest: digest }, projectRoot);
      setContent(result.content);
      setSaved(result.content);
      setDigest(result.digest);
      notify(result.saved ? `Saved ${draft.path}` : `${draft.path} is unchanged`);
    } catch (reason) {
      notify(String(reason), true);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="direct-editor">
      <header><div className="brand"><span className="brand-mark">TB</span><span>TicketBunker</span></div><button className="back-control" onClick={onBack}>← Bunkers</button><div><strong>{draft.path}</strong><small>{draft.branch || projectRoot}</small></div><button className="primary" disabled={busy || content === saved} onClick={save}>Save</button></header>
      <textarea value={content} onChange={(event) => setContent(event.target.value)} spellCheck={false} wrap={draft.settings.line_wrap ? "soft" : "off"} style={{ tabSize: draft.settings.tab_size || 4 }} autoFocus />
    </main>
  );
}

type Provider = { id: string; name: string; models: Array<{ id: string; name: string }>; methods: string[] };

function AgentSetup({
  state,
  onState,
  required,
  onClose,
}: {
  state: AppState;
  onState: (value: AppState) => void;
  required: boolean;
  onClose: () => void;
}) {
  const [catalog, setCatalog] = useState<Provider[]>([]);
  const [provider, setProvider] = useState(state.agent.provider || "openai-codex");
  const [model, setModel] = useState(state.agent.model);
  const [method, setMethod] = useState("oauth");
  const [progress, setProgress] = useState("Preparing models");
  const [authUrl, setAuthUrl] = useState("");
  const [prompt, setPrompt] = useState<{ id: string; title: string; method: string; options?: string[] } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const current = catalog.find((value) => value.id === provider);

  useEffect(() => {
    if (!isNative()) {
      setProgress("Model login is available in the TicketBunker desktop app.");
      return;
    }
    let unlisten: () => void = () => undefined;
    let closed = false;
    listenNative<Record<string, unknown>>("agent-setup-event", (event) => {
      if (event.type === "ticketbunker_agent_error") return setError(String(event.message || "Agent setup stopped."));
      if (event.type !== "extension_ui_request") return;
      if ((event.method === "input" || event.method === "select") && typeof event.id === "string") {
        setPrompt({ id: event.id, title: String(event.title || "Continue login").replace(/^TB_(SECRET|INPUT):/, ""), method: String(event.method), options: Array.isArray(event.options) ? event.options.map(String) : [] });
        return;
      }
      if (event.method !== "notify" || !String(event.message || "").startsWith("TB_AUTH_EVENT:")) return;
      try {
        const payload = JSON.parse(String(event.message).slice("TB_AUTH_EVENT:".length)) as Record<string, unknown>;
        if (payload.kind === "catalog") {
          const values = Array.isArray(payload.providers) ? payload.providers as Provider[] : [];
          setCatalog(values.filter((value) => value.models?.length));
          const desired = values.find((value) => value.id === provider) || values[0];
          if (desired) {
            setProvider(desired.id);
            setModel((old) => old || desired.models[0]?.id || "");
            setMethod(desired.methods?.[0] || "oauth");
          }
          setProgress("");
        } else if (payload.kind === "auth_url") {
          setProgress(String(payload.instructions || "Continue sign-in in your browser."));
          setAuthUrl(String(payload.url || ""));
        } else if (payload.kind === "device_code") {
          setProgress(`Enter ${String(payload.user_code || "the shown code")} on the provider sign-in page.`);
          setAuthUrl(String(payload.verification_url || ""));
        } else if (payload.kind === "complete") {
          setProgress("Login complete. Choose a model.");
          setPrompt(null);
        } else if (payload.kind === "error") {
          setError(String(payload.message || "Login could not finish."));
        } else if (payload.kind === "progress" || payload.kind === "info") {
          setProgress(String(payload.message || "Continuing login"));
        }
      } catch {
        setError("Agent setup returned an unreadable response. Retry setup.");
      }
    }).then((stop) => {
      if (closed) stop(); else unlisten = stop;
    });
    nativeInvoke("start_agent_setup").catch((reason) => setError(String(reason)));
    return () => {
      closed = true;
      unlisten();
      nativeInvoke("stop_agent_setup_command").catch(() => undefined);
    };
  }, []);

  useEffect(() => {
    const selected = catalog.find((value) => value.id === provider);
    if (!selected) return;
    if (!selected.models.some((value) => value.id === model)) setModel(selected.models[0]?.id || "");
    if (!selected.methods.includes(method)) setMethod(selected.methods[0] || "oauth");
  }, [provider, catalog]);

  async function login() {
    setBusy(true);
    setError("");
    setProgress("Starting secure login");
    try {
      await nativeInvoke("begin_agent_login", { provider, method });
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }
  async function answer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!prompt) return;
    const value = String(new FormData(event.currentTarget).get("value") || "");
    await nativeInvoke("answer_agent_prompt", { requestId: prompt.id, value, cancelled: false });
    setPrompt(null);
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      const result = await nativeInvoke<{ agent: Record<string, unknown> }>("configure_agent", { payload: { enabled: true, provider, model } });
      if (!result.agent.ready) return setError(String(result.agent.summary || "Sign in before using this model."));
      onState({ ...state, agent: { enabled: true, configured: true, decided: true, provider: String(result.agent.provider || provider), model: String(result.agent.model || model) } });
      onClose();
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }
  async function disable() {
    try {
      const result = isNative()
        ? await nativeInvoke<{ agent: Record<string, unknown> }>("configure_agent", { payload: { enabled: false } })
        : await command<{ agent: Record<string, unknown> }>("application.agent.configure", { enabled: false });
      onState({ ...state, agent: { enabled: false, configured: false, decided: true, provider: String(result.agent.provider || ""), model: String(result.agent.model || "") } });
      onClose();
    } catch (reason) {
      setError(String(reason));
    }
  }
  return (
    <section className="agent-setup">
      <header><div><p className="eyebrow">Local settings</p><h2>Agent</h2></div>{!required && <button className="icon-control" onClick={onClose}>←</button>}</header>
      <p>Choose the model that maintains ticket synthesis. Provider login stays with the provider runtime.</p>
      {catalog.length > 0 && <div className="agent-grid">
        <label>Provider<select value={provider} onChange={(event) => setProvider(event.target.value)}>{catalog.map((value) => <option key={value.id} value={value.id}>{value.name}</option>)}</select></label>
        <label>Model<select value={model} onChange={(event) => setModel(event.target.value)}>{(current?.models || []).map((value) => <option key={value.id} value={value.id}>{value.name === value.id ? value.id : `${value.name} · ${value.id}`}</option>)}</select></label>
        {(current?.methods || []).length > 0 && <div className="agent-login"><select value={method} onChange={(event) => setMethod(event.target.value)}>{current?.methods.map((value) => <option key={value} value={value}>{value === "oauth" ? "Provider account" : "API key"}</option>)}</select><button onClick={login} disabled={busy}>{method === "oauth" ? "Sign in" : "Add API key"}</button></div>}
      </div>}
      {progress && <div className="agent-progress"><span>{progress}</span>{authUrl && <button onClick={() => nativeInvoke("open_agent_auth_url", { url: authUrl })}>Open sign-in</button>}</div>}
      {prompt && <form className="agent-prompt" onSubmit={answer}><label>{prompt.title}{prompt.method === "select" ? <select name="value">{prompt.options?.map((value) => <option key={value}>{value}</option>)}</select> : <input name="value" type={prompt.title.toLowerCase().includes("key") ? "password" : "text"} autoFocus />}</label><div><button type="button" onClick={() => { nativeInvoke("answer_agent_prompt", { requestId: prompt.id, value: null, cancelled: true }); setPrompt(null); }}>Cancel</button><button className="primary">Continue</button></div></form>}
      {error && <div className="inline-error">{error}</div>}
      <div className="agent-actions"><button onClick={disable}>Use without agent</button><button className="primary" disabled={!model || busy || !isNative()} onClick={save}>Use selected model</button></div>
    </section>
  );
}

function Settings({
  state,
  onState,
  onClose,
  initialTab = "local",
}: {
  state: AppState;
  onState: (value: AppState) => void;
  onClose: () => void;
  initialTab?: "local" | "agent" | "trackers" | "system";
}) {
  const [tab, setTab] = useState<"local" | "agent" | "trackers" | "system">(initialTab);
  const [message, setMessage] = useState("");
  const [checkingRelease, setCheckingRelease] = useState(false);
  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      const result = await command<{ profile: AppState["profile"] }>("application.profile.update", Object.fromEntries(new FormData(event.currentTarget)));
      onState({ ...state, profile: result.profile });
      setMessage("Saved");
    } catch (reason) {
      setMessage(String(reason));
    }
  }
  async function saveEditor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      await command("local.editor.update", { ...values, editor_mode: "builtin", tab_size: Number(values.tab_size), line_wrap: values.line_wrap === "on", trim_trailing_whitespace: values.trim_trailing_whitespace === "on", ensure_final_newline: values.ensure_final_newline === "on" });
      setMessage("Saved");
    } catch (reason) {
      setMessage(String(reason));
    }
  }
  async function checkRelease() {
    setCheckingRelease(true);
    setMessage("");
    try {
      const result = await command<{ release: NonNullable<AppState["release"]> }>("application.release.check");
      onState({ ...state, release: result.release });
      setMessage(result.release.available ? `TicketBunker ${result.release.latest} is ready.` : "TicketBunker is up to date.");
    } catch (reason) {
      setMessage(String(reason));
    } finally {
      setCheckingRelease(false);
    }
  }
  async function downloadRelease() {
    const url = state.release?.download_url || state.release?.release_url;
    if (!url) return checkRelease();
    try {
      await openExternal(url);
    } catch (reason) {
      setMessage(String(reason));
    }
  }
  if (tab === "agent") return <aside className="settings-drawer"><AgentSetup state={state} onState={onState} required={!state.agent.decided} onClose={onClose} /></aside>;
  return (
    <aside className="settings-drawer">
      <header><div><p className="eyebrow">This computer</p><h2>Settings</h2></div><button className="icon-control" onClick={onClose} aria-label="Close settings">←</button></header>
      <nav><button className={tab === "local" ? "selected" : ""} onClick={() => setTab("local")}>Local</button><button onClick={() => setTab("agent")}>Agent</button><button className={tab === "trackers" ? "selected" : ""} onClick={() => setTab("trackers")}>Trackers</button><button className={tab === "system" ? "selected" : ""} onClick={() => setTab("system")}>System</button></nav>
      {tab === "local" && <div className="settings-content">
        <form onSubmit={saveProfile}><h3>Identity</h3><label>Name<input name="name" defaultValue={state.profile.name} required /></label><label>Email<input name="email" type="email" defaultValue={state.profile.email} required /></label><label>Branch name<input name="branch_user" defaultValue={state.profile.branch_user} required /></label><button className="primary">Save identity</button></form>
        <form onSubmit={saveEditor}><h3>Included editor</h3><label>Indentation<select name="indent_style"><option value="spaces">Spaces</option><option value="tabs">Tabs</option></select></label><label>Tab size<input name="tab_size" type="number" min="1" max="16" defaultValue="4" /></label><label className="check"><input name="line_wrap" type="checkbox" defaultChecked />Wrap long lines</label><label className="check"><input name="trim_trailing_whitespace" type="checkbox" />Trim trailing whitespace on save</label><label className="check"><input name="ensure_final_newline" type="checkbox" defaultChecked />Ensure final newline</label><button className="primary">Save editor settings</button></form>
      </div>}
      {tab === "trackers" && <div className="settings-content">
        <div><h3>Trackers</h3><p className="settings-copy">Connect your agile app with a personal access token. Tokens stay in a private file on this computer and never sync. Each Project picks its board from the list that token can see.</p></div>
        <TrackerConnections trackers={state.trackers} onConnected={(trackers) => onState({ ...state, trackers })} onMessage={(message) => setMessage(message)} />
      </div>}
      {tab === "system" && <>
        <dl className="system-info">
          <div><dt>Version</dt><dd>{state.release?.current || "Unknown"}</dd></div>
          <div><dt>Updates</dt><dd className="update-status"><span>{state.release?.available ? `${state.release.latest} available` : state.release?.checked_at ? "Up to date" : "Not checked"}</span><button className={state.release?.available ? "primary" : ""} disabled={checkingRelease} onClick={state.release?.available ? downloadRelease : checkRelease}>{checkingRelease ? "Checking…" : state.release?.available ? "Download update" : "Check now"}</button></dd></div>
          <div><dt>Platform</dt><dd>{state.platform.family} · {state.platform.architecture}</dd></div>
          <div><dt>Application data</dt><dd>{state.platform.data_home}</dd></div>
          <div><dt>Diagnostics</dt><dd>{state.diagnostics?.application || `${state.platform.data_home}/diagnostics/app-worker.jsonl`}</dd></div>
          <div><dt>Agent</dt><dd>{state.agent.enabled ? state.agent.configured ? `${state.agent.provider} / ${state.agent.model}` : "Needs setup" : "Off"}</dd></div>
        </dl>
      </>}
      {message && <p className="settings-message">{message}</p>}
    </aside>
  );
}

export default function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [failure, setFailure] = useState("");
  const [selection, setSelection] = useState<Selection>(null);
  const [ticket, setTicket] = useState<{ bunkerId: string; ticket: TicketReference } | null>(null);
  const [settings, setSettings] = useState(false);
  const [directEditor, setDirectEditor] = useState<{ root: string; draft: SourceDraft } | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [offline, setOffline] = useState(false);
  const handledLaunch = useRef("");

  const notify = useCallback((message: string, error = false) => {
    setNotice({ message, error });
    window.setTimeout(() => setNotice(null), 5000);
  }, []);

  useEffect(() => {
    const listener = (event: Event) => setOffline(Boolean((event as CustomEvent<{ offline: boolean }>).detail?.offline));
    window.addEventListener("ticketbunker-service", listener);
    return () => window.removeEventListener("ticketbunker-service", listener);
  }, []);

  const openRepository = useCallback((path: string) => {
    if (!state) return;
    for (const bunker of state.bunkers) {
      const repository = bunker.repositories.find((value) => value.local_path === path);
      if (repository) {
        setSelection({ bunkerId: bunker.id, projectId: repository.project_id });
        return;
      }
    }
    notify("This repository is not attached to a local Bunker yet.", true);
  }, [state, notify]);

  const openLaunch = useCallback(async (path: string, route = "/") => {
    if (route.startsWith("/source")) {
      const parsed = new URL(route, "http://ticketbunker.local");
      const sourcePath = parsed.searchParams.get("path") || "";
      const line = Number(parsed.searchParams.get("line") || 1);
      if (sourcePath) {
        try {
          const draft = await command<SourceDraft>("editor.read", { path: sourcePath, line }, path);
          setDirectEditor({ root: path, draft });
          return;
        } catch (reason) {
          notify(String(reason), true);
        }
      }
    }
    openRepository(path);
  }, [openRepository, notify]);

  useEffect(() => {
    bootstrap().then(setState).catch((reason) => setFailure(String(reason)));
  }, []);
  useEffect(() => {
    if (!state?.launch?.repository) return;
    const key = `${state.launch.repository}\n${state.launch.route || "/"}`;
    if (handledLaunch.current === key) return;
    handledLaunch.current = key;
    openLaunch(state.launch.repository, state.launch.route);
  }, [state?.launch?.repository, state?.launch?.route, openLaunch]);
  useEffect(() => {
    let stop: () => void = () => undefined;
    listenNative<{ path: string; route?: string }>("open-project", (value) => openLaunch(value.path, value.route)).then((value) => { stop = value; });
    return () => stop();
  }, [openLaunch]);
  useEffect(() => {
    if (state?.profile.complete && !state.agent.decided) setSettings(true);
  }, [state?.profile.complete, state?.agent.decided]);
  useEffect(() => {
    if (!state?.profile.complete || !isNative()) return;
    const attemptedAt = state.release?.attempted_at || state.release?.checked_at;
    const checked = attemptedAt ? Date.parse(attemptedAt) : 0;
    if (checked && Date.now() - checked < 24 * 60 * 60 * 1000) return;
    const timer = window.setTimeout(() => {
      command<{ release: NonNullable<AppState["release"]> }>("application.release.check")
        .then((result) => setState((current) => current ? { ...current, release: result.release } : current))
        .catch(() => undefined);
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [state?.profile.complete, state?.release?.attempted_at, state?.release?.checked_at]);

  const selectedBunker = useMemo(() => state?.bunkers.find((value) => value.id === ticket?.bunkerId), [state, ticket]);
  if (failure) return <main className="boot-error"><h1>TicketBunker could not open</h1><p>{failure}</p><button onClick={() => window.location.reload()}>Retry</button></main>;
  if (!state) return <Loading />;
  if (!state.profile.complete) return <ProfileSetup state={state} onState={setState} />;
  return (
    <>
      {directEditor ? <DirectEditor projectRoot={directEditor.root} draft={directEditor.draft} onBack={() => setDirectEditor(null)} notify={notify} /> : ticket && selectedBunker ? <TicketWorkspace bunker={selectedBunker} reference={ticket.ticket} onBack={() => setTicket(null)} notify={notify} /> : <BunkerHub state={state} selection={selection} onSelection={(value) => { setSelection(value); setTicket(null); }} onTicket={(bunkerId, value) => setTicket({ bunkerId, ticket: value })} onState={setState} onSettings={() => setSettings(true)} notify={notify} />}
      {settings && <Settings state={state} onState={setState} onClose={() => setSettings(false)} initialTab={!state.agent.decided ? "agent" : "local"} />}
      {offline && <div className="service-banner" role="alert"><span>{OFFLINE_MESSAGE}</span><button onClick={() => window.location.reload()}>Reload</button></div>}
      {!offline && state.release?.current && state.release.current !== __TB_UI_VERSION__ && <div className="service-banner" role="alert"><span>This interface is version {__TB_UI_VERSION__} but the TicketBunker core it is talking to is {state.release.current}. Restart TicketBunker so both match.</span><button onClick={() => window.location.reload()}>Reload</button></div>}
      {notice && <div className={notice.error ? "toast error" : "toast"} role="status">{notice.message}</div>}
    </>
  );
}
