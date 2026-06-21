from __future__ import annotations

import pytest

from engine.models import Segment, TaskOptions
from engine.translate import _extract_translations, _run_batches


def test_extract_translations_from_json_object() -> None:
    assert _extract_translations('{"translations":["你好","谢谢"]}', 2) == ["你好", "谢谢"]


def test_extract_translations_from_fenced_json() -> None:
    text = '```json\n{"translations":["第一句"]}\n```'

    assert _extract_translations(text, 1) == ["第一句"]


def test_extract_translations_rejects_count_mismatch() -> None:
    with pytest.raises(ValueError):
        _extract_translations('{"translations":["只有一条"]}', 2)


def test_run_batches_writes_translation_without_changing_timeline() -> None:
    segments = [
        Segment(index=0, start=1.0, end=2.0, text_src="こんにちは"),
        Segment(index=1, start=3.0, end=4.0, text_src="ありがとう"),
    ]

    result = _run_batches(
        segments,
        TaskOptions(),
        on_progress=None,
        is_canceled=None,
        translate_batch=lambda batch, _: [f"译:{seg.text_src}" for seg in batch],
    )

    assert result is segments
    assert [(seg.start, seg.end) for seg in segments] == [(1.0, 2.0), (3.0, 4.0)]
    assert [seg.text_zh for seg in segments] == ["译:こんにちは", "译:ありがとう"]
