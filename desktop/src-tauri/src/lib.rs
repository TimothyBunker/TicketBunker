use std::env;
use std::ffi::OsString;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

#[cfg(target_os = "linux")]
fn missing_session_bus(address: Option<&str>) -> bool {
    let Some(address) = address else {
        return true;
    };
    let Some(value) = address.strip_prefix("unix:path=") else {
        return false;
    };
    let path = value.split(',').next().unwrap_or_default();
    path.is_empty() || !Path::new(path).exists()
}

#[cfg(target_os = "linux")]
fn configure_settings_backend() {
    if env::var_os("GSETTINGS_BACKEND").is_some() {
        return;
    }
    let address = env::var("DBUS_SESSION_BUS_ADDRESS").ok();
    if missing_session_bus(address.as_deref()) {
        // Minimal Linux desktops can advertise a stale D-Bus socket. GTK's
        // in-memory backend keeps the native picker responsive
        // without asking the user to repair an unrelated desktop service.
        env::set_var("GSETTINGS_BACKEND", "memory");
    }
}

#[derive(Debug, PartialEq, Eq)]
struct LaunchOptions {
    repo: Option<PathBuf>,
    core: Option<PathBuf>,
    route: String,
}

struct ApplicationState {
    core: PathBuf,
    bundle_root: Mutex<Option<PathBuf>>,
    command_worker: Mutex<Option<CoreWorker>>,
    agent_setup: Mutex<Option<AgentSetupProcess>>,
    initial_repo: Option<PathBuf>,
    initial_route: String,
}

struct CoreWorker {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
    next_id: u64,
}

#[derive(Debug)]
enum WorkerError {
    Rejected(String),
    Transport(String),
}

struct AgentSetupProcess {
    child: Child,
    input: ChildStdin,
}

fn parse_options<I>(args: I) -> Result<LaunchOptions, String>
where
    I: IntoIterator<Item = OsString>,
{
    let mut values = args.into_iter();
    let mut repo = None;
    let mut core = None;
    let mut route = "/".to_string();
    while let Some(value) = values.next() {
        match value.to_string_lossy().as_ref() {
            "--repo" => {
                let raw = PathBuf::from(
                    values
                        .next()
                        .ok_or_else(|| "--repo requires a Git project path".to_string())?,
                );
                repo = Some(raw.canonicalize().map_err(|error| {
                    format!("cannot open Git project {}: {error}", raw.display())
                })?);
            }
            "--core" => {
                core = Some(PathBuf::from(values.next().ok_or_else(|| {
                    "--core requires the tb executable path".to_string()
                })?));
            }
            "--route" => {
                route = values
                    .next()
                    .ok_or_else(|| "--route requires an application path".to_string())?
                    .to_string_lossy()
                    .into_owned();
                validate_route(&route)?;
            }
            unknown => return Err(format!("unknown desktop option: {unknown}")),
        }
    }
    Ok(LaunchOptions { repo, core, route })
}

fn validate_route(route: &str) -> Result<(), String> {
    if !route.starts_with('/') || route.starts_with("//") || route.contains("://") {
        return Err("--route must begin with one '/'".to_string());
    }
    Ok(())
}

fn sibling_core() -> Option<PathBuf> {
    let executable = env::current_exe().ok()?;
    let name = if cfg!(windows) { "tb.exe" } else { "tb" };
    let candidate = executable.parent()?.join(name);
    candidate.is_file().then_some(candidate)
}

fn core_executable(explicit: Option<PathBuf>) -> PathBuf {
    explicit
        .or_else(|| env::var_os("TB_DESKTOP_CORE").map(PathBuf::from))
        .or_else(sibling_core)
        .unwrap_or_else(|| PathBuf::from(if cfg!(windows) { "tb.exe" } else { "tb" }))
}

fn launch_id() -> String {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("desktop-{}-{timestamp}", std::process::id())
}

fn bundle_root(state: &ApplicationState) -> Option<PathBuf> {
    state.bundle_root.lock().ok()?.clone()
}

fn stop_agent_setup(state: &ApplicationState) {
    let Ok(mut guarded) = state.agent_setup.lock() else {
        return;
    };
    if let Some(mut process) = guarded.take() {
        let _ = process.child.kill();
        let _ = process.child.wait();
    }
}

