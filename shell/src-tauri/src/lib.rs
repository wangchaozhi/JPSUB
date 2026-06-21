//! Tauri 壳:负责拉起 Python 引擎 sidecar、读取其端口、向前端暴露能力。
//!
//! 引擎(engine.exe / `python -m engine.server`)启动后第一行 stdout 打印端口,
//! 这里读取后存入 EngineState,前端通过 `engine_port` 命令拿到本地接口地址。

use std::sync::Mutex;

use serde::Serialize;
use tauri::{Emitter, Manager, State};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

/// 引擎运行状态:HTTP 端口 + 子进程句柄。
#[derive(Default)]
struct EngineState {
    port: Mutex<Option<u16>>,
}

#[derive(Clone, Serialize)]
struct Capabilities {
    base_url: String,
}

/// 前端调用:拿到引擎本地接口地址。引擎未就绪时返回 None。
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

/// 启动引擎 sidecar,后台读取其 stdout 首行端口写入 state。
fn spawn_engine(app: &tauri::AppHandle) {
    let sidecar = app
        .shell()
        .sidecar("engine")
        .expect("找不到 engine sidecar,检查 tauri.conf.json externalBin 与打包产物");

    let (mut rx, _child) = sidecar.spawn().expect("引擎进程启动失败");

    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let text = String::from_utf8_lossy(&line);
                    let trimmed = text.trim();
                    // 首行是端口号
                    if let Ok(port) = trimmed.parse::<u16>() {
                        let state = handle.state::<EngineState>();
                        *state.port.lock().unwrap() = Some(port);
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
                    eprintln!("[engine] 退出: {:?}", payload.code);
                    let _ = handle.emit("engine-exit", payload.code);
                    // TODO: 健康检查 + 自动重启(spawn_engine 重入)
                    break;
                }
                _ => {}
            }
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(EngineState::default())
        .setup(|app| {
            spawn_engine(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![engine_port, engine_base_url])
        .run(tauri::generate_context!())
        .expect("Tauri 应用启动失败");
}
