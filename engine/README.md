# engine — 日语视频转中文字幕引擎骨架

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
# 默认本地占位翻译引擎,先验证识别+时间轴链路
python -m engine.cli input.mp4 -o output.srt

# 指定模型档 / 在线翻译引擎
python -m engine.cli input.mp4 -o out.srt --model large-v3 --engine online --provider deepl
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
| `translate.py` | 双引擎策略(本地占位 + 在线 API 接口) |
| `subtitle.py` | SRT / ASS 序列化、双语 |
| `orchestrator.py` | 任务状态机、进度、取消 |
| `server.py` | FastAPI + SSE |
| `cli.py` | 命令行入口 |

> translate.py 的 LocalLLMEngine / OnlineApiEngine 目前是带 TODO 的占位实现,
> 把对应 SDK 调用补上即可。其余链路(抽音轨→识别→时间轴→字幕)已可端到端跑通。