fn start_command_worker(state: &ApplicationState) -> Result<CoreWorker, String> {
    let mut command = Command::new(&state.core);
    command
        .arg("_app-worker")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    if let Some(root) = bundle_root(state) {
        command.env("TB_BUNDLE_ROOT", root);
    }
    let mut child = command
        .spawn()
        .map_err(|error| format!("TicketBunker could not start its core: {error}"))?;
    let input = child
        .stdin
        .take()
        .ok_or_else(|| "TicketBunker could not connect to its local core input.".to_string())?;
    let output = child
        .stdout
        .take()
        .ok_or_else(|| "TicketBunker could not connect to its local core output.".to_string())?;
    Ok(CoreWorker {
        child,
        input,
        output: BufReader::new(output),
        next_id: 1,
    })
}

fn stop_command_worker(state: &ApplicationState) {
    let Ok(mut guarded) = state.command_worker.lock() else {
        return;
    };
    if let Some(mut worker) = guarded.take() {
        let _ = worker.child.kill();
        let _ = worker.child.wait();
    }
}

fn send_agent_setup(process: &mut AgentSetupProcess, value: Value) -> Result<(), String> {
    serde_json::to_writer(&mut process.input, &value)
        .map_err(|_| "TicketBunker could not encode the agent setup response.".to_string())?;
    process
        .input
        .write_all(b"\n")
        .and_then(|_| process.input.flush())
        .map_err(|_| "The agent setup helper stopped. Retry setup.".to_string())
}

fn valid_agent_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "-_.".contains(character))
}

fn validated_auth_url(raw: &str) -> Result<tauri::Url, String> {
    let parsed: tauri::Url = raw
        .parse()
        .map_err(|_| "The login service returned an invalid address.".to_string())?;
    if parsed.scheme() != "https" || parsed.host_str().is_none() {
        return Err("TicketBunker only opens secure login addresses.".to_string());
    }
    Ok(parsed)
}

fn validated_release_url(raw: &str) -> Result<tauri::Url, String> {
    let parsed: tauri::Url = raw
        .parse()
        .map_err(|_| "TicketBunker received an invalid release address.".to_string())?;
    let path = parsed.path();
    let official_path = path.starts_with("/TimothyBunker/TicketBunker/releases/tag/")
        || path.starts_with("/TimothyBunker/TicketBunker/releases/download/");
    if parsed.scheme() != "https"
        || parsed.host_str() != Some("github.com")
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.port().is_some_and(|port| port != 443)
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || !official_path
    {
        return Err("TicketBunker only opens its official release downloads.".to_string());
    }
    Ok(parsed)
}

fn relay_agent_output(app: AppHandle, output: impl std::io::Read + Send + 'static) {
    thread::spawn(move || {
        for line in BufReader::new(output).lines().map_while(Result::ok) {
            if let Ok(value) = serde_json::from_str::<Value>(&line) {
                let _ = app.emit_to("launcher", "agent-setup-event", value);
            }
        }
    });
}

fn worker_exchange(
    worker: &mut CoreWorker,
    name: &str,
    payload: &Value,
    project_root: Option<&Path>,
) -> Result<Value, WorkerError> {
    let request_id = worker.next_id;
    worker.next_id = worker.next_id.saturating_add(1);
    let mut request = json!({ "id": request_id, "action": name, "payload": payload });
    if let Some(root) = project_root {
        request["project_root"] = Value::String(root.to_string_lossy().into_owned());
    }
    serde_json::to_writer(&mut worker.input, &request).map_err(|_| {
        WorkerError::Transport("TicketBunker could not encode that local action.".to_string())
    })?;
    worker
        .input
        .write_all(b"\n")
        .and_then(|_| worker.input.flush())
        .map_err(|_| {
            WorkerError::Transport(
                "TicketBunker's local core stopped before receiving that action.".to_string(),
            )
        })?;

    let mut line = String::new();
    worker.output.read_line(&mut line).map_err(|_| {
        WorkerError::Transport("TicketBunker could not read its local core response.".to_string())
    })?;
    if line.is_empty() {
        return Err(WorkerError::Transport(
            "TicketBunker's local core stopped before responding.".to_string(),
        ));
    }
    parse_worker_response(request_id, &line)
}

fn parse_worker_response(request_id: u64, line: &str) -> Result<Value, WorkerError> {
    let response: Value = serde_json::from_str(line).map_err(|_| {
        WorkerError::Transport(
            "TicketBunker received an invalid response from its local core.".to_string(),
        )
    })?;
    if response.get("id").and_then(Value::as_u64) != Some(request_id) {
        return Err(WorkerError::Transport(
            "TicketBunker received an out-of-order local core response.".to_string(),
        ));
    }
    if response.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(WorkerError::Rejected(
            response
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("TicketBunker could not complete that action.")
                .to_string(),
        ));
    }
    Ok(response)
}

