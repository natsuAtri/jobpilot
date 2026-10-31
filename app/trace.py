"""Agent 执行轨迹：一次用户输入（turn）里发生了什么。

为什么需要它：
改 prompt、调工具、换模型之后，唯一能回答「这次和上次差在哪」的就是
每一步的输入输出。没有 trace，只能靠读库和猜。

设计要点：
- turn_id 用 contextvar 传递，业务代码不用层层透传
- 写库失败绝不抛异常（观测不该影响主流程）
- 只存摘要，不存完整 prompt（隐私 + 体积）
"""
from __future__ import annotations

import contextvars
import json
import time
import uuid
from typing import Any

from .db import get_conn

_current_turn: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_turn", default=None
)
_step: contextvars.ContextVar[int] = contextvars.ContextVar("trace_step", default=0)

MAX_FIELD = 1200


def new_turn() -> str:
    turn_id = uuid.uuid4().hex[:16]
    _current_turn.set(turn_id)
    _step.set(0)
    return turn_id


def current_turn() -> str | None:
    return _current_turn.get()


def _short(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text[:MAX_FIELD]


def record(kind: str, name: str, *, args: Any = None, result: Any = None,
           latency_ms: int | None = None, tokens: int | None = None,
           turn_id: str | None = None) -> None:
    """记一步。任何异常都吞掉——观测不能影响业务。"""
    tid = turn_id or _current_turn.get()
    if not tid:
        return
    step = _step.get()
    _step.set(step + 1)
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_trace(turn_id, step, kind, name, args_json, "
                "result_json, latency_ms, tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, step, kind, name, _short(args), _short(result), latency_ms, tokens),
            )
    except Exception:  # noqa: BLE001
        pass


class span:
    """with trace.span("llm", "resume_draft", args=...) as s: s.tokens = 123"""

    def __init__(self, kind: str, name: str, *, args: Any = None) -> None:
        self.kind = kind
        self.name = name
        self.args = args
        self.result: Any = None
        self.tokens: int | None = None
        self._t0 = 0.0

    def __enter__(self) -> "span":
        self._t0 = time.time()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        latency = int((time.time() - self._t0) * 1000)
        if exc is not None:
            record("error", self.name, args=self.args,
                   result={"error": f"{exc_type.__name__}: {exc}"}, latency_ms=latency)
            return
        record(self.kind, self.name, args=self.args, result=self.result,
               latency_ms=latency, tokens=self.tokens)


def list_turn(turn_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_trace WHERE turn_id = ? ORDER BY id", (turn_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def recent(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT turn_id, MIN(created_at) AS started_at, COUNT(*) AS steps "
            "FROM agent_trace GROUP BY turn_id ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
