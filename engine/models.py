"""核心数据结构。"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Stage(StrEnum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    MUXING = "muxing"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


# 各阶段在总进度中的权重(和为 1.0)
STAGE_WEIGHTS: dict[Stage, float] = {
    Stage.EXTRACTING: 0.05,
    Stage.TRANSCRIBING: 0.55,
    Stage.TRANSLATING: 0.35,
    Stage.MUXING: 0.05,
}


@dataclass
class Segment:
    index: int
    start: float  # 秒
    end: float
    text_src: str  # 日文原文
    text_zh: str = ""  # 中文译文,翻译后填充

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskOptions:
    model: str | None = None  # tiny|base|small|medium|large-v3;None=自适应默认
    device: str | None = None  # cuda|cpu;None=自适应
    engine: str = "local"  # local | online
    engine_params: dict[str, Any] = field(default_factory=dict)
    output_format: str = "srt"  # srt | ass
    bilingual: bool = False
    burn_in: bool = False
    glossary: list[dict[str, str]] = field(default_factory=list)  # [{"src":..,"dst":..}]


@dataclass
class TaskError:
    kind: str  # input|engine_missing|oom|auth|network|unknown
    message: str
    hint: str = ""


@dataclass
class Task:
    input_path: str
    options: TaskOptions = field(default_factory=TaskOptions)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    stage: Stage = Stage.PENDING
    progress: float = 0.0
    error: TaskError | None = None
    segments: list[Segment] = field(default_factory=list)
    result: dict[str, str] = field(default_factory=dict)  # {srt_path?, ass_path?, video_path?}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["stage"] = self.stage.value
        d.pop("segments", None)
        return d
