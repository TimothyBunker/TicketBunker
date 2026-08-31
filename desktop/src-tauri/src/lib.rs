use std::env;
use std::ffi::OsString;
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

const DEFAULT_PORT: u16 = 8377;
const PORT_ATTEMPTS: u16 = 20;

#[derive(Debug, PartialEq, Eq)]
struct LaunchOptions {
    repo: Option<PathBuf>,
    port: u16,
    core: Option<PathBuf>,
    route: String,
}

struct ApplicationState {
    core: PathBuf,
    preferred_port: u16,
    bundle_root: Mutex<Option<PathBuf>>,
    process: Mutex<Option<Child>>,
}

fn parse_options<I>(args: I) -> Result<LaunchOptions, String>
where
    I: IntoIterator<Item = OsString>,
{
    let mut values = args.into_iter();
    let mut repo = None;
    let mut port = DEFAULT_PORT;
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
            "--port" => {
                let raw = values
                    .next()
                    .ok_or_else(|| "--port requires a number".to_string())?;
                port = raw
                    .to_string_lossy()
                    .parse::<u16>()
                    .map_err(|_| "--port must be between 1 and 65535".to_string())?;
                if port == 0 {
                    return Err("--port must be between 1 and 65535".to_string());
                }
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
    Ok(LaunchOptions {
        repo,
        port,
        core,
        route,
    })
}

fn validate_route(route: &str) -> Result<(), String> {
    if !route.starts_with('/') || route.starts_with("//") || route.contains("://") {
        return Err("--route must begin with one '/'".to_string());
    }
    Ok(())
}

fn available_port(preferred: u16) -> Result<u16, String> {
    for offset in 0..PORT_ATTEMPTS {
        let Some(candidate) = preferred.checked_add(offset) else {
            break;
        };
        let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), candidate);
        if TcpListener::bind(address).is_ok() {
            return Ok(candidate);
        }
    }
    Err(format!(
        "no local TicketBunker port is available from {preferred} through {}",
        preferred.saturating_add(PORT_ATTEMPTS - 1)
    ))
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

fn start_core(state: &ApplicationState, repo: &Path, port: u16) -> Result<Child, String> {
    let mut command = Command::new(&state.core);
    command
        .arg("_viewer-worker")
        .arg("--repo")
        .arg(repo)
        .arg("--port")
        .arg(port.to_string())
        .arg(format!("--launch={}", launch_id()))
        .current_dir(repo)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Some(root) = bundle_root(state) {
        command.env("TB_BUNDLE_ROOT", root);
    }
    command.spawn().map_err(|error| {
        format!(
            "cannot start the TicketBunker core at {}: {error}",
            state.core.display()
        )
    })
}

fn wait_until_ready(child: &mut Child, port: u16) -> Result<(), String> {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port);
    for _ in 0..100 {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("cannot inspect the TicketBunker core: {error}"))?
        {
            return Err(format!(
                "the TicketBunker core stopped during startup ({status})"
            ));
        }
        if TcpStream::connect_timeout(&address, Duration::from_millis(50)).is_ok() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    Err("the TicketBunker core did not become ready within five seconds".to_string())
}