fn core_command(state: &ApplicationState, name: &str, payload: Value) -> Result<Value, String> {
    core_command_in(state, name, payload, None)
}

fn core_command_in(
    state: &ApplicationState,
    name: &str,
    payload: Value,
    project_root: Option<&Path>,
) -> Result<Value, String> {
    let mut guarded = state
        .command_worker
        .lock()
        .map_err(|_| "TicketBunker could not access its local core.".to_string())?;
    if guarded.is_none() {
        *guarded = Some(start_command_worker(state)?);
    }
    match worker_exchange(
        guarded.as_mut().expect("worker was started"),
        name,
        &payload,
        project_root,
    ) {
        Ok(response) => Ok(response),
        Err(WorkerError::Rejected(message)) => Err(message),
        Err(WorkerError::Transport(message)) => {
            // The action may have reached the core before the transport broke,
            // so never replay a mutation automatically. The next user action
            // starts a clean worker and the shared state makes the outcome
            // visible without risking a duplicate side effect.
            if let Some(mut failed) = guarded.take() {
                let _ = failed.child.kill();
                let _ = failed.child.wait();
            }
            Err(format!("{message} Retry the action."))
        }
    }
}

fn require_launcher(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "launcher" {
        return Err("This action is available only from the local Bunkers window.".to_string());
    }
    Ok(())
}

fn open_project_window(
    app: &AppHandle,
    state: &ApplicationState,
    repo: &Path,
    route: &str,
) -> Result<(), String> {
    validate_route(route)?;
    stop_agent_setup(state);
    let repo = repo
        .canonicalize()
        .map_err(|error| format!("cannot open Git project {}: {error}", repo.display()))?;
    let launcher = app
        .get_webview_window("launcher")
        .ok_or_else(|| "TicketBunker's application window is not available.".to_string())?;
    app.emit_to(
        "launcher",
        "open-project",
        json!({ "path": repo, "route": route }),
    )
    .map_err(|_| "TicketBunker could not open that project in the app.".to_string())?;
    let _ = launcher.show();
    let _ = launcher.set_focus();
    Ok(())
}

#[tauri::command]
fn desktop_state(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
) -> Result<Value, String> {
    require_launcher(&window)?;
    if let Some(repository) = state.initial_repo.as_ref() {
        core_command(
            &state,
            "application.project.add",
            json!({ "path": repository }),
        )?;
    }
    let mut response = core_command(&state, "application.state", json!({}))?;
    response["launch"] = json!({
        "repository": state.initial_repo.as_ref().map(|value| value.to_string_lossy()),
        "route": state.initial_route,
    });
    Ok(response)
}

#[tauri::command]
fn desktop_command(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    action: String,
    payload: Value,
    project_root: Option<String>,
) -> Result<Value, String> {
    require_launcher(&window)?;
    let root = match project_root {
        Some(value) => Some(
            PathBuf::from(value)
                .canonicalize()
                .map_err(|_| "The selected local project is no longer available.".to_string())?,
        ),
        None => None,
    };
    core_command_in(&state, &action, payload, root.as_deref())
}

#[tauri::command]
fn update_profile(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    payload: Value,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(&state, "application.profile.update", payload)
}

