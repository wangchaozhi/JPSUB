"""语音识别:faster-whisper,GPU/CPU 自适应。"""

from __future__ import annotations

from collections.abc import Callable

from .models import Segment, TaskOptions


def detect_device() -> tuple[str, str, str]:
    """探测算力,返回 (device, compute_type, default_model)。

    有 CUDA -> cuda/float16/large-v3;否则 cpu/int8/medium。
    """
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16", "large-v3"
    except Exception:
        pass
    return "cpu", "int8", "medium"


def transcribe(
    audio_wav: str,
    options: TaskOptions,
    on_progress: Callable[[float], None] | None = None,
    audio_duration: float = 0.0,
    is_canceled: Callable[[], bool] | None = None,
) -> list[Segment]:
    """识别日语音轨,返回带时间戳的分段列表。"""
    from faster_whisper import WhisperModel

    device, compute_type, default_model = detect_device()
    device = options.device or device
    if device == "cpu":
        compute_type = "int8"
    model_size = options.model or default_model

    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    seg_iter, info = model.transcribe(
        audio_wav,
        language="ja",  # 显式固定日语
        task="transcribe",  # 识别为日文;译中文交给 translate 模块
        vad_filter=True,  # 过滤静音,减少幻觉
    )
    duration = audio_duration or getattr(info, "duration", 0.0) or 0.0

    results: list[Segment] = []
    for i, s in enumerate(seg_iter):
        if is_canceled and is_canceled():
            break
        results.append(Segment(index=i, start=s.start, end=s.end, text_src=s.text.strip()))
        if on_progress and duration > 0:
            on_progress(min(s.end / duration, 1.0))
    if on_progress:
        on_progress(1.0)
    return results