fn stop_core(state: &ApplicationState) {
    let Ok(mut guarded) = state.process.lock() else {
        return;
    };
    if let Some(mut child) = guarded.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn core_command(state: &ApplicationState, name: &str, payload: Value) -> Result<Value, String> {
    let mut command = Command::new(&state.core);
    command
        .arg("_app-command")
        .arg(name)
        .arg("--payload")
        .arg(payload.to_string())
        .stdin(Stdio::null());
    if let Some(root) = bundle_root(state) {
        command.env("TB_BUNDLE_ROOT", root);
    }
    let output = command
        .output()
        .map_err(|error| format!("TicketBunker could not start its core: {error}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr)
            .trim()
            .trim_start_matches("error:")
            .trim()
            .to_string();
        return Err(if detail.is_empty() {
            "TicketBunker could not complete that action.".to_string()
        } else {
            detail
        });
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|_| "TicketBunker received an invalid response from its core.".to_string())
}

fn require_launcher(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "launcher" {
        return Err("This action is available only from the local Projects window.".to_string());
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
    let repo = repo
        .canonicalize()
        .map_err(|error| format!("cannot open Git project {}: {error}", repo.display()))?;
    if app.get_webview_window("project").is_some() {
        return Err("Close the open project before opening another one.".to_string());
    }
    let port = available_port(state.preferred_port)?;
    let mut core = start_core(state, &repo, port)?;
    if let Err(message) = wait_until_ready(&mut core, port) {
        let _ = core.kill();
        let _ = core.wait();
        return Err(message);
    }
    let url: tauri::Url = format!("http://127.0.0.1:{port}{route}")
        .parse()
        .map_err(|_| "TicketBunker could not build its local project URL.".to_string())?;
    let window = WebviewWindowBuilder::new(app, "project", WebviewUrl::External(url))
        .title(format!(
            "TicketBunker · {}",
            repo.file_name().unwrap_or_default().to_string_lossy()
        ))
        .inner_size(1280.0, 820.0)
        .min_inner_size(760.0, 560.0)
        .zoom_hotkeys_enabled(true)
        .on_navigation(move |candidate| {
            candidate.scheme() == "http"
                && candidate.host_str() == Some("127.0.0.1")
                && candidate.port() == Some(port)
        })
        .build()
        .map_err(|error| {
            let _ = core.kill();
            let _ = core.wait();
            format!("TicketBunker could not create its project window: {error}")
        })?;
    let mut guarded = state
        .process
        .lock()
        .map_err(|_| "TicketBunker could not own its local core process.".to_string())?;
    *guarded = Some(core);
    drop(guarded);
    if let Some(launcher) = app.get_webview_window("launcher") {
        let _ = launcher.hide();
    }
    let _ = window.set_focus();
    Ok(())
}

#[tauri::command]
fn desktop_state(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(&state, "application.state", json!({}))
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
fn add_git_project(
    window: WebviewWindow,
    state: tauri::State<ApplicationState>,
    path: String,
) -> Result<Value, String> {
    require_launcher(&window)?;
    core_command(&state, "application.project.add", json!({ "path": path }))
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let options = parse_options(env::args_os().skip(1)).unwrap_or_else(|message| {
        eprintln!("TicketBunker could not open: {message}");
        std::process::exit(2);
    });
    let initial_repo = options.repo.clone();
    let initial_route = options.route.clone();
    let state = ApplicationState {
        core: core_executable(options.core),
        preferred_port: options.port,
        bundle_root: Mutex::new(None),
        process: Mutex::new(None),
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            desktop_state,
            update_profile,
            add_git_project,
            remove_git_project,
            connect_git_project,
            open_git_project
        ])
        .setup(move |app| {
            if let Ok(resource_dir) = app.path().resource_dir() {
                if let Ok(mut root) = app.state::<ApplicationState>().bundle_root.lock() {
                    *root = Some(resource_dir);
                }
            }
            if let Some(repo) = initial_repo.as_deref() {
                open_project_window(
                    app.handle(),
                    app.state::<ApplicationState>().inner(),
                    repo,
                    &initial_route,
                )
                .map_err(std::io::Error::other)?;
            } else {
                create_launcher(app.handle())?;
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) && window.label() == "project" {
                stop_core(window.state::<ApplicationState>().inner());
                if let Some(launcher) = window.app_handle().get_webview_window("launcher") {
                    let _ = launcher.show();
                    let _ = launcher.set_focus();
                    let _ = launcher.eval("window.ticketBunkerReload?.()");
                }
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
            OsString::from("--port"),
            OsString::from("18499"),
            OsString::from("--core"),
            OsString::from("/opt/ticketbunker/tb"),
            OsString::from("--route"),
            OsString::from("/source?path=ticketbunker%2Fviewer.py&line=42"),
        ])
        .expect("valid desktop options");

        assert_eq!(options.repo, Some(root.canonicalize().unwrap()));
        assert_eq!(options.port, 18499);
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
    fn desktop_rejects_a_route_that_can_escape_the_loopback_origin() {
        let error = parse_options([
            OsString::from("--route"),
            OsString::from("//example.com/not-ticketbunker"),
        ])
        .unwrap_err();
        assert_eq!(error, "--route must begin with one '/'");
    }

    #[test]
    fn port_selection_moves_past_an_occupied_loopback_port() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("temporary listener");
        let occupied = listener.local_addr().unwrap().port();
        if occupied == u16::MAX {
            return;
        }
        let selected = available_port(occupied).expect("nearby port");
        assert_ne!(selected, occupied);
    }
}
