use std::path::PathBuf;
use std::process::Child;
use std::sync::Mutex;
use tauri::{Manager, RunEvent};

pub struct BackendState {
    pub child: Mutex<Option<Child>>,
}

#[derive(serde::Serialize)]
pub struct BackendInfo {
    pub running: bool,
    pub port: u16,
    pub url: String,
}

pub fn is_backend_running() -> bool {
    use std::net::{SocketAddr, TcpStream};
    use std::time::Duration;

    let addr: SocketAddr = "127.0.0.1:8000".parse().unwrap();
    TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok()
}

pub fn spawn_backend_process() -> Result<Option<Child>, String> {
    if is_backend_running() {
        log::info!("Backend server is already running on 127.0.0.1:8000");
        return Ok(None);
    }

    log::info!("Starting Python backend server for Mono CAD...");

    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()));

    let mut candidates: Vec<PathBuf> = Vec::new();

    if let Some(ref dir) = exe_dir {
        candidates.push(dir.join("mono.exe"));
        candidates.push(dir.join("resources").join("mono.exe"));
        candidates.push(dir.join("bin").join("mono.exe"));
        candidates.push(dir.join(".venv").join("Scripts").join("mono.exe"));
    }

    // Working directory candidates
    candidates.push(PathBuf::from(".venv/Scripts/mono.exe"));
    candidates.push(PathBuf::from("../.venv/Scripts/mono.exe"));
    candidates.push(PathBuf::from(".venv/Scripts/python.exe"));
    candidates.push(PathBuf::from("../.venv/Scripts/python.exe"));

    for candidate in candidates {
        if candidate.exists() {
            log::info!("Found backend candidate at: {:?}", candidate);
            let mut cmd = std::process::Command::new(&candidate);

            if candidate.to_string_lossy().contains("python.exe") {
                cmd.args(["-m", "mono.main"]);
            }

            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                const CREATE_NO_WINDOW: u32 = 0x08000000;
                cmd.creation_flags(CREATE_NO_WINDOW);
            }

            match cmd.spawn() {
                Ok(child) => {
                    log::info!("Successfully spawned backend child process PID: {}", child.id());
                    return Ok(Some(child));
                }
                Err(e) => {
                    log::warn!("Failed to spawn {:?}: {}", candidate, e);
                }
            }
        }
    }

    // Fallbacks via PATH
    log::info!("Attempting PATH fallbacks (uv / mono / python)...");
    let fallbacks: [(&str, &[&str]); 4] = [
        ("uv", &["run", "mono"]),
        ("uv", &["run", "python", "-m", "mono.main"]),
        ("mono", &[]),
        ("python", &["-m", "mono.main"]),
    ];

    for (bin, args) in &fallbacks {
        let mut cmd = std::process::Command::new(bin);
        cmd.args(*args);

        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        if let Ok(child) = cmd.spawn() {
            log::info!("Successfully spawned fallback process '{}' PID: {}", bin, child.id());
            return Ok(Some(child));
        }
    }

    Err("Could not locate or spawn Mono backend process (tested mono.exe, .venv, uv)".to_string())
}

#[tauri::command]
fn get_backend_status() -> BackendInfo {
    let running = is_backend_running();
    BackendInfo {
        running,
        port: 8000,
        url: "http://127.0.0.1:8000".to_string(),
    }
}

#[tauri::command]
fn restart_backend(state: tauri::State<BackendState>) -> Result<BackendInfo, String> {
    if let Ok(mut lock) = state.child.lock() {
        if let Some(mut child) = lock.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    std::thread::sleep(std::time::Duration::from_millis(500));

    let new_child = spawn_backend_process()?;
    if let Ok(mut lock) = state.child.lock() {
        *lock = new_child;
    }

    Ok(get_backend_status())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            match spawn_backend_process() {
                Ok(child) => {
                    app.manage(BackendState {
                        child: Mutex::new(child),
                    });
                }
                Err(err) => {
                    log::warn!("Backend could not be auto-spawned: {}", err);
                    app.manage(BackendState {
                        child: Mutex::new(None),
                    });
                }
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_backend_status,
            restart_backend
        ]);

    let app = builder
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| match event {
        RunEvent::ExitRequested { .. } | RunEvent::Exit => {
            log::info!("Tauri app exiting - shutting down Mono backend child process...");
            if let Some(state) = app_handle.try_state::<BackendState>() {
                if let Ok(mut lock) = state.child.lock() {
                    if let Some(mut child) = lock.take() {
                        log::info!("Terminating child process PID: {}", child.id());
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        }
        _ => {}
    });
}
