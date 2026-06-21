"""媒体处理:ffmpeg 抽音轨 / 烧录字幕。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("找不到 ffmpeg,请先安装(conda install -c conda-forge ffmpeg)")
    return exe


def extract_audio(input_path: str, out_wav: str) -> str:
    """抽取标准化音轨:16kHz 单声道 wav(Whisper 偏好输入)。"""
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(), "-y", "-i", input_path,
        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", out_wav,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_wav


def probe_duration(input_path: str) -> float:
    """返回媒体时长(秒),用于进度估算。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    cmd = [
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", input_path,
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
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
        _ffmpeg(), "-y", "-i", input_video,
        "-vf", f"subtitles='{sub}'",
        "-c:a", "copy", out_video,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_video
