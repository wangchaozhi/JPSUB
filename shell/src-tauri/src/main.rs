// Windows 发布时不弹出控制台窗口
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    jpsub_lib::run()
}
