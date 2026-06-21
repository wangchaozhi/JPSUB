"""翻译模块:双引擎策略(本地 LLM / 在线 API),日文 -> 中文。

设计要点:
- 上下文批译:整批送入,提示词带前后文,避免逐句孤立翻译
- 术语表:翻译前注入 glossary
- 时间轴只读:只填 text_zh,不改 start/end
"""
from __future__ import annotations

from typing import Callable

from .models import Segment, TaskOptions

BATCH_SIZE = 20  # 每批分段数


class TranslatorInterface:
    def translate(
        self,
        segments: list[Segment],
        options: TaskOptions,
        on_progress: Callable[[float], None] | None = None,
        is_canceled: Callable[[], bool] | None = None,
    ) -> list[Segment]:
        raise NotImplementedError


def _glossary_hint(options: TaskOptions) -> str:
    if not options.glossary:
        return ""
    pairs = "; ".join(f"{g['src']}→{g['dst']}" for g in options.glossary)
    return f"\n固定术语对照:{pairs}"


def _iter_batches(segments: list[Segment]):
    for i in range(0, len(segments), BATCH_SIZE):
        yield segments[i:i + BATCH_SIZE]


class LocalLLMEngine(TranslatorInterface):
    """本地 LLM(Ollama / llama.cpp)。离线。"""

    def translate(self, segments, options, on_progress=None, is_canceled=None):
        # TODO: 接入本地 LLM。示例:用 httpx 调 Ollama /api/chat
        #   model = options.engine_params.get("model_name", "qwen2.5")
        #   构造 prompt = 系统指令 + 术语表 + 当前批(带前后文) -> 请求 -> 解析逐条译文
        return _run_batches(
            segments, options, on_progress, is_canceled,
            translate_batch=self._translate_batch,
        )

    def _translate_batch(self, batch: list[Segment], options: TaskOptions) -> list[str]:
        # 占位:回传原文,接入后替换为真实译文
        return [s.text_src for s in batch]


class OnlineApiEngine(TranslatorInterface):
    """在线 API(DeepL / Google / OpenAI 兼容)。需要 api_key。"""

    def translate(self, segments, options, on_progress=None, is_canceled=None):
        provider = options.engine_params.get("provider", "deepl")
        # TODO: 按 provider 接入。并发限制 + 指数退避重试;失败分段可单独重试。
        return _run_batches(
            segments, options, on_progress, is_canceled,
            translate_batch=lambda b, o: self._translate_batch(b, o, provider),
        )

    def _translate_batch(self, batch, options, provider) -> list[str]:
        # 占位:回传原文,接入后替换为真实译文
        return [s.text_src for s in batch]


def _run_batches(segments, options, on_progress, is_canceled, translate_batch):
    total = len(segments) or 1
    done = 0
    for batch in _iter_batches(segments):
        if is_canceled and is_canceled():
            break
        zh = translate_batch(batch, options)
        for seg, t in zip(batch, zh):
            seg.text_zh = t
        done += len(batch)
        if on_progress:
            on_progress(min(done / total, 1.0))
    return segments


def get_translator(options: TaskOptions) -> TranslatorInterface:
    return OnlineApiEngine() if options.engine == "online" else LocalLLMEngine()
