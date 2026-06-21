# 开发交接说明(给 AI 开发代理)

本仓库目标:交付一个**日语视频转中文字幕的 Windows 桌面应用**。技术栈固定为 Tauri 2 + React/TypeScript + Python 3.12 sidecar 引擎,重计算在本地完成。

## 先读

- `日语视频转中文字幕_Tauri技术架构文档.md`:实现规格。
- `README.md`:仓库结构、开发命令、打包命令。
- `engine/README.md`:Python 引擎环境和运行说明。
- `.github/workflows/ci-release.yml`:CI、tag release、日志和部分构建失败策略。

## 当前状态

- Python 引擎链路已实现:抽音轨、faster-whisper 日语识别、翻译、SRT/ASS 导出、可选烧录。
- 翻译已接真实引擎:Ollama 本地模型、OpenAI-compatible Chat Completions、DeepL,带批处理、术语表、退避重试和单段兜底。
- FastAPI 服务已实现:任务创建、SSE 进度、取消、结果查询、字幕分段 GET/PUT 回写。
- React 工作台已实现:导入、引擎状态、模型/翻译/输出设置、术语表、进度、字幕编辑器。
- Whisper 模型预下载已实现:引擎提供模型缓存状态和下载 SSE,前端显示缓存状态、下载进度和速度。
- Tauri 壳已实现:启动 Python sidecar、注入 engine base URL、sidecar 退出事件、有限自动重启、手动重试。
- 在线翻译 API key 已通过 Tauri 命令接 Windows Credential Manager,前端可保存/加载/删除,不写入仓库或项目配置。
- 打包链路已跑通:PyInstaller sidecar + `cargo tauri build` 可产出 NSIS/MSI。`shell/src-tauri/binaries/` 为本地/CI 生成物,已忽略。
- GitHub Actions 已创建:push/PR 跑测试和构建,tag 创建 Release 资产,日志单独上传,sidecar/安装包允许部分失败。

## 已验证

在 Windows + conda env `jpsub` 下已验证:

- `conda run -n jpsub python -m pytest tests` -> 9 passed
- `conda run -n jpsub python -m ruff check engine tests` -> passed
- `npm run build` -> passed
- `npm run lint` -> passed
- `cargo fmt -- --check` -> passed
- `cargo check` -> passed
- `cargo tauri build` -> passed,本地产出 NSIS/MSI
- PyInstaller 冻结 sidecar 后 `/health` 返回 ok

## 仍需完成/确认

1. 准备一段真实日语视频,做一次完整端到端验收。`large-v3` 已在当前开发机缓存并通过 `local_files_only=True` 加载验证;其他机器首次使用仍需下载。
2. 用真实 Ollama/OpenAI/DeepL 配置各跑一次翻译冒烟测试。当前单元测试覆盖解析和控制流,未覆盖真实外部服务质量。
3. 推 tag 触发正式 GitHub Release:
   ```powershell
   git tag v0.1.0
   git push origin v0.1.0
   ```
4. 正式对外分发前需要代码签名,否则 Windows SmartScreen 可能拦截安装包。
5. 模型下载体验仍可继续增强:进度提示、失败重试、缓存目录说明、必要时预下载按钮。
6. 图标目前是基础占位,可以换成正式品牌图标。

## 硬约束

- 翻译只填 `text_zh`,不得改 `start`/`end`。
- ASR 固定 `language="ja"`、`task="transcribe"`,不要用 Whisper 自带 translate。
- Python 固定 3.12,不要升到 3.13/3.14。
- API key 不要写进文件,只走 Windows Credential Manager 或运行时环境变量。
- 机翻定位是人工校对前草稿,字幕编辑器是核心功能。
