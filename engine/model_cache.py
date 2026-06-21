"""faster-whisper model cache and download helpers."""

from __future__ import annotations

import os
import shutil
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, constants

SUPPORTED_MODELS = {"tiny", "base", "small", "medium", "large-v3"}
CHUNK_SIZE = 1024 * 1024


def normalize_model_name(model: str | None) -> str:
    name = (model or "large-v3").strip()
    if name not in SUPPORTED_MODELS:
        raise ValueError(f"unsupported whisper model: {name}")
    return name


def repo_id_for_model(model: str) -> str:
    return f"Systran/faster-whisper-{normalize_model_name(model)}"


def local_status(model: str) -> dict[str, Any]:
    model = normalize_model_name(model)
    repo_id = repo_id_for_model(model)
    cache_dir = _cache_dir(repo_id)
    snapshot = _snapshot_dir(cache_dir)
    model_bin = snapshot / "model.bin" if snapshot else None
    cached = bool(model_bin and model_bin.exists() and model_bin.stat().st_size > 0)
    return {
        "model": model,
        "repo_id": repo_id,
        "cached": cached,
        "downloaded_bytes": _dir_size(cache_dir) if cache_dir.exists() else 0,
        "total_bytes": model_bin.stat().st_size if cached and model_bin else 0,
    }


def download_model(
    model: str,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    model = normalize_model_name(model)
    repo_id = repo_id_for_model(model)
    emit = on_event or (lambda event: None)
    emit({"stage": "checking", "model": model, "repo_id": repo_id})

    api = HfApi()
    info = api.model_info(repo_id, files_metadata=True)
    revision = info.sha
    siblings = [s for s in info.siblings if not s.rfilename.endswith("/")]
    total = sum(_file_size(s) for s in siblings)
    cache_dir = _cache_dir(repo_id)
    blobs_dir = cache_dir / "blobs"
    snapshot_dir = cache_dir / "snapshots" / revision
    blobs_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "refs").mkdir(parents=True, exist_ok=True)
    (cache_dir / "refs" / "main").write_text(revision, encoding="utf-8")

    completed = 0
    for sibling in siblings:
        size = _file_size(sibling)
        blob_name = _blob_name(sibling)
        blob_path = blobs_dir / blob_name
        if _blob_complete(blob_path, size):
            completed += size
            _ensure_snapshot_entry(snapshot_dir, sibling.rfilename, blob_path)
            emit(_progress_event(model, repo_id, completed, total, sibling.rfilename))
            continue

        url = _resolve_url(repo_id, revision, sibling.rfilename)
        downloaded = _download_blob(url, blob_path, size, completed, total, model, repo_id, emit)
        completed += downloaded
        _ensure_snapshot_entry(snapshot_dir, sibling.rfilename, blob_path)
        emit(_progress_event(model, repo_id, completed, total, sibling.rfilename))

    status = local_status(model)
    emit(
        {
            "stage": "done",
            "model": model,
            "repo_id": repo_id,
            "progress": 1.0,
            "downloaded_bytes": total,
            "total_bytes": total,
        }
    )
    return status


def _cache_dir(repo_id: str) -> Path:
    return Path(constants.HF_HUB_CACHE) / f"models--{repo_id.replace('/', '--')}"


def _snapshot_dir(cache_dir: Path) -> Path | None:
    ref = cache_dir / "refs" / "main"
    if not ref.exists():
        return None
    revision = ref.read_text(encoding="utf-8").strip()
    snapshot = cache_dir / "snapshots" / revision
    return snapshot if snapshot.exists() else None


def _file_size(sibling: Any) -> int:
    lfs = getattr(sibling, "lfs", None)
    if lfs and getattr(lfs, "size", None):
        return int(lfs.size)
    return int(getattr(sibling, "size", None) or 0)


def _blob_name(sibling: Any) -> str:
    lfs = getattr(sibling, "lfs", None)
    if lfs and getattr(lfs, "sha256", None):
        return str(lfs.sha256)
    return str(sibling.blob_id)


def _blob_complete(blob_path: Path, expected_size: int) -> bool:
    if not blob_path.exists():
        return False
    if expected_size <= 0:
        return blob_path.stat().st_size > 0
    return blob_path.stat().st_size == expected_size


def _resolve_url(repo_id: str, revision: str, filename: str) -> str:
    quoted = urllib.parse.quote(filename)
    return f"https://huggingface.co/{repo_id}/resolve/{revision}/{quoted}"


def _download_blob(
    url: str,
    blob_path: Path,
    expected_size: int,
    completed_before: int,
    total: int,
    model: str,
    repo_id: str,
    emit: Callable[[dict[str, Any]], None],
) -> int:
    tmp_path = _resume_path(blob_path)
    start = tmp_path.stat().st_size if tmp_path.exists() else 0
    headers = {"Range": f"bytes={start}-"} if start else {}
    request = urllib.request.Request(url, headers=headers)
    response = urllib.request.urlopen(request, timeout=60)
    if start and response.status != 206:
        start = 0
        tmp_path.unlink(missing_ok=True)
        response.close()
        response = urllib.request.urlopen(url, timeout=60)

    downloaded = start
    last_report = start
    started_at = time.time()
    mode = "ab" if start else "wb"
    with response, tmp_path.open(mode) as fh:
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            fh.write(chunk)
            downloaded += len(chunk)
            if downloaded - last_report >= 25 * CHUNK_SIZE:
                last_report = downloaded
                elapsed = max(time.time() - started_at, 0.001)
                emit(
                    _progress_event(
                        model,
                        repo_id,
                        completed_before + downloaded,
                        total,
                        blob_path.name,
                        speed_bytes=(downloaded - start) / elapsed,
                    )
                )

    if expected_size and tmp_path.stat().st_size != expected_size:
        raise RuntimeError(
            f"incomplete download for {blob_path.name}: "
            f"{tmp_path.stat().st_size} != {expected_size}"
        )
    tmp_path.replace(blob_path)
    return expected_size or blob_path.stat().st_size


def _resume_path(blob_path: Path) -> Path:
    existing = list(blob_path.parent.glob(f"{blob_path.name}*.incomplete"))
    if existing:
        return max(existing, key=lambda p: p.stat().st_size)
    return blob_path.with_name(f"{blob_path.name}.jpsub.incomplete")


def _ensure_snapshot_entry(snapshot_dir: Path, filename: str, blob_path: Path) -> None:
    link_path = snapshot_dir / filename
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()
    target = os.path.relpath(blob_path, start=link_path.parent)
    try:
        os.symlink(target, link_path)
    except OSError:
        shutil.copy2(blob_path, link_path)


def _progress_event(
    model: str,
    repo_id: str,
    downloaded: int,
    total: int,
    file_name: str,
    speed_bytes: float | None = None,
) -> dict[str, Any]:
    progress = downloaded / total if total > 0 else 0.0
    event: dict[str, Any] = {
        "stage": "downloading",
        "model": model,
        "repo_id": repo_id,
        "file": file_name,
        "progress": min(progress, 1.0),
        "downloaded_bytes": downloaded,
        "total_bytes": total,
    }
    if speed_bytes is not None:
        event["speed_bytes"] = speed_bytes
    return event


def _dir_size(path: Path) -> int:
    return sum(
        file.stat().st_size for file in path.rglob("*") if file.is_file() and not file.is_symlink()
    )