#[tauri::command]
fn start_agent_setup(
    app: AppHandle,
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
) -> Result<Value, String> {
    require_launcher(&window)?;
    stop_agent_setup(&state);
    let prepared = core_command(&state, "application.agent.prepare", json!({}))?;
    let runtime = prepared
        .get("runtime")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .filter(|path| path.is_file())
        .ok_or_else(|| "TicketBunker could not find its verified agent runtime.".to_string())?;
    let extension = prepared
        .get("extension")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .filter(|path| path.is_file())
        .ok_or_else(|| "TicketBunker could not find its agent setup bridge.".to_string())?;
    let mut command = Command::new(runtime);
    command
        .args([
            "--mode",
            "rpc",
            "--no-session",
            "--no-tools",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--extension",
        ])
        .arg(extension)
        .current_dir(env::temp_dir())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command
        .spawn()
        .map_err(|_| "TicketBunker could not start agent setup. Retry setup.".to_string())?;
    let input = child
        .stdin
        .take()
        .ok_or_else(|| "TicketBunker could not connect to agent setup.".to_string())?;
    let output = child
        .stdout
        .take()
        .ok_or_else(|| "TicketBunker could not read agent setup.".to_string())?;
    let errors = child
        .stderr
        .take()
        .ok_or_else(|| "TicketBunker could not monitor agent setup.".to_string())?;
    relay_agent_output(app.clone(), output);
    thread::spawn(move || {
        let has_error = BufReader::new(errors)
            .lines()
            .map_while(Result::ok)
            .any(|line| !line.trim().is_empty());
        if has_error {
            let _ = app.emit_to(
                "launcher",
                "agent-setup-event",
                json!({
                    "type": "ticketbunker_agent_error",
                    "message": "The agent setup helper stopped. Retry setup."
                }),
            );
        }
    });
    let mut process = AgentSetupProcess { child, input };
    if let Err(message) = send_agent_setup(
        &mut process,
        json!({
            "id": launch_id(),
            "type": "prompt",
            "message": "/tb-auth-catalog"
        }),
    ) {
        let _ = process.child.kill();
        let _ = process.child.wait();
        return Err(message);
    }
    let mut guarded = state
        .agent_setup
        .lock()
        .map_err(|_| "TicketBunker could not own agent setup.".to_string())?;
    *guarded = Some(process);
    Ok(prepared.get("agent").cloned().unwrap_or_else(|| json!({})))
}

#[tauri::command]
fn begin_agent_login(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    provider: String,
    method: String,
) -> Result<(), String> {
    require_launcher(&window)?;
    if !valid_agent_identifier(&provider) || !matches!(method.as_str(), "oauth" | "api_key") {
        return Err("Choose a valid provider and login method.".to_string());
    }
    let mut guarded = state
        .agent_setup
        .lock()
        .map_err(|_| "TicketBunker could not access agent setup.".to_string())?;
    let process = guarded
        .as_mut()
        .ok_or_else(|| "Open agent setup again, then retry login.".to_string())?;
    send_agent_setup(
        process,
        json!({
            "id": launch_id(),
            "type": "prompt",
            "message": format!("/tb-auth-login {provider} {method}")
        }),
    )
}

#[tauri::command]
fn answer_agent_prompt(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    request_id: String,
    value: Option<String>,
    cancelled: bool,
) -> Result<(), String> {
    require_launcher(&window)?;
    if !valid_agent_identifier(&request_id) {
        return Err("The agent setup request is no longer valid.".to_string());
    }
    if value.as_ref().is_some_and(|text| text.len() > 32_768) {
        return Err("That agent setup response is too long.".to_string());
    }
    let response = if cancelled {
        json!({ "type": "extension_ui_response", "id": request_id, "cancelled": true })
    } else {
        json!({
            "type": "extension_ui_response",
            "id": request_id,
            "value": value.unwrap_or_default()
        })
    };
    let mut guarded = state
        .agent_setup
        .lock()
        .map_err(|_| "TicketBunker could not access agent setup.".to_string())?;
    let process = guarded
        .as_mut()
        .ok_or_else(|| "Open agent setup again, then retry.".to_string())?;
    send_agent_setup(process, response)
}

#[tauri::command]
fn stop_agent_setup_command(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
) -> Result<(), String> {
    require_launcher(&window)?;
    stop_agent_setup(&state);
    Ok(())
}

#[tauri::command]
fn configure_agent(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    payload: Value,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(&state, "application.agent.configure", payload)
}

#[tauri::command]
fn open_agent_auth_url(app: AppHandle, window: WebviewWindow, url: String) -> Result<(), String> {
    require_launcher(&window)?;
    let parsed = validated_auth_url(&url)?;
    app.opener()
        .open_url(parsed, None::<&str>)
        .map_err(|_| "TicketBunker could not open the login page in your browser.".to_string())
}

#[tauri::command]
fn open_release_url(app: AppHandle, window: WebviewWindow, url: String) -> Result<(), String> {
    require_launcher(&window)?;
    let parsed = validated_release_url(&url)?;
    app.opener()
        .open_url(parsed, None::<&str>)
        .map_err(|_| "TicketBunker could not open the update in your browser.".to_string())
}

#[tauri::command]
fn add_git_project(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    path: String,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(&state, "application.project.add", json!({ "path": path }))
}

#[tauri::command]
fn create_bunker(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    payload: Value,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(&state, "application.bunker.create", payload)
}

#[tauri::command]
fn create_bunker_project(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    payload: Value,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(&state, "application.bunker.project.create", payload)
}

#[tauri::command]
fn inspect_git_repository(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    path: String,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(
        &state,
        "application.repository.inspect",
        json!({ "path": path }),
    )
}

