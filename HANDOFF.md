# 开发交接说明(给 AI 开发代理)

本文件是接手本仓库的入口。请先读完再动手。目标:把一个**日语视频转中文字幕的 Windows 桌面应用**做完整。

技术栈已固定,不要替换:**Tauri 最新稳定版(2.9.x)壳 + React/TS 前端 + Python 3.12 引擎(以 sidecar 方式被壳拉起)**。重计算全在本地。

---

## 0. 先读这两份文档

- `日语视频转中文字幕_Tauri技术架构文档.md` —— **实现规格**(文件名是历史遗留,内容是规格,不是泛泛的架构介绍)。接口、数据结构、模块职责以它为准。
- `README.md` —— 仓库结构与开发/构建命令。
- `engine/README.md` —— 引擎环境搭建与运行。

---

## 1. 已完成(可直接运行)

- **引擎核心链路**:抽音轨 → faster-whisper 识别(GPU/CPU 自适应)→ 翻译 → SRT/ASS。
  命令行可端到端跑通:`python -m engine.cli input.mp4 -o out.srt`。
- **引擎 HTTP 服务**:`engine/server.py`(FastAPI + SSE),`python -m engine.server` 启动,首行 stdout 打印端口。
- **Tauri 壳**:`shell/src-tauri/` 能拉起引擎 sidecar、读端口、发 `engine-ready` 事件、暴露 `engine_base_url` 命令。
- **代码规范**:ruff/black/mypy(Py)、clippy/rustfmt(Rust)、eslint/prettier(TS),经 pre-commit 统一执行。
- **前端连线占位**:`ui/src/App.tsx` 仅验证「壳→引擎」已通。

> 已校验:Python 全部文件语法通过、所有 JSON 合法。Rust 与 TS 尚未在真实工具链编译(开发环境无 cargo/node),首次构建请按报错修正版本/依赖细节。

---

## 2. 待办(按优先级)

### P0 — 让它真正能用
1. **实现翻译引擎**(`engine/translate.py`)。现在 `LocalLLMEngine` / `OnlineApiEngine` 都是占位,直接回传日文原文。
   - 需用户先决策(见 §4),拿到决策后接入真实调用。
   - 保留既有框架:策略接口、分批(BATCH_SIZE)、术语表注入、进度回调、时间轴只读。
   - 在线引擎要加:并发限制 + 指数退避重试,失败分段可单独重试。
2. **补两个字幕接口**(`engine/server.py`)。规格写了但未实现:
   - `GET /tasks/{id}/segments` → 返回 `[Segment]`(供前端字幕编辑器)
   - `PUT /tasks/{id}/segments` → 回写校对后的分段并重新导出字幕
   - 需在任务运行时把分段结果存起来(现在 orchestrator 内部持有,server 未暴露)。

### P1 — 完整前端
3. **实现完整 UI**(`ui/`)。当前只有占位。按规格做四块:
   - 导入区(拖拽视频 + 显示算力/耗时预估,调 `GET /capabilities`)
   - 设置区(模型档、翻译引擎及参数、输出格式、双语、烧录、术语表)
   - 进度面板(订阅 `GET /tasks/{id}/events` SSE,可取消)
   - **字幕编辑器**(逐条改文本/调时间/合并拆分 → `PUT /segments`)。这是机翻质量的关键补偿,必须做。
   - 前端通过 `engine_base_url` 拿到引擎地址后,直接 fetch 引擎 HTTP 接口。

### P2 — 打包分发
4. **PyInstaller 冻结引擎**为 `engine.exe`,放 `shell/src-tauri/binaries/`(Tauri sidecar 命名需带平台 target 后缀,如 `engine-x86_64-pc-windows-msvc.exe`)。
5. **ffmpeg.exe** 放 `shell/src-tauri/binaries/`(已在 tauri.conf.json 的 resources 里声明)。
6. **应用图标** `shell/src-tauri/icons/icon.ico`。
7. `cargo tauri build` 产出 `.msi`/NSIS;正式分发需代码签名以避开 SmartScreen。
8. **模型不内置**,首次使用按需下载到缓存目录(断点续传 + 校验)。

### P3 — 质量
9. 补 pytest 单元测试(subtitle 序列化、orchestrator 状态机、translate 分批逻辑最值得测)。
10. 错误归类已在 orchestrator 做了基础版,前端要把 `error.kind` 映射成可读提示。

---

## 3. 硬约束(不要违反)

- **时间轴只读**:翻译只填 `text_zh`,绝不改 `start`/`end`。
- **识别固定** `language="ja"`、`task="transcribe"`;**不要**用 whisper 自带 translate(它只译英文)。
- **算力自适应**:有 CUDA → cuda/float16/large-v3;纯 CPU → cpu/int8/medium。逻辑在 `engine/asr.py:detect_device()`,别写死。
- **长任务全异步**:带进度回传 + 取消令牌,取消后清理临时文件。
- **API key 不明文落盘**:走 Windows 凭据管理器(经 Tauri 存取)。
- **Python 锁 3.12**:ML 依赖 wheel 最齐全,别升 3.13/3.14。Tauri 跟最新稳定版。
- 机翻定位为「人工校对前的草稿」,字幕编辑器是产品必需,不是可选项。

---

## 4. 需用户先拍板的决策(开工前确认)

1. **翻译引擎**:本地 LLM(Ollama 用哪个模型?)/ 在线 API(DeepL / Google / OpenAI 哪个?)/ 两者都做可切换?
2. **是否需要 GPU 版打包**:面向有独显用户要单独出 CUDA 版,还是先只做 CPU 版?
3. **字幕产物默认**:默认 SRT 还是 ASS?是否默认双语?

> 这些没定之前,P0 的翻译接入和 P2 的打包形态都无法确定,先找用户确认。

---

## 5. 建议第一步

1. `conda create -n jpsub python=3.12 -y && conda activate jpsub`
2. `conda install -c conda-forge ffmpeg -y`
3. `pip install -e ".[dev]" && pre-commit install`
4. 准备一段 30 秒日语视频,跑 `python -m engine.cli test.mp4 -o test.srt`,确认识别+时间轴正常(此时译文还是日文原文,正常)。
5. 跑通后,按 §4 拿到用户决策,从 P0-1 接入真实翻译开始。
