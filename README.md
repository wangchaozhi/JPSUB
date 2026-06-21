# jpsub — 日语视频转中文字幕(Windows 桌面应用)

Tauri 壳(Rust)+ React 前端 + Python 引擎(sidecar)。重计算在本地完成。

## 仓库结构

```
.
├─ engine/              Python 引擎(识别 + 翻译 + 字幕),可独立 CLI/HTTP 运行
│  ├─ asr.py            faster-whisper 自适应识别
│  ├─ translate.py      双引擎(本地 LLM / 在线 API)
│  ├─ subtitle.py       SRT/ASS 序列化
│  ├─ orchestrator.py   任务流水线 + 进度 + 取消
│  ├─ server.py         FastAPI + SSE(给 sidecar 用)
│  └─ cli.py            命令行入口
├─ shell/src-tauri/     Tauri 壳:拉起引擎 sidecar、注入端口、GPU 探测
├─ ui/                  前端工作台(导入/设置/进度/字幕编辑器)
├─ pyproject.toml       引擎打包 + ruff/black/mypy 配置
├─ .pre-commit-config.yaml  统一格式化/静态检查钩子
├─ 日语视频转中文字幕_Tauri技术架构文档.md   实现规格
└─ engine/README.md     引擎单独说明与环境搭建
```

## 快速开始

```bash
# 1. Python 引擎环境(Anaconda)
conda create -n jpsub python=3.12 -y
conda activate jpsub
conda install -c conda-forge ffmpeg -y
pip install -e ".[dev]"        # 装引擎 + 开发工具(ruff/black/mypy/pytest/pre-commit/PyInstaller)

# 2. 装代码规范钩子
pre-commit install

# 3. 验证引擎(命令行)
python -m engine.cli input.mp4 -o out.srt

# 4. 跑 Tauri 壳(需 Rust 工具链 + Node)
cd ui && npm install && cd ..
cd shell/src-tauri && cargo tauri dev
```

## 翻译引擎

- 本地模式:`engine=local`,默认调用 Ollama `http://127.0.0.1:11434/api/chat`,模型默认 `qwen2.5`;可用 `engine_params.model_name` / `OLLAMA_MODEL` 覆盖。
- 在线模式:`engine=online`,支持 OpenAI-compatible Chat Completions 和 DeepL。API key 只从运行时参数或环境变量读取,不要写入仓库或配置文件。
- 运行时环境变量:
  - `OPENAI_API_KEY`,可选 `OPENAI_BASE_URL`, `OPENAI_MODEL`
  - `DEEPL_API_KEY`,可选 `DEEPL_ENDPOINT`
  - `OLLAMA_BASE_URL`, `OLLAMA_MODEL`

## 代码规范

- Python:`ruff`(lint + import 排序)+ `black`(格式)+ `mypy`(类型)
- Rust:`cargo fmt` + `cargo clippy -D warnings`
- 前端:`eslint` + `prettier`
- 全部经 `pre-commit` 在提交前自动执行;手动全量:`pre-commit run --all-files`

## 构建分发

引擎用 PyInstaller 冻结为 Tauri sidecar;`cargo tauri build` 产出 `.msi`/NSIS 安装包。模型不内置,首次使用按需下载。详见架构文档。

```bash
# sidecar 命名需带 target triple
python -m PyInstaller --noconfirm --clean --onefile \
  --name engine-x86_64-pc-windows-msvc \
  --collect-submodules engine \
  --collect-submodules faster_whisper \
  --collect-submodules ctranslate2 \
  --collect-submodules sse_starlette \
  engine/sidecar.py

mkdir -p shell/src-tauri/binaries
cp dist/engine-x86_64-pc-windows-msvc.exe shell/src-tauri/binaries/

cd shell/src-tauri
cargo tauri build
```
