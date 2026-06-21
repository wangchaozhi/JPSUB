"""任务编排:把媒体→识别→翻译→字幕串成可观测、可取消的流水线。"""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from . import asr, media, subtitle, translate
from .models import STAGE_WEIGHTS, Segment, Stage, Task, TaskError

# 各阶段进度的累计起点
_STAGE_BASE = {
    Stage.EXTRACTING: 0.0,
    Stage.TRANSCRIBING: 0.05,
    Stage.TRANSLATING: 0.60,
    Stage.MUXING: 0.95,
}

ProgressCb = Callable[[Task], None]


class CanceledError(Exception):
    pass


def run(
    task: Task,
    on_update: ProgressCb | None = None,
    cancel_event: threading.Event | None = None,
) -> Task:
    """同步执行整条流水线。on_update 在 stage/progress 变化时回调。"""
    cancel_event = cancel_event or threading.Event()

    def canceled() -> bool:
        return cancel_event.is_set()

    def emit(stage: Stage, local: float):
        task.stage = stage
        base = _STAGE_BASE.get(stage, task.progress)
        weight = STAGE_WEIGHTS.get(stage, 0.0)
        task.progress = round(base + weight * local, 4)
        if on_update:
            on_update(task)

    workdir = Path(tempfile.mkdtemp(prefix=f"jpsub_{task.id}_"))
    try:
        if canceled():
            raise CanceledError()

        # 1. 抽音轨
        emit(Stage.EXTRACTING, 0.0)
        duration = media.probe_duration(task.input_path)
        wav = media.extract_audio(task.input_path, str(workdir / "audio.wav"))
        emit(Stage.EXTRACTING, 1.0)

        # 2. 识别
        segments: list[Segment] = asr.transcribe(
            wav,
            task.options,
            on_progress=lambda p: emit(Stage.TRANSCRIBING, p),
            audio_duration=duration,
            is_canceled=canceled,
        )
        task.segments = segments
        if on_update:
            on_update(task)
        if canceled():
            raise CanceledError()

        # 3. 翻译
        translator = translate.get_translator(task.options)
        segments = translator.translate(
            segments,
            task.options,
            on_progress=lambda p: emit(Stage.TRANSLATING, p),
            is_canceled=canceled,
        )
        task.segments = segments
        if on_update:
            on_update(task)
        if canceled():
            raise CanceledError()

        # 4. 出字幕(+ 可选烧录)
        emit(Stage.MUXING, 0.0)
        ext = task.options.output_format
        sub_path = str(Path(task.input_path).with_suffix(f".zh.{ext}"))
        subtitle.write(segments, sub_path, task.options)
        task.result[f"{ext}_path"] = sub_path

        if task.options.burn_in:
            out_video = str(Path(task.input_path).with_suffix(".subbed.mp4"))
            media.burn_in(task.input_path, sub_path, out_video)
            task.result["video_path"] = out_video
        emit(Stage.MUXING, 1.0)

        task.stage = Stage.DONE
        task.progress = 1.0
        if on_update:
            on_update(task)

    except CanceledError:
        task.stage = Stage.CANCELED
        if on_update:
            on_update(task)
    except Exception as e:  # noqa: BLE001
        task.stage = Stage.FAILED
        task.error = _classify(e)
        if on_update:
            on_update(task)
    finally:
        # 清理临时文件
        for p in workdir.glob("*"):
            with suppress(OSError):
                p.unlink()
        with suppress(OSError):
            workdir.rmdir()
    return task


def _classify(e: Exception) -> TaskError:
    msg = str(e)
    low = msg.lower()
    if "不存在" in msg or "no such file" in low or "not found" in low:
        return TaskError("input", msg, "检查视频路径是否完整,建议用“选择”按钮重新选文件")
    if "ffmpeg" in low or "ffprobe" in low:
        return TaskError("input", msg, "检查输入文件是否可读,或确认 ffmpeg/ffprobe 可用")
    if "out of memory" in low or "cuda" in low and "memory" in low:
        return TaskError("oom", msg, "显存不足,降低模型档或改用 CPU")
    if "auth" in low or "api key" in low or "401" in low:
        return TaskError("auth", msg, "检查翻译 API 鉴权")
    if "connection" in low or "timeout" in low or "network" in low:
        return TaskError("network", msg, "检查网络连接")
    return TaskError("unknown", msg)
