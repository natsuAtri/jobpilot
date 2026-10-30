"""生成进度：让双 agent 的「思考过程」可见，同时落库一份快照。

每条 step 记录一个阶段和详情，前端轮询后用时间线展示：
策略 → 起草 → 评审 → 校验 → 补写建议 → 完成。
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

from ..repo import task_runs

_runs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()

TraceFn = Callable[[str, str, list | None], None]


def _persist(run_id: str, run: dict[str, Any]) -> None:
    try:
        task_runs.save(run_id, "generate", run.get("status", "running"), "", {
            "steps": run.get("steps") or [],
            "error": run.get("error"),
        })
    except Exception:  # noqa: BLE001
        pass


def create() -> str:
    run_id = uuid.uuid4().hex[:12]
    with _lock:
        _runs[run_id] = {
            "status": "running",
            "steps": [],
            "result_count": 0,
            "error": None,
            "done": False,
            "started_at": time.time(),
        }
        _persist(run_id, _runs[run_id])
    return run_id


def add_step(run_id: str, stage: str, message: str, detail: list | None = None) -> None:
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        run["steps"].append({
            "at": time.strftime("%H:%M:%S"),
            "stage": stage,
            "message": message,
            "detail": detail or [],
        })
        _persist(run_id, run)


def finish(run_id: str, message: str) -> None:
    with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        run["status"] = "done"
        run["done"] = True
        run["steps"].append({
            "at": time.strftime("%H:%M:%S"),
            "stage": "finish",
            "message": message,
            "detail": [],
        })
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
        "steps": payload.get("steps") or [],
        "result_count": 0,
        "error": payload.get("error"),
        "done": saved["status"] in ("done", "error", "interrupted"),
        "from_db": True,
    }


def make_trace(run_id: str) -> TraceFn:
    def trace(stage: str, message: str, detail: list | None = None) -> None:
        add_step(run_id, stage, message, detail)
    return trace
