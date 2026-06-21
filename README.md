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
├─ ui/                  前端(当前为最小占位,待开发完整 UI)
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
pip install -e ".[dev]"        # 装引擎 + 开发工具(ruff/black/mypy/pre-commit)

# 2. 装代码规范钩子
pre-commit install

# 3. 验证引擎(命令行)
python -m engine.cli input.mp4 -o out.srt

# 4. 跑 Tauri 壳(需 Rust 工具链 + Node)
cd ui && npm install && cd ..
cd shell/src-tauri && cargo tauri dev
```

## 代码规范

- Python:`ruff`(lint + import 排序)+ `black`(格式)+ `mypy`(类型)
- Rust:`cargo fmt` + `cargo clippy -D warnings`
- 前端:`eslint` + `prettier`
- 全部经 `pre-commit` 在提交前自动执行;手动全量:`pre-commit run --all-files`

## 构建分发

引擎用 PyInstaller 冻结为 `engine.exe` 作为 Tauri sidecar;`cargo tauri build` 产出 `.msi`/NSIS 安装包。模型不内置,首次使用按需下载。详见架构文档。
