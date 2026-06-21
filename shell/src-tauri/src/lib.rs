use std::{sync::Mutex, thread, time::Duration};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

const MAX_ENGINE_RESTARTS: u8 = 3;

#[derive(Default)]
struct EngineState {
    port: Mutex<Option<u16>>,
    restart_attempts: Mutex<u8>,
}

#[derive(Clone, Serialize)]
struct Capabilities {
    base_url: String,
}

#[tauri::command]
fn engine_port(state: State<EngineState>) -> Option<u16> {
    *state.port.lock().unwrap()
}

#[tauri::command]
fn engine_base_url(state: State<EngineState>) -> Option<String> {
    state
        .port
        .lock()
        .unwrap()
        .map(|p| format!("http://127.0.0.1:{p}"))
}

#[tauri::command]
fn restart_engine(app: AppHandle) -> Result<(), String> {
    {
        let state = app.state::<EngineState>();
        if state.port.lock().unwrap().is_some() {
            return Ok(());
        }
        *state.restart_attempts.lock().unwrap() = 0;
    }
    spawn_engine(&app)
}

#[tauri::command]
fn api_key_status(provider: String) -> Result<bool, String> {
    credential_store::has_api_key(&provider)
}

#[tauri::command]
fn save_api_key(provider: String, api_key: String) -> Result<(), String> {
    credential_store::save_api_key(&provider, &api_key)
}

#[tauri::command]
fn load_api_key(provider: String) -> Result<Option<String>, String> {
    credential_store::load_api_key(&provider)
}

#[tauri::command]
fn delete_api_key(provider: String) -> Result<(), String> {
    credential_store::delete_api_key(&provider)
}

fn spawn_engine(app: &AppHandle) -> Result<(), String> {
    let sidecar = app
        .shell()
        .sidecar("engine")
        .map_err(|err| format!("engine sidecar is unavailable: {err}"))?;

    let (mut rx, _child) = sidecar
        .spawn()
        .map_err(|err| format!("engine process failed to start: {err}"))?;

    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let text = String::from_utf8_lossy(&line);
                    let trimmed = text.trim();
                    if let Ok(port) = trimmed.parse::<u16>() {
                        let state = handle.state::<EngineState>();
                        *state.port.lock().unwrap() = Some(port);
                        *state.restart_attempts.lock().unwrap() = 0;
                        let _ = handle.emit(
                            "engine-ready",
                            Capabilities {
                                base_url: format!("http://127.0.0.1:{port}"),
                            },
                        );
                    }
                }
                CommandEvent::Stderr(line) => {
                    eprintln!("[engine] {}", String::from_utf8_lossy(&line).trim());
                }
                CommandEvent::Terminated(payload) => {
                    {
                        let state = handle.state::<EngineState>();
                        *state.port.lock().unwrap() = None;
                    }
                    eprintln!("[engine] exited: {:?}", payload.code);
                    let _ = handle.emit("engine-exit", payload.code);
                    schedule_engine_restart(handle.clone());
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(())
}

fn schedule_engine_restart(app: AppHandle) {
    let attempt = {
        let state = app.state::<EngineState>();
        let mut attempts = state.restart_attempts.lock().unwrap();
        if *attempts >= MAX_ENGINE_RESTARTS {
            let _ = app.emit("engine-error", "engine exited too many times");
            return;
        }
        *attempts += 1;
        *attempts
    };

    let _ = app.emit("engine-restarting", attempt);
    thread::spawn(move || {
        thread::sleep(Duration::from_secs(u64::from(attempt)));
        if let Err(err) = spawn_engine(&app) {
            eprintln!("[engine] restart failed: {err}");
            let _ = app.emit("engine-error", err);
        }
    });
}

#[cfg(target_os = "windows")]
mod credential_store {
    use std::{io, ptr::null_mut, slice};

    use windows_sys::Win32::Foundation::{GetLastError, ERROR_NOT_FOUND};
    use windows_sys::Win32::Security::Credentials::{
        CredDeleteW, CredFree, CredReadW, CredWriteW, CREDENTIALW, CRED_PERSIST_LOCAL_MACHINE,
        CRED_TYPE_GENERIC,
    };

