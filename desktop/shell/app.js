const invoke = window.__TAURI__.core.invoke;
const chooseDirectory = window.__TAURI__.dialog.open;
const listen = window.__TAURI__.event.listen;

let applicationState = null;
let pendingProject = null;
let agentCatalog = [];
let agentCurrent = null;
let agentPromptRequest = null;
let agentListener = null;
let agentDecisionRequired = false;

const byId = (id) => document.getElementById(id);
const loading = byId("loading-state");
const onboarding = byId("onboarding");
const projectsView = byId("projects-view");
const identityButton = byId("identity-button");
const projectList = byId("project-list");
const emptyState = byId("empty-state");
const connectDialog = byId("connect-dialog");
const connectForm = byId("connect-form");
const storagePreview = byId("storage-preview");
const settingsDialog = byId("settings-dialog");
const settingsForm = byId("settings-form");
const agentDialog = byId("agent-dialog");
const agentProvider = byId("agent-provider");
const agentModel = byId("agent-model");
const agentMethod = byId("agent-method");
const agentLogin = byId("agent-login");
const agentSave = byId("agent-save");
const agentProgress = byId("agent-progress");
const agentPrompt = byId("agent-prompt");
const notice = byId("notice");

function initials(name) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "TB";
}

function message(value, isError = false) {
  notice.textContent = value;
  notice.classList.toggle("error", isError);
  notice.hidden = false;
  window.clearTimeout(message.timeout);
  message.timeout = window.setTimeout(() => { notice.hidden = true; }, 5000);
}

function setBusy(button, busy) {
  if (!button) return;
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
}

function fillProfile(form, profile) {
  form.elements.name.value = profile.name || "";
  form.elements.email.value = profile.email || "";
  form.elements.branch_user.value = profile.branch_user || "";
}

function profilePayload(form) {
  return {
    name: form.elements.name.value,
    email: form.elements.email.value,
    branch_user: form.elements.branch_user.value,
  };
}

function agentLabel(agent) {
  if (!agent.enabled) return "Off on this computer";
  if (agent.configured) return `${agent.model} · ${agent.provider}`;
  return "Not set up";
}

function updateIdentity() {
  const profile = applicationState.profile;
  identityButton.querySelector(".identity-name").textContent = profile.name;
  identityButton.querySelector(".avatar").textContent = initials(profile.name);
  identityButton.hidden = !profile.complete;
}

function projectStatus(project) {
  if (project.missing) return ["Folder unavailable", ""];
  if (project.ready) return ["Ready", "ready"];
  if (project.initialized) return ["Join locally", ""];
  return ["Not connected", ""];
}

