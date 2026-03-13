use std::{
    io::{BufRead, BufReader, Read},
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{Emitter, Manager};

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScanRequest {
    username: Option<String>,
    mode: String,
    sort_mode: String,
    limit: Option<u32>,
    stats_only: bool,
    background_scan: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct BridgeEnvelope {
    #[serde(rename = "type")]
    kind: String,
    payload: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ScanProgressPayload {
    phase: String,
    message: String,
    progress: Option<u64>,
}

#[derive(Debug)]
struct BridgeInvocation {
    python_bin: PathBuf,
    bridge_script: PathBuf,
    working_dir: PathBuf,
    session_dir: PathBuf,
    browsers_path: Option<PathBuf>,
}

#[tauri::command]
fn get_session_status(app: tauri::AppHandle) -> Result<Value, String> {
    run_bridge_json_command(&app, "session-status", |command, invocation| {
        command.arg("--session-dir").arg(&invocation.session_dir);
    })
}

#[tauri::command]
fn connect_instagram(app: tauri::AppHandle, username: Option<String>) -> Result<Value, String> {
    start_bridge_login_command(&app, username)?;
    Ok(serde_json::json!({
        "started": true,
        "message": "Instagram login browser launched."
    }))
}

#[tauri::command]
fn resolve_session_identity(app: tauri::AppHandle) -> Result<Value, String> {
    run_bridge_json_command(&app, "resolve-identity", |command, invocation| {
        command.arg("--session-dir").arg(&invocation.session_dir);
    })
}

#[tauri::command]
fn disconnect_instagram(app: tauri::AppHandle) -> Result<Value, String> {
    run_bridge_json_command(&app, "disconnect", |command, invocation| {
        command.arg("--session-dir").arg(&invocation.session_dir);
    })
}

#[tauri::command]
fn cleanup_instagram_login_processes(app: tauri::AppHandle) -> Result<Value, String> {
    let invocation = resolve_bridge_invocation(&app)?;
    cleanup_login_processes_for_session(&invocation.session_dir)?;
    Ok(serde_json::json!({
        "cleaned": true
    }))
}

#[tauri::command]
async fn run_live_scan(app: tauri::AppHandle, request: ScanRequest) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || run_bridge_scan_command(&app, request))
        .await
        .map_err(|error| error.to_string())?
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            get_session_status,
            connect_instagram,
            resolve_session_identity,
            disconnect_instagram,
            cleanup_instagram_login_processes,
            run_live_scan,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Instagram Followback desktop app");
}

fn run_bridge_json_command<F>(
    app: &tauri::AppHandle,
    subcommand: &str,
    configure: F,
) -> Result<Value, String>
where
    F: FnOnce(&mut Command, &BridgeInvocation),
{
    let invocation = resolve_bridge_invocation(app)?;
    let mut command = Command::new(&invocation.python_bin);
    command.arg(&invocation.bridge_script).arg(subcommand);
    command.current_dir(&invocation.working_dir);
    command.stdin(Stdio::null());
    command.stdout(Stdio::piped());
    command.stderr(Stdio::piped());
    apply_bridge_environment(&mut command, &invocation);
    configure(&mut command, &invocation);

    let output = command.output().map_err(|error| {
        format!(
            "Failed to launch the desktop Python bridge with {}: {error}",
            invocation.python_bin.display()
        )
    })?;

    if !output.status.success() {
        return Err(decode_stderr(&output.stderr).unwrap_or_else(|| {
            format!(
                "The desktop Python bridge exited with status {}.",
                output.status
            )
        }));
    }

    let stdout = String::from_utf8(output.stdout)
        .map_err(|_| "The desktop Python bridge returned invalid UTF-8.".to_string())?;
    let line = stdout
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())
        .ok_or_else(|| "The desktop Python bridge returned no JSON payload.".to_string())?;
    let envelope: BridgeEnvelope = serde_json::from_str(line)
        .map_err(|error| format!("The desktop Python bridge returned invalid JSON: {error}"))?;
    Ok(envelope.payload)
}

