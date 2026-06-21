"""语音识别:faster-whisper,GPU/CPU 自适应。"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from .models import Segment, TaskOptions

_DLL_DIRECTORY_HANDLES: list[object] = []


def detect_device() -> tuple[str, str, str]:
    """探测算力,返回 (device, compute_type, default_model)。

    有 CUDA -> cuda/float16/large-v3;否则 cpu/int8/medium。
    """
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0 and _cuda_runtime_available():
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
    detected_device, compute_type, default_model = detect_device()
    device = options.device or detected_device
    if device == "cpu":
        compute_type = "int8"
    model_size = options.model or default_model

    try:
        return _run_transcribe(
            audio_wav,
            model_size,
            device,
            compute_type,
            on_progress,
            audio_duration,
            is_canceled,
        )
    except Exception as exc:
        if options.device == "cuda" or device != "cuda" or not _is_cuda_runtime_error(exc):
            raise
        device = "cpu"
        compute_type = "int8"
        model_size = options.model or "medium"
        return _run_transcribe(
            audio_wav,
            model_size,
            device,
            compute_type,
            on_progress,
            audio_duration,
            is_canceled,
        )


def _run_transcribe(
    audio_wav: str,
    model_size: str,
    device: str,
    compute_type: str,
    on_progress: Callable[[float], None] | None,
    audio_duration: float,
    is_canceled: Callable[[], bool] | None,
) -> list[Segment]:
    from faster_whisper import WhisperModel

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


def _cuda_runtime_available() -> bool:
    if os.name != "nt":
        return True
    _configure_cuda_dll_search_path()
    try:
        ctypes.WinDLL("cublas64_12.dll")
    except OSError:
        return False
    return True


def _is_cuda_runtime_error(exc: Exception) -> bool:
    low = str(exc).lower()
    return any(
        token in low
        for token in (
            "cublas64_12.dll",
            "cudnn",
            "cuda driver",
            "cuda runtime",
            "library cublas",
        )
    )


def _configure_cuda_dll_search_path() -> None:
    candidates: list[Path] = []
    for env_key in ("CUDA_PATH", "CUDA_HOME"):
        value = os.getenv(env_key)
        if value:
            candidates.append(Path(value) / "bin")

    toolkit_root = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
    if toolkit_root.exists():
        candidates.extend(sorted(toolkit_root.glob("v*/bin"), reverse=True))

    for path in candidates:
        if not path.exists():
            continue
        text = str(path)
        current_path = os.environ.get("PATH", "")
        if text.lower() not in {item.lower() for item in current_path.split(os.pathsep)}:
            os.environ["PATH"] = text + os.pathsep + current_path
        if hasattr(os, "add_dll_directory"):
            with suppress(OSError):
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(text))
