use std::env;
use std::ffi::OsString;
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

const DEFAULT_PORT: u16 = 8377;
const PORT_ATTEMPTS: u16 = 20;

#[derive(Debug, PartialEq, Eq)]
struct LaunchOptions {
    repo: PathBuf,
    port: u16,
    core: Option<PathBuf>,
    route: String,
}

struct CoreProcess(Mutex<Option<Child>>);

fn parse_options<I>(args: I, current_dir: &Path) -> Result<LaunchOptions, String>
where
    I: IntoIterator<Item = OsString>,
{
    let mut values = args.into_iter();
    let mut repo = current_dir.to_path_buf();
    let mut port = DEFAULT_PORT;
    let mut core = None;
    let mut route = "/".to_string();
    while let Some(value) = values.next() {
        match value.to_string_lossy().as_ref() {
            "--repo" => {
                repo = PathBuf::from(
                    values
                        .next()
                        .ok_or_else(|| "--repo requires a Git project path".to_string())?,
                );
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
                if !route.starts_with('/') || route.starts_with("//") || route.contains("://") {
                    return Err("--route must begin with one '/'".to_string());
                }
            }
            unknown => return Err(format!("unknown desktop option: {unknown}")),
        }
    }
    Ok(LaunchOptions {
        repo: repo
            .canonicalize()
            .map_err(|error| format!("cannot open Git project {}: {error}", repo.display()))?,
        port,
        core,
        route,
    })
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

fn core_executable(options: &LaunchOptions) -> PathBuf {
    options
        .core
        .clone()
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

fn start_core(options: &LaunchOptions, port: u16) -> Result<Child, String> {
    let executable = core_executable(options);
    Command::new(&executable)
        .arg("_viewer-worker")
        .arg("--repo")
        .arg(&options.repo)
        .arg("--port")
        .arg(port.to_string())
        .arg(format!("--launch={}", launch_id()))
        .current_dir(&options.repo)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| {
            format!(
                "cannot start the TicketBunker core at {}: {error}",
                executable.display()
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

fn stop_core(process: &CoreProcess) {
    let Ok(mut guarded) = process.0.lock() else {
        return;
    };
    if let Some(mut child) = guarded.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let current_dir = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let options = parse_options(env::args_os().skip(1), &current_dir).unwrap_or_else(|message| {
        eprintln!("TicketBunker could not open: {message}");
        std::process::exit(2);
    });
    let port = available_port(options.port).unwrap_or_else(|message| {
        eprintln!("TicketBunker could not open: {message}");
        std::process::exit(2);
    });
    let mut core = start_core(&options, port).unwrap_or_else(|message| {
        eprintln!("TicketBunker could not open: {message}");
        std::process::exit(2);
    });
    if let Err(message) = wait_until_ready(&mut core, port) {
        let _ = core.kill();
        let _ = core.wait();
        eprintln!("TicketBunker could not open: {message}");
        std::process::exit(2);
    }
    let url: tauri::Url = format!("http://127.0.0.1:{port}{}", options.route)
        .parse()
        .expect("the loopback viewer URL is valid");

    tauri::Builder::default()
        .manage(CoreProcess(Mutex::new(Some(core))))
        .setup(move |app| {
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.clone()))
                .title("TicketBunker")
                .inner_size(1280.0, 820.0)
                .min_inner_size(760.0, 560.0)
                .zoom_hotkeys_enabled(true)
                .on_navigation(move |candidate| {
                    candidate.scheme() == "http"
                        && candidate.host_str() == Some("127.0.0.1")
                        && candidate.port() == Some(port)
                })
                .build()?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                stop_core(&window.state::<CoreProcess>());
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
        let options = parse_options(
            [
                OsString::from("--repo"),
                root.clone().into_os_string(),
                OsString::from("--port"),
                OsString::from("18499"),
                OsString::from("--core"),
                OsString::from("/opt/ticketbunker/tb"),
                OsString::from("--route"),
                OsString::from("/source?path=ticketbunker%2Fviewer.py&line=42"),
            ],
            &root,
        )
        .expect("valid desktop options");

        assert_eq!(options.repo, root.canonicalize().unwrap());
        assert_eq!(options.port, 18499);
        assert_eq!(options.core, Some(PathBuf::from("/opt/ticketbunker/tb")));
        assert_eq!(
            options.route,
            "/source?path=ticketbunker%2Fviewer.py&line=42"
        );
    }

    #[test]
    fn desktop_rejects_an_unknown_option() {
        let root = env::current_dir().expect("current directory");
        let error = parse_options([OsString::from("--surprise")], &root).unwrap_err();
        assert_eq!(error, "unknown desktop option: --surprise");
    }

    #[test]
    fn desktop_rejects_a_route_that_can_escape_the_loopback_origin() {
        let root = env::current_dir().expect("current directory");
        let error = parse_options(
            [
                OsString::from("--route"),
                OsString::from("//example.com/not-ticketbunker"),
            ],
            &root,
        )
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