    const MAX_API_KEY_BYTES: usize = 2048;

    pub fn has_api_key(provider: &str) -> Result<bool, String> {
        load_api_key(provider).map(|value| value.is_some())
    }

    pub fn save_api_key(provider: &str, api_key: &str) -> Result<(), String> {
        let key = api_key.trim();
        if key.is_empty() {
            return delete_api_key(provider);
        }

        let mut target = target_name(provider)?;
        let mut user_name = wide("api_key");
        let mut blob = key.as_bytes().to_vec();
        if blob.len() > MAX_API_KEY_BYTES {
            return Err("api key is too long for Windows Credential Manager".into());
        }

        let mut credential: CREDENTIALW = unsafe { std::mem::zeroed() };
        credential.Type = CRED_TYPE_GENERIC;
        credential.TargetName = target.as_mut_ptr();
        credential.CredentialBlobSize = blob.len() as u32;
        credential.CredentialBlob = blob.as_mut_ptr();
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE;
        credential.UserName = user_name.as_mut_ptr();

        let ok = unsafe { CredWriteW(&credential, 0) };
        if ok == 0 {
            return Err(format!(
                "Windows Credential Manager write failed: {}",
                io::Error::last_os_error()
            ));
        }
        Ok(())
    }

    pub fn load_api_key(provider: &str) -> Result<Option<String>, String> {
        let target = target_name(provider)?;
        let mut raw: *mut CREDENTIALW = null_mut();
        let ok = unsafe { CredReadW(target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut raw) };
        if ok == 0 {
            let code = unsafe { GetLastError() };
            if code == ERROR_NOT_FOUND {
                return Ok(None);
            }
            return Err(format!(
                "Windows Credential Manager read failed: {}",
                io::Error::last_os_error()
            ));
        }

        let bytes = unsafe {
            let credential = &*raw;
            slice::from_raw_parts(
                credential.CredentialBlob,
                credential.CredentialBlobSize as usize,
            )
            .to_vec()
        };
        unsafe { CredFree(raw.cast()) };

        String::from_utf8(bytes)
            .map(Some)
            .map_err(|_| "stored api key is not valid UTF-8".into())
    }

    pub fn delete_api_key(provider: &str) -> Result<(), String> {
        let target = target_name(provider)?;
        let ok = unsafe { CredDeleteW(target.as_ptr(), CRED_TYPE_GENERIC, 0) };
        if ok == 0 {
            let code = unsafe { GetLastError() };
            if code == ERROR_NOT_FOUND {
                return Ok(());
            }
            return Err(format!(
                "Windows Credential Manager delete failed: {}",
                io::Error::last_os_error()
            ));
        }
        Ok(())
    }

    fn target_name(provider: &str) -> Result<Vec<u16>, String> {
        let provider = provider.trim().to_ascii_lowercase();
        if provider.is_empty()
            || !provider
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))
        {
            return Err("invalid provider name".into());
        }
        Ok(wide(&format!("JPSUB:{provider}:api-key")))
    }

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(Some(0)).collect()
    }
}

#[cfg(not(target_os = "windows"))]
mod credential_store {
    pub fn has_api_key(provider: &str) -> Result<bool, String> {
        let _ = provider;
        Ok(false)
    }

    pub fn save_api_key(provider: &str, api_key: &str) -> Result<(), String> {
        let _ = (provider, api_key);
        Err("secure api key storage is only available on Windows".into())
    }

    pub fn load_api_key(provider: &str) -> Result<Option<String>, String> {
        let _ = provider;
        Ok(None)
    }

    pub fn delete_api_key(provider: &str) -> Result<(), String> {
        let _ = provider;
        Ok(())
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(EngineState::default())
        .setup(|app| {
            if let Err(err) = spawn_engine(app.handle()) {
                eprintln!("[engine] startup failed: {err}");
                let _ = app.handle().emit("engine-error", err);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            engine_port,
            engine_base_url,
            restart_engine,
            api_key_status,
            save_api_key,
            load_api_key,
            delete_api_key
        ])
        .run(tauri::generate_context!())
        .expect("Tauri application failed to start");
}