#[tauri::command]
fn attach_git_repository(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    payload: Value,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(&state, "application.bunker.repository.attach", payload)
}

#[tauri::command]
fn configure_bunker_remote(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    payload: Value,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(&state, "application.bunker.remote.configure", payload)
}

#[tauri::command]
fn inspect_bunker_invitation(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    invitation: String,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(
        &state,
        "application.invitation.inspect",
        json!({ "invitation": invitation }),
    )
}

#[tauri::command]
fn join_bunker(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    payload: Value,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(&state, "application.bunker.join", payload)
}

#[tauri::command]
fn import_companion_project(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    payload: Value,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(&state, "application.bunker.import.companion", payload)
}

#[tauri::command]
fn remove_git_project(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    path: String,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(
        &state,
        "application.project.remove",
        json!({ "path": path }),
    )
}

#[tauri::command]
fn connect_git_project(
    app: AppHandle,
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    payload: Value,
) -> Result<(), String> {
    require_launcher(&window)?;
    let response = core_command(&state, "application.project.connect", payload)?;
    let path = response
        .get("project")
        .and_then(|value| value.get("path"))
        .and_then(Value::as_str)
        .ok_or_else(|| "TicketBunker did not return the connected project path.".to_string())?;
    open_project_window(&app, &state, Path::new(path), "/")
}

#[tauri::command]
fn open_git_project(
    app: AppHandle,
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    path: String,
) -> Result<(), String> {
    require_launcher(&window)?;
    let response = core_command(&state, "application.project.add", json!({ "path": path }))?;
    let project = response
        .get("project")
        .ok_or_else(|| "TicketBunker did not return the selected project.".to_string())?;
    if !project
        .get("ready")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Err(
            "Connect this Git project to its TicketBunker data before opening it.".to_string(),
        );
    }
    let path = project
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| "TicketBunker did not return the selected project path.".to_string())?;
    open_project_window(&app, &state, Path::new(path), "/")
}

fn create_launcher(app: &AppHandle) -> Result<(), tauri::Error> {
    WebviewWindowBuilder::new(app, "launcher", WebviewUrl::App("index.html".into()))
        .title("TicketBunker")
        .inner_size(1040.0, 700.0)
        .min_inner_size(720.0, 520.0)
        .on_navigation(|candidate| {
            candidate.scheme() == "tauri" || candidate.host_str() == Some("tauri.localhost")
        })
        .build()?;
    Ok(())
}

