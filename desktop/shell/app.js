const invoke = window.__TAURI__.core.invoke;
const chooseDirectory = window.__TAURI__.dialog.open;
const listen = window.__TAURI__.event.listen;

let applicationState = null;
let pendingRepository = null;
let reviewedInvitation = "";
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
const bunkerAction = byId("bunker-action");
const createBunkerForm = byId("create-bunker-form");
const joinBunkerForm = byId("join-bunker-form");
const attachDialog = byId("attach-dialog");
const attachForm = byId("attach-form");
const projectDialog = byId("project-dialog");
const projectForm = byId("project-form");
const remoteDialog = byId("remote-dialog");
const remoteForm = byId("remote-form");
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

function flattenProjects(projects, depth = 0) {
  const values = [];
  for (const project of projects || []) {
    values.push({ project, depth });
    values.push(...flattenProjects(project.children, depth + 1));
  }
  return values;
}

function actionButton(label, handler, className = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  if (className) button.className = className;
  button.addEventListener("click", handler);
  return button;
}

function remoteLabel(bunker) {
  if (!bunker.remote) return "Local only";
  try {
    const host = new URL(bunker.remote.server_url).host;
    return bunker.remote.state === "ready" ? `Connected to ${host}` : `${host} · authentication required`;
  } catch (_error) {
    return "Remote connection needs attention";
  }
}

function projectTree(bunker, projects) {
  const list = document.createElement("ul");
  list.className = "project-tree";
  for (const project of projects || []) {
    const item = document.createElement("li");
    const line = document.createElement("div");
    line.className = "project-line";
    const identity = document.createElement("div");
    identity.className = "project-identity";
    const key = document.createElement("span");
    key.className = "project-key";
    key.textContent = project.key;
    const name = document.createElement("strong");
    name.textContent = project.name;
    identity.append(key, name);
    const repositories = (bunker.repositories || []).filter((value) => value.project_id === project.id);
    for (const repository of repositories) {
      const repo = document.createElement("span");
      repo.className = "repository-name";
      repo.textContent = repository.name;
      repo.title = repository.local_path || repository.canonical_remote || "Repository binding";
      identity.append(repo);
    }
    const actions = document.createElement("div");
    actions.className = "project-inline-actions";
    actions.append(
      actionButton("Add subproject", () => showProjectForm(bunker, project.id)),
      actionButton("Attach repository", () => chooseRepository(bunker, project.id)),
    );
    line.append(identity, actions);
    item.append(line);
    if (project.children?.length) item.append(projectTree(bunker, project.children));
    list.append(item);
  }
  return list;
}

function renderLegacyProjects() {
  const section = byId("legacy-projects");
  const list = byId("legacy-project-list");
  const projects = (applicationState.legacy_projects || []).filter((project) => project.initialized && !project.missing);
  section.hidden = projects.length === 0;
  list.replaceChildren();
  for (const project of projects) {
    const row = document.createElement("div");
    row.className = "legacy-project-row";
    const copy = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = project.project ? `${project.project} · ${project.name}` : project.name;
    const path = document.createElement("span");
    path.textContent = project.path;
    copy.append(name, path);
    const importButton = actionButton(
      project.imported ? "Imported" : "Import copy",
      (event) => importLegacyProject(project, event.currentTarget),
    );
    importButton.disabled = Boolean(project.imported);
    row.append(copy, importButton);
    list.append(row);
  }
}

