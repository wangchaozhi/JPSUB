from __future__ import annotations

from engine.orchestrator import _classify


def test_classify_cuda_runtime_missing() -> None:
    error = _classify(RuntimeError("Library cublas64_12.dll is not found or cannot be loaded"))

    assert error.kind == "engine_missing"
    assert "CUDA" in error.hint
