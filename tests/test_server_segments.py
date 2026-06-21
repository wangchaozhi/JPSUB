from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from engine import server
from engine.models import Segment, Stage, Task, TaskOptions


def setup_function() -> None:
    server._tasks.clear()
    server._cancels.clear()


def _add_done_task(tmp_path):
    input_path = tmp_path / "sample.mp4"
    input_path.write_bytes(b"")
    task = Task(
        input_path=str(input_path),
        options=TaskOptions(output_format="srt", bilingual=True),
        stage=Stage.DONE,
        progress=1.0,
        segments=[
            Segment(index=0, start=0.0, end=1.2, text_src="こんにちは", text_zh="你好"),
            Segment(index=1, start=1.3, end=2.0, text_src="世界", text_zh="世界"),
        ],
    )
    server._tasks[task.id] = task
    server._cancels[task.id] = threading.Event()
    return task


def test_get_segments_returns_task_segments(tmp_path) -> None:
    task = _add_done_task(tmp_path)
    client = TestClient(server.app)

    response = client.get(f"/tasks/{task.id}/segments")

    assert response.status_code == 200
    assert response.json() == [
        {
            "index": 0,
            "start": 0.0,
            "end": 1.2,
            "text_src": "こんにちは",
            "text_zh": "你好",
        },
        {
            "index": 1,
            "start": 1.3,
            "end": 2.0,
            "text_src": "世界",
            "text_zh": "世界",
        },
    ]


def test_get_task_does_not_embed_segments(tmp_path) -> None:
    task = _add_done_task(tmp_path)
    client = TestClient(server.app)

    response = client.get(f"/tasks/{task.id}")

    assert response.status_code == 200
    assert "segments" not in response.json()


def test_put_segments_reindexes_and_exports_subtitle(tmp_path) -> None:
    task = _add_done_task(tmp_path)
    client = TestClient(server.app)

    response = client.put(
        f"/tasks/{task.id}/segments",
        json=[
            {
                "index": 9,
                "start": 0.0,
                "end": 1.0,
                "text_src": "はい",
                "text_zh": "好的",
            },
            {
                "index": 3,
                "start": 1.0,
                "end": 2.0,
                "text_src": "ありがとう",
                "text_zh": "谢谢",
            },
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert [segment["index"] for segment in payload["segments"]] == [0, 1]
    sub_path = tmp_path / "sample.zh.srt"
    assert payload["result"]["srt_path"] == str(sub_path)
    assert sub_path.exists()
    assert "好的" in sub_path.read_text(encoding="utf-8")


def test_put_segments_rejects_running_task(tmp_path) -> None:
    task = _add_done_task(tmp_path)
    task.stage = Stage.TRANSCRIBING
    client = TestClient(server.app)

    response = client.put(
        f"/tasks/{task.id}/segments",
        json=[
            {
                "start": 0.0,
                "end": 1.0,
                "text_src": "はい",
                "text_zh": "好的",
            },
        ],
    )

    assert response.status_code == 409


def test_put_segments_rejects_invalid_time_range(tmp_path) -> None:
    task = _add_done_task(tmp_path)
    client = TestClient(server.app)

    response = client.put(
        f"/tasks/{task.id}/segments",
        json=[
            {
                "start": 2.0,
                "end": 1.0,
                "text_src": "はい",
                "text_zh": "好的",
            },
        ],
    )

    assert response.status_code == 400