function renderProjects() {
  projectList.replaceChildren();
  const projects = applicationState.projects || [];
  emptyState.hidden = projects.length !== 0;
  projectList.hidden = projects.length === 0;
  for (const project of projects) {
    const row = document.createElement("article");
    row.className = "project-row";
    const info = document.createElement("div");
    const title = document.createElement("div");
    title.className = "project-name";
    title.append(document.createTextNode(project.name));
    if (project.project) {
      const key = document.createElement("span");
      key.className = "project-key";
      key.textContent = project.project;
      title.append(key);
    }
    const path = document.createElement("div");
    path.className = "project-path";
    path.textContent = project.path;
    info.append(title, path);

    const actions = document.createElement("div");
    actions.className = "project-actions";
    const [label, className] = projectStatus(project);
    const state = document.createElement("span");
    state.className = `project-state ${className}`;
    state.textContent = label;
    const open = document.createElement("button");
    open.type = "button";
    open.textContent = project.ready ? "Open" : project.initialized ? "Join" : "Connect";
    open.disabled = Boolean(project.missing);
    open.addEventListener("click", () => project.ready ? openProject(project, open) : showConnect(project));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-button";
    remove.setAttribute("aria-label", `Remove ${project.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => removeProject(project, remove));
    actions.append(state, open, remove);
    row.append(info, actions);
    projectList.append(row);
  }
}

function render() {
  loading.hidden = true;
  updateIdentity();
  if (!applicationState.profile.complete) {
    projectsView.hidden = true;
    onboarding.hidden = false;
    fillProfile(byId("onboarding-form"), applicationState.profile);
    byId("onboarding-form").elements.name.focus();
    return;
  }
  onboarding.hidden = true;
  projectsView.hidden = false;
  renderProjects();
}

async function loadState() {
  loading.hidden = false;
  onboarding.hidden = true;
  projectsView.hidden = true;
  try {
    applicationState = await invoke("desktop_state");
    render();
    if (applicationState.profile.complete && !applicationState.agent.decided) {
      await showAgentSetup(true);
    }
  } catch (error) {
    const detail = document.createElement("p");
    detail.textContent = String(error);
    loading.replaceChildren(detail);
  }
}

async function saveProfile(form, button, status) {
  setBusy(button, true);
  status.textContent = "";
  try {
    const result = await invoke("update_profile", { payload: profilePayload(form) });
    applicationState.profile = result.profile;
    render();
    return true;
  } catch (error) {
    status.textContent = String(error);
    return false;
  } finally {
    setBusy(button, false);
  }
}

byId("onboarding-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const offerAgent = !applicationState.agent.decided;
  const saved = await saveProfile(event.currentTarget, event.submitter, event.currentTarget.querySelector("output"));
  if (saved && offerAgent) await showAgentSetup(true);
});

identityButton.addEventListener("click", () => {
  fillProfile(settingsForm, applicationState.profile);
  const agent = applicationState.agent;
  byId("agent-status").textContent = agentLabel(agent);
  byId("settings-status").textContent = "";
  settingsDialog.showModal();
});

byId("agent-configure").addEventListener("click", async () => {
  settingsDialog.close();
  await showAgentSetup();
});

function preferredProviders(providers) {
  const preferred = [agentCurrent?.provider, "openai-codex", "anthropic", "github-copilot", "google", "openai"];
  const rank = (id) => {
    const index = preferred.indexOf(id);
    return index < 0 ? preferred.length : index;
  };
  return providers
    .filter((provider) => provider.models?.length)
    .sort((left, right) => rank(left.id) - rank(right.id) || left.name.localeCompare(right.name));
}

function selectedProvider() {
  return agentCatalog.find((provider) => provider.id === agentProvider.value);
}

function fillAgentProvider() {
  const provider = selectedProvider();
  agentModel.replaceChildren();
  agentMethod.replaceChildren();
  if (!provider) {
    agentSave.disabled = true;
    return;
  }
  for (const model of provider.models) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.name === model.id ? model.id : `${model.name} · ${model.id}`;
    agentModel.append(option);
  }
  const currentProvider = agentCurrent?.provider || applicationState.agent.provider;
  const currentModel = agentCurrent?.model || applicationState.agent.model;
  if (currentProvider === provider.id && currentModel) agentModel.value = currentModel;
  for (const method of provider.methods) {
    const option = document.createElement("option");
    option.value = method;
    option.textContent = method === "oauth" ? "Provider account" : "API key";
    agentMethod.append(option);
  }
  const authenticated = Boolean(agentCurrent?.ready && agentCurrent.provider === provider.id);
  const hasLogin = provider.methods.length > 0;
  agentMethod.hidden = !hasLogin;
  agentLogin.hidden = !hasLogin;
  byId("agent-auth-state").textContent = authenticated ? "Connected" : hasLogin ? "Not connected" : "Uses local credentials";
  agentSave.disabled = !agentModel.value;
  updateAgentLoginLabel();
}

function fillAgentCatalog() {
  const desired = agentCurrent?.provider || applicationState.agent.provider || "openai-codex";
  agentProvider.replaceChildren();
  agentCatalog = preferredProviders(agentCatalog);
  for (const provider of agentCatalog) {
    const option = document.createElement("option");
    option.value = provider.id;
    option.textContent = provider.name;
    agentProvider.append(option);
  }
  if (agentCatalog.some((provider) => provider.id === desired)) agentProvider.value = desired;
  fillAgentProvider();
  byId("agent-loading").hidden = true;
  byId("agent-fields").hidden = false;
}

function updateAgentLoginLabel() {
  agentLogin.textContent = agentMethod.value === "api_key" ? "Add API key" : "Sign in";
}

function setAgentLoginBusy(busy) {
  setBusy(agentLogin, busy);
  agentProvider.disabled = busy;
  agentMethod.disabled = busy;
}

function showAgentProgress(text, { url = "", code = "" } = {}) {
  agentProgress.replaceChildren();
  if (code) {
    const value = document.createElement("strong");
    value.textContent = code;
    agentProgress.append(value);
  }
  const copy = document.createElement("span");
  copy.textContent = text;
  agentProgress.append(copy);
  if (url) {
    const open = document.createElement("button");
    open.type = "button";
    open.textContent = "Open sign-in";
    open.addEventListener("click", async () => {
      try {
        await invoke("open_agent_auth_url", { url });
      } catch (error) {
        byId("agent-dialog-status").textContent = String(error);
      }
    });
    agentProgress.append(document.createElement("br"), open);
  }
  agentProgress.hidden = false;
}

function handleAuthPayload(payload) {
  if (payload.kind === "catalog") {
    agentCatalog = payload.providers || [];
    fillAgentCatalog();
  } else if (payload.kind === "auth_url") {
    showAgentProgress(payload.instructions || "Continue in your browser.", { url: payload.url });
  } else if (payload.kind === "device_code") {
    showAgentProgress("Enter this code on the provider sign-in page.", {
      url: payload.verification_url,
      code: payload.user_code,
    });
  } else if (payload.kind === "info" || payload.kind === "progress") {
    const link = payload.links?.find((item) => item.url)?.url || "";
    showAgentProgress(payload.message || "Continuing login", { url: link });
  } else if (payload.kind === "complete") {
    agentCurrent = { ...agentCurrent, ready: true, provider: payload.provider };
    byId("agent-auth-state").textContent = "Connected";
    showAgentProgress("Login complete. Choose the model TicketBunker should use.");
    agentPrompt.hidden = true;
    setAgentLoginBusy(false);
  } else if (payload.kind === "cancelled") {
    showAgentProgress("Login cancelled.");
    agentPrompt.hidden = true;
    setAgentLoginBusy(false);
  } else if (payload.kind === "error") {
    byId("agent-dialog-status").textContent = payload.message || "Login could not finish.";
    agentPrompt.hidden = true;
    setAgentLoginBusy(false);
  }
}

function showAgentPrompt(payload) {
  agentPromptRequest = payload.id;
  const title = payload.title || "Continue login";
  const input = byId("agent-prompt-value");
  const select = byId("agent-prompt-select");
  byId("agent-prompt-title").textContent = title.replace(/^TB_(SECRET|INPUT):/, "");
  input.hidden = payload.method === "select";
  select.hidden = payload.method !== "select";
  input.value = "";
  input.type = title.startsWith("TB_SECRET:") ? "password" : "text";
  select.replaceChildren();
  for (const value of payload.options || []) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  }
  agentPrompt.hidden = false;
  (payload.method === "select" ? select : input).focus();
}

function handleAgentEvent(payload) {
  if (payload.type === "ticketbunker_agent_error") {
    byId("agent-dialog-status").textContent = payload.message;
    agentPrompt.hidden = true;
    setAgentLoginBusy(false);
    return;
  }
  if (payload.type !== "extension_ui_request") return;
  if (payload.method === "notify" && payload.message?.startsWith("TB_AUTH_EVENT:")) {
    try {
      handleAuthPayload(JSON.parse(payload.message.slice("TB_AUTH_EVENT:".length)));
    } catch (_error) {
      byId("agent-dialog-status").textContent = "Agent setup returned an unreadable response. Retry setup.";
    }
  } else if (payload.method === "input" || payload.method === "select") {
    showAgentPrompt(payload);
  }
}

async function ensureAgentListener() {
  if (!agentListener) agentListener = await listen("agent-setup-event", (event) => handleAgentEvent(event.payload));
}

async function showAgentSetup(requireDecision = false) {
  agentDecisionRequired = requireDecision;
  byId("agent-close").hidden = requireDecision;
  agentCatalog = [];
  agentCurrent = null;
  agentPromptRequest = null;
  byId("agent-loading").hidden = false;
  byId("agent-fields").hidden = true;
  agentProgress.hidden = true;
  agentPrompt.hidden = true;
  setAgentLoginBusy(false);
  byId("agent-dialog-status").textContent = "";
  agentSave.disabled = true;
  agentDialog.showModal();
  try {
    await ensureAgentListener();
    agentCurrent = await invoke("start_agent_setup");
    if (agentCatalog.length) fillAgentCatalog();
  } catch (error) {
    byId("agent-loading").hidden = true;
    byId("agent-dialog-status").textContent = String(error);
  }
}

agentProvider.addEventListener("change", fillAgentProvider);
agentMethod.addEventListener("change", updateAgentLoginLabel);

agentLogin.addEventListener("click", async () => {
  setAgentLoginBusy(true);
  byId("agent-dialog-status").textContent = "";
  showAgentProgress("Starting secure login");
  try {
    await invoke("begin_agent_login", { provider: agentProvider.value, method: agentMethod.value });
  } catch (error) {
    byId("agent-dialog-status").textContent = String(error);
    setAgentLoginBusy(false);
  }
});

agentPrompt.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = byId("agent-prompt-select").hidden ? byId("agent-prompt-value").value : byId("agent-prompt-select").value;
  try {
    await invoke("answer_agent_prompt", { requestId: agentPromptRequest, value, cancelled: false });
    agentPrompt.hidden = true;
    byId("agent-prompt-value").value = "";
  } catch (error) {
    byId("agent-dialog-status").textContent = String(error);
  }
});

byId("agent-prompt-cancel").addEventListener("click", async () => {
  try {
    await invoke("answer_agent_prompt", { requestId: agentPromptRequest, value: null, cancelled: true });
  } catch (error) {
    byId("agent-dialog-status").textContent = String(error);
  }
  agentPrompt.hidden = true;
});

agentSave.addEventListener("click", async () => {
  setBusy(agentSave, true);
  byId("agent-dialog-status").textContent = "";
  try {
    const result = await invoke("configure_agent", {
      payload: { enabled: true, provider: agentProvider.value, model: agentModel.value },
    });
    if (!result.agent.ready) {
      byId("agent-dialog-status").textContent = result.agent.summary || "Sign in before using this model.";
      byId("agent-auth-state").textContent = "Not connected";
      return;
    }
    applicationState.agent = {
      enabled: true,
      configured: true,
      decided: true,
      provider: result.agent.provider,
      model: result.agent.model,
    };
    agentDecisionRequired = false;
    agentDialog.close();
    message(`Agent set to ${result.agent.model}`);
  } catch (error) {
    byId("agent-dialog-status").textContent = String(error);
  } finally {
    setBusy(agentSave, false);
  }
});

byId("agent-disable").addEventListener("click", async () => {
  try {
    await invoke("configure_agent", { payload: { enabled: false } });
    applicationState.agent = { enabled: false, configured: false, decided: true, provider: "", model: "" };
    agentDecisionRequired = false;
    agentDialog.close();
    message("Agent is off on this computer");
  } catch (error) {
    byId("agent-dialog-status").textContent = String(error);
  }
});

byId("agent-close").addEventListener("click", () => {
  if (!agentDecisionRequired) agentDialog.close();
});
agentDialog.addEventListener("cancel", (event) => {
  if (agentDecisionRequired) event.preventDefault();
});
agentDialog.addEventListener("close", () => { invoke("stop_agent_setup_command").catch(() => {}); });

settingsForm.addEventListener("submit", async (event) => {
  if (event.submitter?.value !== "default") return;
  event.preventDefault();
  const saved = await saveProfile(settingsForm, event.submitter, byId("settings-status"));
  if (saved) settingsDialog.close();
});

async function chooseProject() {
  try {
    const selected = await chooseDirectory({ directory: true, multiple: false, title: "Add Git project" });
    if (!selected) return;
    const result = await invoke("add_git_project", { path: selected });
    applicationState.projects = result.projects;
    renderProjects();
    const project = result.project;
    if (project.ready) await openProject(project);
    else showConnect(project);
  } catch (error) {
    message(String(error), true);
  }
}

byId("add-project").addEventListener("click", chooseProject);
byId("empty-add-project").addEventListener("click", chooseProject);

async function openProject(project, button = null) {
  setBusy(button, true);
  try {
    await invoke("open_git_project", { path: project.path });
  } catch (error) {
    message(String(error), true);
    setBusy(button, false);
  }
}

async function removeProject(project, button) {
  setBusy(button, true);
  try {
    const result = await invoke("remove_git_project", { path: project.path });
    applicationState.projects = result.projects;
    renderProjects();
  } catch (error) {
    message(String(error), true);
    setBusy(button, false);
  }
}

function storageRemote() {
  const transport = connectForm.elements.storage_transport.value;
  const host = connectForm.elements.storage_host.value.trim();
  const namespace = connectForm.elements.storage_namespace.value.trim().replace(/^\/+|\/+$/g, "");
  const repository = connectForm.elements.storage_repository.value.trim().replace(/\.git$/, "");
  if (!host || !namespace || !repository) return "";
  const path = `${namespace}/${repository}.git`;
  return transport === "https" ? `https://${host}/${path}` : `git@${host}:${path}`;
}

function updateStoragePreview() {
  const remote = storageRemote();
  storagePreview.textContent = remote || "Complete the storage fields";
  storagePreview.classList.toggle("placeholder", !remote);
}

for (const name of ["storage_transport", "storage_host", "storage_namespace", "storage_repository"]) {
  connectForm.elements[name].addEventListener("input", updateStoragePreview);
  connectForm.elements[name].addEventListener("change", updateStoragePreview);
}

function showConnect(project) {
  pendingProject = project;
  connectForm.reset();
  connectForm.elements.path.value = project.path;
  byId("connect-title").textContent = project.name;
  byId("connect-status").textContent = "";
  const create = !project.initialized;
  byId("create-fields").hidden = !create;
  connectForm.elements.project.required = create;
  const storage = project.storage || {};
  connectForm.elements.storage_transport.value = storage.transport || "ssh";
  connectForm.elements.storage_host.value = storage.host || "github.com";
  connectForm.elements.storage_namespace.value = storage.namespace || "";
  connectForm.elements.storage_repository.value = storage.repository || `${project.name}-ticketbunker`;
  for (const name of ["storage_host", "storage_namespace", "storage_repository"]) {
    connectForm.elements[name].required = create;
  }
  updateStoragePreview();
  byId("connect-copy").textContent = create
    ? "Connect the shared storage for this TicketBunker project."
    : "Join the TicketBunker project declared by this checkout. This fetches its companion data without merging source work.";
  byId("connect-submit").textContent = create ? "Create TicketBunker project" : "Join project";
  connectDialog.showModal();
  if (create) connectForm.elements.project.focus();
}

connectForm.addEventListener("submit", async (event) => {
  if (event.submitter?.value !== "default") return;
  event.preventDefault();
  const submit = event.submitter;
  const status = byId("connect-status");
  status.textContent = "";
  setBusy(submit, true);
  const create = !pendingProject.initialized;
  const payload = { path: pendingProject.path };
  if (create) {
    payload.project = connectForm.elements.project.value.toUpperCase();
    payload.storage = {
      backend: "git",
      transport: connectForm.elements.storage_transport.value,
      host: connectForm.elements.storage_host.value,
      namespace: connectForm.elements.storage_namespace.value,
      repository: connectForm.elements.storage_repository.value,
    };
  }
  try {
    await invoke("connect_git_project", { payload });
    connectDialog.close();
  } catch (error) {
    status.textContent = String(error);
    setBusy(submit, false);
  }
});

window.ticketBunkerReload = loadState;
loadState();