fn run_bridge_scan_command(app: &tauri::AppHandle, request: ScanRequest) -> Result<Value, String> {
    let invocation = resolve_bridge_invocation(app)?;
    let mut command = Command::new(&invocation.python_bin);
    command.arg(&invocation.bridge_script).arg("scan");
    command.current_dir(&invocation.working_dir);
    command.stdin(Stdio::null());
    command.stdout(Stdio::piped());
    command.stderr(Stdio::piped());
    apply_bridge_environment(&mut command, &invocation);

    command.arg("--session-dir").arg(&invocation.session_dir);
    command.arg("--mode").arg(&request.mode);
    command.arg("--sort").arg(&request.sort_mode);
    if let Some(limit) = request.limit {
        command.arg("--limit").arg(limit.to_string());
    }
    if request.stats_only {
        command.arg("--stats-only");
    }
    if request.background_scan {
        command.arg("--headless");
    }
    if let Some(username) = request
        .username
        .as_ref()
        .filter(|value| !value.trim().is_empty())
    {
        command.arg("--username").arg(username.trim());
    }

    let mut child = command.spawn().map_err(|error| {
        format!(
            "Failed to launch the live scan bridge with {}: {error}",
            invocation.python_bin.display()
        )
    })?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "The desktop Python bridge did not expose stdout.".to_string())?;
    let mut stderr = child
        .stderr
        .take()
        .ok_or_else(|| "The desktop Python bridge did not expose stderr.".to_string())?;

    let stderr_handle = std::thread::spawn(move || {
        let mut buffer = String::new();
        let _ = stderr.read_to_string(&mut buffer);
        buffer
    });

    let mut final_payload: Option<Value> = None;
    let mut raw_stdout_lines: Vec<String> = Vec::new();

    for line in BufReader::new(stdout).lines() {
        let line = line.map_err(|error| format!("Failed to read scan output: {error}"))?;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        match serde_json::from_str::<BridgeEnvelope>(trimmed) {
            Ok(envelope) if envelope.kind == "progress" => {
                let payload: ScanProgressPayload = serde_json::from_value(envelope.payload)
                    .map_err(|error| format!("Invalid scan progress payload: {error}"))?;
                app.emit("scan-progress", payload)
                    .map_err(|error| format!("Failed to emit scan progress event: {error}"))?;
            }
            Ok(envelope) if envelope.kind == "result" => {
                final_payload = Some(envelope.payload);
            }
            Ok(_) => {}
            Err(_) => raw_stdout_lines.push(trimmed.to_string()),
        }
    }

    let status = child
        .wait()
        .map_err(|error| format!("Failed to wait for live scan: {error}"))?;
    let stderr_output = stderr_handle.join().unwrap_or_default();

    if !status.success() {
        let message = if !stderr_output.trim().is_empty() {
            stderr_output.trim().to_string()
        } else if !raw_stdout_lines.is_empty() {
            raw_stdout_lines.join("\n")
        } else {
            format!("The live scan bridge exited with status {status}.")
        };
        return Err(message);
    }

    final_payload.ok_or_else(|| {
        if !raw_stdout_lines.is_empty() {
            format!(
                "The live scan finished without returning a report. Output:\n{}",
                raw_stdout_lines.join("\n")
            )
        } else {
            "The live scan finished without returning a report.".to_string()
        }
    })
}

fn start_bridge_login_command(
    app: &tauri::AppHandle,
    username: Option<String>,
) -> Result<(), String> {
    let invocation = resolve_bridge_invocation(app)?;
    let mut command = Command::new(&invocation.python_bin);
    command.arg(&invocation.bridge_script).arg("login");
    command.current_dir(&invocation.working_dir);
    command.stdin(Stdio::null());
    command.stdout(Stdio::null());
    command.stderr(Stdio::null());
    apply_bridge_environment(&mut command, &invocation);
    command.arg("--session-dir").arg(&invocation.session_dir);
    if let Some(username) = username.as_ref().filter(|value| !value.trim().is_empty()) {
        command.arg("--username").arg(username.trim());
    }

    command.spawn().map_err(|error| {
        format!(
            "Failed to launch the Instagram login browser with {}: {error}",
            invocation.python_bin.display()
        )
    })?;
    Ok(())
}

fn resolve_bridge_invocation(app: &tauri::AppHandle) -> Result<BridgeInvocation, String> {
    let session_dir = resolve_session_dir(app)?;

    if let Ok(resource_dir) = app.path().resource_dir() {
        let script_root = resource_dir.join("python");
        let bridge_script = script_root.join("instagram_followback_desktop_bridge.py");
        let bundled_runtime_root = resource_dir.join("python-runtime");
        if bridge_script.exists() {
            if let Some(python_bin) = find_python_executable(&bundled_runtime_root.join("python")) {
                let browsers_path = bundled_runtime_root.join("playwright-browsers");
                return Ok(BridgeInvocation {
                    python_bin,
                    bridge_script,
                    working_dir: script_root,
                    session_dir,
                    browsers_path: browsers_path.exists().then_some(browsers_path),
                });
            }
        }
    }

    let repo_root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or_else(|| {
            "Failed to resolve the repository root for desktop development.".to_string()
        })?
        .to_path_buf();
    let bridge_script = repo_root.join("instagram_followback_desktop_bridge.py");
    if !bridge_script.exists() {
        return Err("The desktop bridge script is missing from the repository root.".to_string());
    }

    let dev_runtime_root = repo_root.join(".desktop-runtime");
    if let Some(python_bin) = find_python_executable(&dev_runtime_root.join("python")) {
        let browsers_path = dev_runtime_root.join("playwright-browsers");
        return Ok(BridgeInvocation {
            python_bin,
            bridge_script,
            working_dir: repo_root,
            session_dir,
            browsers_path: browsers_path.exists().then_some(browsers_path),
        });
    }

    let python_bin = resolve_system_python()?;
    Ok(BridgeInvocation {
        python_bin,
        bridge_script,
        working_dir: repo_root,
        session_dir,
        browsers_path: None,
    })
}