function renderBunkers() {
  projectList.replaceChildren();
  const bunkers = applicationState.bunkers || [];
  emptyState.hidden = bunkers.length !== 0;
  projectList.hidden = bunkers.length === 0;
  for (const bunker of bunkers) {
    const row = document.createElement("article");
    row.className = "bunker-row";
    const header = document.createElement("header");
    const title = document.createElement("div");
    const name = document.createElement("h2");
    name.textContent = bunker.name;
    const state = document.createElement("span");
    state.className = `bunker-state ${bunker.remote?.state === "ready" ? "ready" : ""}`;
    state.textContent = remoteLabel(bunker);
    title.append(name, state);
    const actions = document.createElement("div");
    actions.className = "bunker-actions";
    actions.append(
      actionButton("Add project", () => showProjectForm(bunker)),
      actionButton(bunker.remote ? "Remote settings" : "Connect service", () => showRemoteForm(bunker)),
    );
    header.append(title, actions);
    const summary = document.createElement("p");
    summary.className = "bunker-summary";
    summary.textContent = `${bunker.project_count} ${bunker.project_count === 1 ? "project" : "projects"} · ${bunker.repository_count} ${bunker.repository_count === 1 ? "repository" : "repositories"} · ${bunker.ticket_count} ${bunker.ticket_count === 1 ? "ticket" : "tickets"}`;
    const details = document.createElement("details");
    details.className = "bunker-storage";
    const detailsLabel = document.createElement("summary");
    detailsLabel.textContent = "Storage relationship";
    const local = document.createElement("p");
    local.textContent = `Local database: ${bunker.database}`;
    details.append(detailsLabel, local);
    if (bunker.remote) {
      const remote = document.createElement("p");
      remote.textContent = `Remote service: ${bunker.remote.server_url} / ${bunker.remote.remote_bunker_id}`;
      details.append(remote);
    }
    row.append(header, summary, projectTree(bunker, bunker.projects), details);
    projectList.append(row);
  }
  renderLegacyProjects();
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
  renderBunkers();
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

function showBunkerAction(mode) {
  const creating = mode === "create";
  bunkerAction.hidden = false;
  createBunkerForm.hidden = !creating;
  joinBunkerForm.hidden = creating;
  byId("action-eyebrow").textContent = creating ? "New Bunker" : "Existing Bunker";
  byId("action-title").textContent = creating ? "Create a local Bunker" : "Join with an invitation";
  reviewedInvitation = "";
  byId("join-review").hidden = true;
  byId("confirm-join").hidden = true;
  byId("review-invitation").hidden = false;
  const form = creating ? createBunkerForm : joinBunkerForm;
  form.querySelector("output").textContent = "";
  requestAnimationFrame(() => form.querySelector("input")?.focus());
}

function hideBunkerAction() {
  bunkerAction.hidden = true;
  reviewedInvitation = "";
  createBunkerForm.reset();
  joinBunkerForm.reset();
}

byId("create-bunker").addEventListener("click", () => showBunkerAction("create"));
byId("empty-create-bunker").addEventListener("click", () => showBunkerAction("create"));
byId("join-bunker").addEventListener("click", () => showBunkerAction("join"));
byId("empty-join-bunker").addEventListener("click", () => showBunkerAction("join"));
byId("cancel-bunker-action").addEventListener("click", hideBunkerAction);

createBunkerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  const status = event.currentTarget.querySelector("output");
  status.textContent = "";
  setBusy(button, true);
  try {
    const result = await invoke("create_bunker", {
      payload: {
        name: event.currentTarget.elements.name.value,
        project_key: event.currentTarget.elements.project_key.value.toUpperCase(),
        project_name: event.currentTarget.elements.project_name.value,
      },
    });
    applicationState.bunkers = result.bunkers;
    hideBunkerAction();
    renderBunkers();
    message(`Created ${result.bunker.name}`);
  } catch (error) {
    status.textContent = String(error);
  } finally {
    setBusy(button, false);
  }
});

joinBunkerForm.elements.invitation.addEventListener("input", () => {
  reviewedInvitation = "";
  byId("join-review").hidden = true;
  byId("confirm-join").hidden = true;
  byId("review-invitation").hidden = false;
});

joinBunkerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  const status = event.currentTarget.querySelector("output");
  const invitation = event.currentTarget.elements.invitation.value.trim();
  status.textContent = "";
  setBusy(button, true);
  try {
    const result = await invoke("inspect_bunker_invitation", { invitation });
    reviewedInvitation = invitation;
    byId("join-server").textContent = result.invitation.server;
    byId("join-id").textContent = result.invitation.bunker_id;
    byId("join-review").hidden = false;
    byId("confirm-join").hidden = false;
    button.hidden = true;
  } catch (error) {
    status.textContent = String(error);
  } finally {
    setBusy(button, false);
  }
});

byId("confirm-join").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const status = joinBunkerForm.querySelector("output");
  status.textContent = "";
  if (!reviewedInvitation) return;
  setBusy(button, true);
  try {
    const result = await invoke("join_bunker", { payload: { invitation: reviewedInvitation } });
    applicationState.bunkers = result.bunkers;
    hideBunkerAction();
    renderBunkers();
    message(`Joined ${result.bunker.name}`);
  } catch (error) {
    status.textContent = String(error);
  } finally {
    setBusy(button, false);
  }
});

function projectOptions(select, bunker, selected = "", includeTopLevel = false) {
  select.replaceChildren();
  if (includeTopLevel) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Top level";
    select.append(option);
  }
  for (const { project, depth } of flattenProjects(bunker.projects)) {
    const option = document.createElement("option");
    option.value = project.id;
    option.textContent = `${"\u00a0\u00a0".repeat(depth)}${project.key} · ${project.name}`;
    select.append(option);
  }
  if (selected) select.value = selected;
}

