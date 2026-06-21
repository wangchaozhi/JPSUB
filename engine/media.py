"""媒体处理:ffmpeg 抽音轨 / 烧录字幕。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _tool(name: str, env_key: str) -> str | None:
    candidates = [
        os.getenv(env_key),
        shutil.which(name),
        Path(sys.executable).parent / name,
        Path(sys.executable).parent / "binaries" / name,
        Path.cwd() / name,
        Path.cwd() / "binaries" / name,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return str(path)
    return None


def _ffmpeg() -> str:
    exe = _tool("ffmpeg.exe" if os.name == "nt" else "ffmpeg", "JPSUB_FFMPEG")
    if not exe:
        raise RuntimeError("找不到 ffmpeg,请先安装(conda install -c conda-forge ffmpeg)")
    return exe


def extract_audio(input_path: str, out_wav: str) -> str:
    """抽取标准化音轨:16kHz 单声道 wav(Whisper 偏好输入)。"""
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(),
        "-y",
        "-i",
        input_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        out_wav,
    ]
    _run_media_command(cmd, "ffmpeg 抽音轨失败")
    return out_wav


def probe_duration(input_path: str) -> float:
    """返回媒体时长(秒),用于进度估算。"""
    ffprobe = _tool("ffprobe.exe" if os.name == "nt" else "ffprobe", "JPSUB_FFPROBE")
    if not ffprobe:
        return 0.0
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        input_path,
    ]
    out = _run_media_command(cmd, "ffprobe 读取视频失败").stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def burn_in(input_video: str, subtitle_file: str, out_video: str) -> str:
    """硬字幕:把字幕烧入画面。软字幕封装可另写一个 mux 函数。"""
    Path(out_video).parent.mkdir(parents=True, exist_ok=True)
    # 注意 Windows 路径转义,ffmpeg subtitles 滤镜对路径敏感
    sub = subtitle_file.replace("\\", "/").replace(":", "\\:")
    cmd = [
        _ffmpeg(),
        "-y",
        "-i",
        input_video,
        "-vf",
        f"subtitles='{sub}'",
        "-c:a",
        "copy",
        out_video,
    ]
    _run_media_command(cmd, "ffmpeg 烧录字幕失败")
    return out_video


def _run_media_command(cmd: list[str], message: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if detail:
            raise RuntimeError(f"{message}: {detail}") from exc
        raise RuntimeError(f"{message}: {exc}") from exc