#[cfg(desktop)]
fn single_instance_plugin() -> tauri::plugin::TauriPlugin<tauri::Wry> {
    tauri_plugin_single_instance::init(|app, args, _cwd| {
        let options = parse_options(args.into_iter().skip(1).map(OsString::from)).ok();
        if let Some(window) = app.get_webview_window("launcher") {
            if let Some(LaunchOptions {
                repo: Some(repository),
                route,
                ..
            }) = options
            {
                let _ = app.emit_to(
                    "launcher",
                    "open-project",
                    json!({ "path": repository, "route": route }),
                );
            }
            let _ = window.unminimize();
            let _ = window.show();
            let _ = window.set_focus();
        }
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(target_os = "linux")]
    configure_settings_backend();
    let options = parse_options(env::args_os().skip(1)).unwrap_or_else(|message| {
        eprintln!("TicketBunker could not open: {message}");
        std::process::exit(2);
    });
    let state = ApplicationState {
        core: core_executable(options.core),
        bundle_root: Mutex::new(None),
        command_worker: Mutex::new(None),
        agent_setup: Mutex::new(None),
        initial_repo: options.repo,
        initial_route: options.route,
    };

    let mut builder = tauri::Builder::default();
    #[cfg(any(target_os = "windows", target_os = "macos"))]
    {
        builder = builder.plugin(single_instance_plugin());
    }
    #[cfg(target_os = "linux")]
    {
        let address = env::var("DBUS_SESSION_BUS_ADDRESS").ok();
        if !missing_session_bus(address.as_deref()) {
            builder = builder.plugin(single_instance_plugin());
        }
    }

    builder
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            desktop_state,
            desktop_command,
            update_profile,
            start_agent_setup,
            begin_agent_login,
            answer_agent_prompt,
            stop_agent_setup_command,
            configure_agent,
            open_agent_auth_url,
            open_release_url,
            add_git_project,
            create_bunker,
            create_bunker_project,
            inspect_git_repository,
            attach_git_repository,
            configure_bunker_remote,
            inspect_bunker_invitation,
            join_bunker,
            import_companion_project,
            remove_git_project,
            connect_git_project,
            open_git_project
        ])
        .setup(|app| {
            if let Ok(resource_dir) = app.path().resource_dir() {
                if let Ok(mut root) = app.state::<ApplicationState>().bundle_root.lock() {
                    *root = Some(resource_dir);
                }
            }
            create_launcher(app.handle())?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) && window.label() == "launcher" {
                stop_agent_setup(window.state::<ApplicationState>().inner());
                stop_command_worker(window.state::<ApplicationState>().inner());
            }
        })
        .run(tauri::generate_context!())
        .expect("TicketBunker desktop runtime stopped unexpectedly");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn desktop_arguments_keep_the_project_and_core_explicit() {
        let root = env::current_dir().expect("current directory");
        let options = parse_options([
            OsString::from("--repo"),
            root.clone().into_os_string(),
            OsString::from("--core"),
            OsString::from("/opt/ticketbunker/tb"),
            OsString::from("--route"),
            OsString::from("/source?path=ticketbunker%2Fviewer.py&line=42"),
        ])
        .expect("valid desktop options");

        assert_eq!(options.repo, Some(root.canonicalize().unwrap()));
        assert_eq!(options.core, Some(PathBuf::from("/opt/ticketbunker/tb")));
        assert_eq!(
            options.route,
            "/source?path=ticketbunker%2Fviewer.py&line=42"
        );
    }

    #[test]
    fn desktop_can_start_without_a_project_for_the_projects_window() {
        let options = parse_options(Vec::<OsString>::new()).expect("empty desktop options");
        assert_eq!(options.repo, None);
        assert_eq!(options.route, "/");
    }

    #[test]
    fn desktop_rejects_an_unknown_option() {
        let error = parse_options([OsString::from("--surprise")]).unwrap_err();
        assert_eq!(error, "unknown desktop option: --surprise");
    }

    #[test]
    fn desktop_rejects_a_route_that_can_escape_the_bundled_application() {
        let error = parse_options([
            OsString::from("--route"),
            OsString::from("//example.com/not-ticketbunker"),
        ])
        .unwrap_err();
        assert_eq!(error, "--route must begin with one '/'");
    }

    #[test]
    fn agent_setup_identifiers_cannot_inject_rpc_commands() {
        assert!(valid_agent_identifier("openai-codex"));
        assert!(!valid_agent_identifier("openai-codex\n/another-command"));
        assert!(!valid_agent_identifier(""));
    }

    #[test]
    fn agent_login_opens_only_secure_web_addresses() {
        assert!(validated_auth_url("https://auth.openai.com/authorize").is_ok());
        assert!(validated_auth_url("http://auth.openai.com/authorize").is_err());
        assert!(validated_auth_url("javascript:alert(1)").is_err());
    }

    #[test]
    fn release_download_opens_only_ticketbunkers_official_github_path() {
        assert!(validated_release_url(
            "https://github.com/TimothyBunker/TicketBunker/releases/tag/v0.5.0"
        )
        .is_ok());
        assert!(validated_release_url(
            "https://github.com/TimothyBunker/TicketBunker/releases/download/v0.5.0/TicketBunker_0.5.0_x64-setup.exe"
        )
        .is_ok());
        assert!(
            validated_release_url("https://github.com/other/repo/releases/tag/v0.5.0").is_err()
        );
        assert!(validated_release_url(
            "https://example.test/TimothyBunker/TicketBunker/releases/tag/v0.5.0"
        )
        .is_err());
    }

    #[test]
    fn command_worker_requires_matching_ids_and_preserves_rejections() {
        let response =
            parse_worker_response(3, r#"{"id":3,"ok":true,"value":7}"#).expect("matching response");
        assert_eq!(response["value"], 7);

        assert!(matches!(
            parse_worker_response(3, r#"{"id":4,"ok":true}"#),
            Err(WorkerError::Transport(_))
        ));
        match parse_worker_response(3, r#"{"id":3,"ok":false,"message":"Choose."}"#) {
            Err(WorkerError::Rejected(message)) => assert_eq!(message, "Choose."),
            _ => panic!("expected a typed command rejection"),
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn missing_linux_session_bus_uses_the_memory_settings_backend() {
        assert!(missing_session_bus(None));
        assert!(missing_session_bus(Some(
            "unix:path=/ticketbunker/definitely/missing/bus"
        )));
        assert!(!missing_session_bus(Some("unix:abstract=/tmp/dbus-test")));
    }
}
