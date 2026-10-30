"""搜索进度：内存里一份运行状态，同时落库一份快照。

单用户本地应用，不需要消息队列。一个字典 + 一把锁就够；落库只是为了让
服务重启后页面还能看到上次的结果。
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from ..repo import task_runs

_runs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _persist(run_id: str, run: dict[str, Any]) -> None:
    try:
        task_runs.save(run_id, "search", run.get("status", "running"), run.get("step"), {
            "jobs": run.get("jobs") or [],
            "found": run.get("found") or 0,
            "error": run.get("error"),
        })
    except Exception:  # noqa: BLE001 - 落库失败不影响运行
        pass


def create() -> str:
    run_id = uuid.uuid4().hex[:12]
    with _lock:
        _runs[run_id] = {
            "status": "running",
            "step": "准备中",
            "found": 0,
            "jobs": [],
            "error": None,
            "done": False,
            "started_at": time.time(),
        }
        _persist(run_id, _runs[run_id])
    return run_id


def update(run_id: str, *, step: str | None = None, found: int | None = None,
           jobs: list | None = None, status: str | None = None,
           error: str | None = None) -> None:
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        if step is not None:
            run["step"] = step
        if found is not None:
            run["found"] = found
        if jobs is not None:
            run["jobs"] = jobs
        if status is not None:
            run["status"] = status
        if error is not None:
            run["error"] = error
        _persist(run_id, run)


def finish(run_id: str, jobs: list) -> None:
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        run["status"] = "done"
        run["done"] = True
        run["jobs"] = jobs
        run["found"] = len(jobs)
        run["step"] = f"完成，共 {len(jobs)} 个职位"
        _persist(run_id, run)


def fail(run_id: str, error: str) -> None:
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        run["status"] = "error"
        run["done"] = True
        run["error"] = error
        _persist(run_id, run)


def get(run_id: str) -> dict[str, Any] | None:
    with _lock:
        run = _runs.get(run_id)
        if run is not None:
            return dict(run)

    saved = task_runs.load(run_id)
    if saved is None:
        return None
    payload = saved.get("payload") or {}
    return {
        "status": saved["status"],
        "step": saved.get("step") or "",
        "found": payload.get("found") or 0,
        "jobs": payload.get("jobs") or [],
        "error": payload.get("error"),
        "done": saved["status"] in ("done", "error", "interrupted"),
        "from_db": True,
    }
