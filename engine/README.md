# engine — 日语视频转中文字幕引擎

可独立运行的核心引擎,先用 CLI 跑通「视频 → 中文 SRT」,再由 Tauri 壳作为 sidecar 调用其 HTTP 接口。

## 环境(Anaconda)

```bash
# 1. 创建并激活环境(Python 3.12:ML 依赖 wheel 最齐全,踩坑最少)
conda create -n jpsub python=3.12 -y
conda activate jpsub

# 2. ffmpeg(走 conda-forge,免单独装系统 ffmpeg)
conda install -c conda-forge ffmpeg -y

# 3. Python 依赖(全部装最新版)
pip install -U faster-whisper fastapi "uvicorn[standard]" sse-starlette httpx pysubs2 srt pydantic
```

> GPU 加速:需 NVIDIA 显卡 + 对应 CUDA。faster-whisper 依赖的 CTranslate2 会自动用 GPU;
> 若 CUDA 库缺失,可 `pip install -U nvidia-cublas-cu12 nvidia-cudnn-cu12`,或直接用 CPU(自动回退)。
>
> 版本选择:Python 锁 3.12——ctranslate2/torch 等重型 ML 依赖出 wheel 常滞后于 Python 新版,
> 3.12 wheel 最齐全、踩坑最少。Tauri 则跟最新稳定版(2.9.x),壳无生态包袱。

## 跑通命令行版

```bash
# 默认本地 Ollama 翻译引擎
python -m engine.cli input.mp4 -o output.srt

# 指定模型档 / 在线翻译引擎
python -m engine.cli input.mp4 -o out.srt --model large-v3 --engine online --provider deepl
```

## 翻译

### 本地 Ollama

默认本地引擎调用 `http://127.0.0.1:11434/api/chat`,模型名默认 `qwen2.5`。

```bash
set OLLAMA_MODEL=qwen2.5
python -m engine.cli input.mp4 -o out.srt --engine local
```

### OpenAI-compatible

```bash
set OPENAI_API_KEY=sk-...
set OPENAI_MODEL=gpt-4o-mini
python -m engine.cli input.mp4 -o out.srt --engine online --provider openai
```

兼容服务可设置 `OPENAI_BASE_URL`,例如 `https://api.openai.com/v1`。

### DeepL

```bash
set DEEPL_API_KEY=...
python -m engine.cli input.mp4 -o out.srt --engine online --provider deepl
```

## 启 HTTP 服务(给 Tauri sidecar 用)

```bash
python -m engine.server          # 监听 127.0.0.1 随机端口,端口打印到 stdout 首行
```

## 模块

| 文件 | 职责 |
|---|---|
| `models.py` | Segment / Task / TaskOptions 数据结构 |
| `media.py` | ffmpeg 抽音轨 / 烧录 |
| `asr.py` | faster-whisper 自适应识别 |
| `translate.py` | 双引擎策略(Ollama + OpenAI-compatible / DeepL) |
| `subtitle.py` | SRT / ASS 序列化、双语 |
| `orchestrator.py` | 任务状态机、进度、取消 |
| `server.py` | FastAPI + SSE |
| `cli.py` | 命令行入口 |

> 翻译只写入 `text_zh`,不会修改 `start` / `end` 时间轴。
