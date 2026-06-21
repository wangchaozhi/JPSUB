"""翻译模块:双引擎策略(本地 LLM / 在线 API),日文 -> 中文。

设计要点:
- 上下文批译:整批送入,提示词带前后文,避免逐句孤立翻译
- 术语表:翻译前注入 glossary
- 时间轴只读:只填 text_zh,不改 start/end
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable

import httpx

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
    pairs = "; ".join(f"{g['src']}→{g['dst']}" for g in options.glossary if g.get("src"))
    return f"\n固定术语对照:{pairs}" if pairs else ""


def _iter_batches(segments: list[Segment]):
    for i in range(0, len(segments), BATCH_SIZE):
        yield segments[i : i + BATCH_SIZE]


def _translation_prompt(batch: list[Segment], options: TaskOptions) -> list[dict[str, str]]:
    payload = [
        {
            "index": seg.index,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text_src,
        }
        for seg in batch
    ]
    system = (
        "你是专业日语影视字幕译者。把日语字幕翻译为自然、简洁的简体中文。"
        "保留人名、作品名和术语一致性;不要解释;不要改时间轴;不要合并或拆分条目。"
        f"{_glossary_hint(options)}"
    )
    user = (
        "请按输入顺序返回 JSON 对象,格式必须为 "
        '{"translations":["第一条中文","第二条中文"]}。'
        f"\n字幕 JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _extract_translations(text: str, expected: int) -> list[str]:
    """从 LLM 输出中提取译文数组。"""
    raw = text.strip()
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.S)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    bracket = re.search(r"(\{.*\}|\[.*\])", raw, flags=re.S)
    if bracket:
        candidates.append(bracket.group(1))

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                data = data.get("translations")
            if isinstance(data, list) and all(isinstance(item, str) for item in data):
                if len(data) != expected:
                    raise ValueError(f"expected {expected} translations, got {len(data)}")
                return [item.strip() for item in data]
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise ValueError(f"无法解析翻译结果: {last_error or raw[:120]}")


def _request_with_retry(
    call: Callable[[], httpx.Response],
    *,
    retries: int,
    retry_base: float,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = call()
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(retry_base * (2**attempt))
    raise RuntimeError(f"翻译请求失败: {last_error}")


class LocalLLMEngine(TranslatorInterface):
    """本地 LLM(Ollama)。离线。"""

    def translate(self, segments, options, on_progress=None, is_canceled=None):
        return _run_batches(
            segments,
            options,
            on_progress,
            is_canceled,
            translate_batch=self._translate_batch,
        )

    def _translate_batch(self, batch: list[Segment], options: TaskOptions) -> list[str]:
        params = options.engine_params
        endpoint = (
            params.get("endpoint") or os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
        )
        model = params.get("model_name") or os.getenv("OLLAMA_MODEL") or "qwen2.5"
        timeout = float(params.get("timeout", 120))
        retries = int(params.get("retries", 2))

        messages = _translation_prompt(batch, options)
        with httpx.Client(timeout=timeout) as client:
            response = _request_with_retry(
                lambda: client.post(
                    f"{endpoint.rstrip('/')}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": float(params.get("temperature", 0.2))},
                    },
                ),
                retries=retries,
                retry_base=1.0,
            )
        content = response.json().get("message", {}).get("content", "")
        return _extract_translations(content, len(batch))


class OnlineApiEngine(TranslatorInterface):
    """在线 API(OpenAI compatible / DeepL)。API key 只从运行时参数或环境变量读取。"""

    def translate(self, segments, options, on_progress=None, is_canceled=None):
        return _run_batches(
            segments,
            options,
            on_progress,
            is_canceled,
            translate_batch=self._translate_batch,
            allow_single_fallback=True,
        )

    def _translate_batch(self, batch: list[Segment], options: TaskOptions) -> list[str]:
        provider = str(options.engine_params.get("provider", "openai")).lower()
        if provider == "deepl":
            return self._translate_deepl(batch, options)
        if provider in {"openai", "openai-compatible", "compatible"}:
            return self._translate_openai(batch, options)
        raise ValueError(f"不支持的在线翻译 provider: {provider}")

    def _translate_openai(self, batch: list[Segment], options: TaskOptions) -> list[str]:
        params = options.engine_params
        api_key = params.get("api_key") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("缺少 OPENAI_API_KEY 或 engine_params.api_key")
        base_url = (
            params.get("base_url") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        ).rstrip("/")
        model = params.get("model_name") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
        timeout = float(params.get("timeout", 120))
        retries = int(params.get("retries", 3))

        messages = _translation_prompt(batch, options)
        with httpx.Client(timeout=timeout) as client:
            response = _request_with_retry(
                lambda: client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": float(params.get("temperature", 0.2)),
                        "response_format": {"type": "json_object"},
                    },
                ),
                retries=retries,
                retry_base=1.0,
            )
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return _extract_translations(content, len(batch))

    def _translate_deepl(self, batch: list[Segment], options: TaskOptions) -> list[str]:
        params = options.engine_params
        api_key = params.get("api_key") or os.getenv("DEEPL_API_KEY")
        if not api_key:
            raise ValueError("缺少 DEEPL_API_KEY 或 engine_params.api_key")
        endpoint = (
            params.get("endpoint")
            or params.get("base_url")
            or os.getenv("DEEPL_ENDPOINT")
            or "https://api-free.deepl.com/v2/translate"
        )
        timeout = float(params.get("timeout", 60))
        retries = int(params.get("retries", 3))

        with httpx.Client(timeout=timeout) as client:
            response = _request_with_retry(
                lambda: client.post(
                    endpoint,
                    headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
                    data={
                        "text": [seg.text_src for seg in batch],
                        "source_lang": "JA",
                        "target_lang": params.get("target_lang", "ZH-HANS"),
                    },
                ),
                retries=retries,
                retry_base=1.0,
            )
        translations = response.json().get("translations", [])
        texts = [item.get("text", "").strip() for item in translations if isinstance(item, dict)]
        if len(texts) != len(batch):
            raise ValueError(f"DeepL 返回条数不匹配: expected {len(batch)}, got {len(texts)}")
        return texts


def _run_batches(
    segments: list[Segment],
    options: TaskOptions,
    on_progress,
    is_canceled,
    translate_batch,
    *,
    allow_single_fallback: bool = False,
):
    total = len(segments) or 1
    done = 0
    for batch in _iter_batches(segments):
        if is_canceled and is_canceled():
            break
        try:
            zh = translate_batch(batch, options)
        except Exception:
            if not allow_single_fallback or len(batch) == 1:
                raise
            zh = []
            for seg in batch:
                if is_canceled and is_canceled():
                    break
                zh.extend(translate_batch([seg], options))
        if len(zh) != len(batch):
            raise ValueError(f"翻译结果条数不匹配: expected {len(batch)}, got {len(zh)}")
        for seg, text in zip(batch, zh, strict=True):
            seg.text_zh = text
        done += len(batch)
        if on_progress:
            on_progress(min(done / total, 1.0))
    return segments


def get_translator(options: TaskOptions) -> TranslatorInterface:
    return OnlineApiEngine() if options.engine == "online" else LocalLLMEngine()
