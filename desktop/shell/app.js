const invoke = window.__TAURI__.core.invoke;
const chooseDirectory = window.__TAURI__.dialog.open;

let applicationState = null;
let pendingProject = null;

const byId = (id) => document.getElementById(id);
const loading = byId("loading-state");
const onboarding = byId("onboarding");
const projectsView = byId("projects-view");
const identityButton = byId("identity-button");
const projectList = byId("project-list");
const emptyState = byId("empty-state");
const connectDialog = byId("connect-dialog");
const connectForm = byId("connect-form");
const settingsDialog = byId("settings-dialog");
const settingsForm = byId("settings-form");
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
  await saveProfile(event.currentTarget, event.submitter, event.currentTarget.querySelector("output"));
});

identityButton.addEventListener("click", () => {
  fillProfile(settingsForm, applicationState.profile);
  const agent = applicationState.agent;
  byId("agent-status").textContent = agent.configured ? `${agent.model} · ${agent.provider}` : agent.enabled ? "Not set up" : "Off on this computer";
  byId("settings-status").textContent = "";
  settingsDialog.showModal();
});

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

function showConnect(project) {
  pendingProject = project;
  connectForm.reset();
  connectForm.elements.path.value = project.path;
  byId("connect-title").textContent = project.name;
  byId("connect-status").textContent = "";
  const create = !project.initialized;
  byId("create-fields").hidden = !create;
  connectForm.elements.project.required = create;
  connectForm.elements.bunker_repo.required = create;
  byId("connect-copy").textContent = create
    ? "Link this checkout to TicketBunker storage. TicketBunker will not create or host the repository."
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
    payload.bunker_repo = connectForm.elements.bunker_repo.value;
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
