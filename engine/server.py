"""FastAPI + SSE 服务,给 Tauri sidecar 调用。

启动后第一行 stdout 打印实际端口,Rust 侧读取后注入前端。
  python -m engine.server
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from . import asr, media, subtitle
from .models import Segment, Stage, Task, TaskOptions
from .orchestrator import run

app = FastAPI(title="jpsub-engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 内存任务表(单机单用户够用;多任务可换队列)
_tasks: dict[str, Task] = {}
_cancels: dict[str, threading.Event] = {}


class CreateTaskBody(BaseModel):
    input_path: str
    options: dict[str, Any] = Field(default_factory=dict)


class SegmentBody(BaseModel):
    index: int = 0
    start: float
    end: float
    text_src: str = ""
    text_zh: str = ""


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/capabilities")
def capabilities():
    device, compute_type, default_model = asr.detect_device()
    return {
        "gpu": device == "cuda",
        "compute_type": compute_type,
        "default_model": default_model,
    }


@app.post("/tasks")
def create_task(body: CreateTaskBody):
    options = TaskOptions(**body.options)
    task = Task(input_path=body.input_path, options=options)
    cancel = threading.Event()
    _tasks[task.id] = task
    _cancels[task.id] = cancel

    def worker():
        run(task, on_update=lambda t: None, cancel_event=cancel)

    threading.Thread(target=worker, daemon=True).start()
    return {"task_id": task.id}


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    task = _get_task(task_id)
    return task.to_dict()


@app.post("/tasks/{task_id}/cancel", status_code=204)
def cancel_task(task_id: str):
    ev = _cancels.get(task_id)
    if not ev:
        raise HTTPException(404, "task not found")
    ev.set()


@app.get("/tasks/{task_id}/result")
def get_result(task_id: str):
    task = _get_task(task_id)
    return task.result


@app.get("/tasks/{task_id}/segments")
def get_segments(task_id: str):
    task = _get_task(task_id)
    return [seg.to_dict() for seg in task.segments]


@app.put("/tasks/{task_id}/segments")
def update_segments(task_id: str, body: list[SegmentBody]):
    task = _get_task(task_id)
    if task.stage not in (Stage.DONE, Stage.FAILED, Stage.CANCELED):
        raise HTTPException(409, "task is still running")
    if not body:
        raise HTTPException(400, "segments cannot be empty")

    segments = [_segment_from_body(i, seg) for i, seg in enumerate(body)]
    _validate_segments(segments)
    task.segments = segments
    task.result.update(_export_segments(task))
    task.stage = Stage.DONE
    task.progress = 1.0
    return {
        "segments": [seg.to_dict() for seg in task.segments],
        "result": task.result,
    }


@app.get("/tasks/{task_id}/events")
async def events(task_id: str):
    """SSE:轮询任务状态并推送 stage/progress。"""

    async def gen():
        last = None
        while True:
            task = _tasks.get(task_id)
            if not task:
                break
            snap = (task.stage.value, task.progress)
            if snap != last:
                last = snap
                yield {"data": _dump(task)}
            if task.stage in (Stage.DONE, Stage.FAILED, Stage.CANCELED):
                break
            await asyncio.sleep(0.3)

    return EventSourceResponse(gen())


def _dump(task: Task) -> str:
    return json.dumps(
        {
            "stage": task.stage.value,
            "progress": task.progress,
            "error": task.error.__dict__ if task.error else None,
        },
        ensure_ascii=False,
    )


def _get_task(task_id: str) -> Task:
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task


def _segment_from_body(index: int, seg: SegmentBody) -> Segment:
    return Segment(
        index=index,
        start=seg.start,
        end=seg.end,
        text_src=seg.text_src,
        text_zh=seg.text_zh,
    )


def _validate_segments(segments: list[Segment]) -> None:
    previous_start = 0.0
    for seg in segments:
        if seg.start < 0 or seg.end <= seg.start:
            raise HTTPException(400, f"invalid time range at segment {seg.index}")
        if seg.index > 0 and seg.start < previous_start:
            raise HTTPException(
                400, f"segments must be sorted by start time at segment {seg.index}"
            )
        previous_start = seg.start


def _export_segments(task: Task) -> dict[str, str]:
    ext = task.options.output_format if task.options.output_format in ("srt", "ass") else "srt"
    sub_path = str(Path(task.input_path).with_suffix(f".zh.{ext}"))
    subtitle.write(task.segments, sub_path, task.options)
    result = {f"{ext}_path": sub_path}
    if task.options.burn_in:
        out_video = str(Path(task.input_path).with_suffix(".subbed.mp4"))
        media.burn_in(task.input_path, sub_path, out_video)
        result["video_path"] = out_video
    return result


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    import uvicorn

    port = _free_port()
    print(port, flush=True)  # 首行输出端口,供 Rust 读取
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
