"""字幕序列化:SRT / ASS,支持双语。"""
from __future__ import annotations

from pathlib import Path

from .models import Segment, TaskOptions


def _line(seg: Segment, bilingual: bool) -> str:
    zh = seg.text_zh or seg.text_src
    if bilingual and seg.text_src and seg.text_zh:
        return f"{zh}\n{seg.text_src}"
    return zh


def write_srt(segments: list[Segment], out_path: str, bilingual: bool = False) -> str:
    import srt  # pip install srt
    from datetime import timedelta

    items = [
        srt.Subtitle(
            index=i + 1,
            start=timedelta(seconds=s.start),
            end=timedelta(seconds=s.end),
            content=_line(s, bilingual),
        )
        for i, s in enumerate(segments)
    ]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(srt.compose(items), encoding="utf-8")
    return out_path


def write_ass(segments: list[Segment], out_path: str, bilingual: bool = False) -> str:
    import pysubs2  # pip install pysubs2

    subs = pysubs2.SSAFile()
    for s in segments:
        subs.append(pysubs2.SSAEvent(
            start=int(s.start * 1000),
            end=int(s.end * 1000),
            text=_line(s, bilingual).replace("\n", r"\N"),
        ))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    subs.save(out_path)
    return out_path


def write(segments: list[Segment], out_path: str, options: TaskOptions) -> str:
    if options.output_format == "ass":
        return write_ass(segments, out_path, options.bilingual)
    return write_srt(segments, out_path, options.bilingual)