async function chooseRepository(bunker, projectId) {
  try {
    const selected = await chooseDirectory({ directory: true, multiple: false, title: "Attach Git repository" });
    if (!selected) return;
    const result = await invoke("inspect_git_repository", { path: selected });
    pendingRepository = result.repository;
    attachForm.reset();
    attachForm.elements.bunker_id.value = bunker.id;
    attachForm.elements.path.value = result.repository.path;
    projectOptions(attachForm.elements.project_id, bunker, projectId);
    byId("attach-repository-name").textContent = result.repository.name;
    byId("attach-repository-path").textContent = result.repository.path;
    byId("attach-repository-remote").textContent = result.repository.canonical_remote || "No shared Git remote detected";
    byId("attach-status").textContent = "";
    attachDialog.showModal();
  } catch (error) {
    message(String(error), true);
  }
}

attachForm.addEventListener("submit", async (event) => {
  if (event.submitter?.value !== "default") return;
  event.preventDefault();
  const button = event.submitter;
  const status = byId("attach-status");
  status.textContent = "";
  setBusy(button, true);
  try {
    const result = await invoke("attach_git_repository", {
      payload: {
        bunker_id: event.currentTarget.elements.bunker_id.value,
        project_id: event.currentTarget.elements.project_id.value,
        path: event.currentTarget.elements.path.value,
        name: pendingRepository?.name || "",
      },
    });
    applicationState.bunkers = result.bunkers;
    attachDialog.close();
    renderBunkers();
    message(`Attached ${pendingRepository?.name || "repository"}`);
  } catch (error) {
    status.textContent = String(error);
  } finally {
    setBusy(button, false);
  }
});

function showProjectForm(bunker, parentId = "") {
  projectForm.reset();
  projectForm.elements.bunker_id.value = bunker.id;
  projectOptions(projectForm.elements.parent_id, bunker, parentId, true);
  byId("project-status").textContent = "";
  projectDialog.showModal();
  projectForm.elements.key.focus();
}

projectForm.addEventListener("submit", async (event) => {
  if (event.submitter?.value !== "default") return;
  event.preventDefault();
  const button = event.submitter;
  const status = byId("project-status");
  status.textContent = "";
  setBusy(button, true);
  try {
    const result = await invoke("create_bunker_project", {
      payload: {
        bunker_id: event.currentTarget.elements.bunker_id.value,
        parent_id: event.currentTarget.elements.parent_id.value,
        key: event.currentTarget.elements.key.value.toUpperCase(),
        name: event.currentTarget.elements.name.value,
      },
    });
    applicationState.bunkers = result.bunkers;
    projectDialog.close();
    renderBunkers();
  } catch (error) {
    status.textContent = String(error);
  } finally {
    setBusy(button, false);
  }
});

function showRemoteForm(bunker) {
  remoteForm.reset();
  remoteForm.elements.bunker_id.value = bunker.id;
  remoteForm.elements.server_url.value = bunker.remote?.server_url || "";
  remoteForm.elements.remote_bunker_id.value = bunker.remote?.remote_bunker_id || "";
  byId("remote-status").textContent = "";
  remoteDialog.showModal();
  remoteForm.elements.server_url.focus();
}

remoteForm.addEventListener("submit", async (event) => {
  if (event.submitter?.value !== "default") return;
  event.preventDefault();
  const button = event.submitter;
  const status = byId("remote-status");
  status.textContent = "";
  setBusy(button, true);
  try {
    const result = await invoke("configure_bunker_remote", {
      payload: {
        bunker_id: event.currentTarget.elements.bunker_id.value,
        server_url: event.currentTarget.elements.server_url.value,
        remote_bunker_id: event.currentTarget.elements.remote_bunker_id.value,
      },
    });
    applicationState.bunkers = result.bunkers;
    remoteDialog.close();
    renderBunkers();
    message("Remote relationship saved. Authenticate before synchronization.");
  } catch (error) {
    status.textContent = String(error);
  } finally {
    setBusy(button, false);
  }
});

async function importLegacyProject(project, button) {
  setBusy(button, true);
  try {
    const result = await invoke("import_companion_project", { payload: { path: project.path } });
    applicationState.bunkers = result.bunkers;
    renderBunkers();
    message(`Imported ${result.migration.tickets.length} tickets. The companion data was not changed.`);
  } catch (error) {
    message(String(error), true);
    setBusy(button, false);
  }
}

window.ticketBunkerReload = loadState;
loadState();