fn cleanup_login_processes_for_session(session_dir: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        let session_dir = session_dir.display().to_string();
        let patterns = [
            format!("instagram_followback_desktop_bridge.py login --session-dir {session_dir}"),
            format!("Google Chrome for Testing.*--user-data-dir={session_dir}"),
            format!("chrome-headless-shell.*--user-data-dir={session_dir}"),
        ];

        for pattern in patterns {
            let status = Command::new("pkill")
                .arg("-f")
                .arg(&pattern)
                .status()
                .map_err(|error| {
                    format!("Failed to run pkill for lingering Instagram login processes: {error}")
                })?;

            if !status.success() && status.code() != Some(1) {
                return Err(format!(
                    "Failed to clean up lingering Instagram login processes for session {}.",
                    session_dir
                ));
            }
        }
    }

    #[cfg(windows)]
    {
        let command = r#"
$session = $env:IFB_SESSION_DIR
if ([string]::IsNullOrWhiteSpace($session)) {
  exit 0
}

$targets = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -and
  $_.CommandLine.Contains($session) -and
  (
    $_.CommandLine.Contains("instagram_followback_desktop_bridge.py login") -or
    $_.CommandLine.Contains("--user-data-dir=" + $session) -or
    $_.CommandLine.Contains("--user-data-dir=""" + $session + """")
  )
}

foreach ($target in $targets) {
  Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
}
"#;

        let status = Command::new("powershell.exe")
            .arg("-NoProfile")
            .arg("-NonInteractive")
            .arg("-ExecutionPolicy")
            .arg("Bypass")
            .arg("-Command")
            .arg(command)
            .env("IFB_SESSION_DIR", session_dir)
            .status()
            .map_err(|error| {
                format!(
                    "Failed to run PowerShell cleanup for lingering Instagram login processes: {error}"
                )
            })?;

        if !status.success() {
            return Err(format!(
                "Failed to clean up lingering Instagram login processes for session {}.",
                session_dir.display()
            ));
        }
    }

    Ok(())
}

fn resolve_session_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let base_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Failed to resolve the desktop app data directory: {error}"))?;
    std::fs::create_dir_all(&base_dir)
        .map_err(|error| format!("Failed to create the desktop app data directory: {error}"))?;
    Ok(base_dir.join("live-session"))
}

fn find_python_executable(runtime_root: &Path) -> Option<PathBuf> {
    let direct_candidates = [
        runtime_root.join("python.exe"),
        runtime_root.join("python3.exe"),
        runtime_root.join("Scripts").join("python.exe"),
        runtime_root
            .join("Lib")
            .join("venv")
            .join("scripts")
            .join("nt")
            .join("python.exe"),
    ];
    for candidate in direct_candidates {
        if candidate.exists() {
            return Some(candidate);
        }
    }

    let bin_dir = runtime_root.join("bin");
    if !bin_dir.exists() {
        return None;
    }

    let mut candidates: Vec<PathBuf> = std::fs::read_dir(&bin_dir)
        .ok()?
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .filter(|path| {
            path.file_name()
                .and_then(|value| value.to_str())
                .map(|value| {
                    (value == "python3" || value.starts_with("python3."))
                        && !value.ends_with("-config")
                })
                .unwrap_or(false)
        })
        .collect();
    candidates.sort_by_key(|path| {
        path.file_name()
            .and_then(|value| value.to_str())
            .map(|value| match value {
                "python3" => 2,
                _ if value.starts_with("python3.") => 1,
                _ => 0,
            })
            .unwrap_or(0)
    });
    candidates.into_iter().next_back()
}

fn resolve_system_python() -> Result<PathBuf, String> {
    if let Ok(explicit_python) = std::env::var("IFB_PYTHON") {
        let path = PathBuf::from(explicit_python);
        if path.exists() {
            return Ok(path);
        }
    }

    for candidate in ["python3", "python"] {
        let status = Command::new(candidate)
            .arg("--version")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        if matches!(status, Ok(status) if status.success()) {
            return Ok(PathBuf::from(candidate));
        }
    }

    Err(
        "Python was not found. Install Python or run `npm run desktop:prepare-runtime` before launching the desktop app."
            .to_string(),
    )
}

fn apply_bridge_environment(command: &mut Command, invocation: &BridgeInvocation) {
    if let Some(browsers_path) = invocation.browsers_path.as_ref() {
        command.env("PLAYWRIGHT_BROWSERS_PATH", browsers_path);
    }
}

fn decode_stderr(stderr: &[u8]) -> Option<String> {
    let value = String::from_utf8_lossy(stderr).trim().to_string();
    if value.is_empty() {
        None
    } else {
        Some(value)
    }
}
