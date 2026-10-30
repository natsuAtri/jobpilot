"""后台任务运行的持久化（搜索 / 生成）。

内存里跑、库里留一份快照：重启后页面还能看到上次的进度和结果，
而不是白屏。
"""
from __future__ import annotations

import json
from typing import Any

from ..db import get_conn, now_local


def save(run_id: str, kind: str, status: str, step: str | None,
         payload: dict[str, Any] | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO task_run(id, kind, status, step, payload_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status = excluded.status, step = excluded.step, "
            "payload_json = excluded.payload_json, updated_at = excluded.updated_at",
            (run_id, kind, status, step or "",
             json.dumps(payload or {}, ensure_ascii=False), now_local()),
        )


def load(run_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM task_run WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    try:
        out["payload"] = json.loads(out.pop("payload_json") or "{}")
    except json.JSONDecodeError:
        out["payload"] = {}
    return out


def mark_interrupted() -> int:
    """启动时把上次没跑完的任务标成中断，避免页面一直转圈。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE task_run SET status = 'interrupted', step = '服务重启，任务已中断' "
            "WHERE status = 'running'"
        )
        return int(cur.rowcount)
