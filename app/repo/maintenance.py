"""数据保留策略：不让观测/日志表无限增长。

三张表会随使用时间无限变大：
    agent_trace        每次模型调用、每个工具调用都写一行
    conversation_message 全量保留对话
    generation_step    每次生成的中间检查点（成功后会清，异常遗留的会堆）

策略刻意保守：只删「老到没人会看」的数据，不动业务表（resume / job / application）。
"""
from __future__ import annotations

from ..db import get_conn

TRACE_KEEP_DAYS = 7
TRACE_KEEP_ROWS = 5000
MESSAGE_KEEP_PER_CONVERSATION = 200
STEP_KEEP_DAYS = 7


def prune_agent_trace(keep_days: int = TRACE_KEEP_DAYS,
                      keep_rows: int = TRACE_KEEP_ROWS) -> int:
    """先按时间删，再按行数兜底（高频使用的几天也能压住）。"""
    deleted = 0
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM agent_trace WHERE created_at < datetime('now', 'localtime', ?)",
            (f"-{int(keep_days)} days",),
        )
        deleted += max(cur.rowcount, 0)
        cur = conn.execute(
            "DELETE FROM agent_trace WHERE id NOT IN "
            "(SELECT id FROM agent_trace ORDER BY id DESC LIMIT ?)",
            (int(keep_rows),),
        )
        deleted += max(cur.rowcount, 0)
    return deleted


def prune_messages(keep: int = MESSAGE_KEEP_PER_CONVERSATION) -> int:
    """每个会话只保留最近 N 条消息。"""
    deleted = 0
    with get_conn() as conn:
        conversations = [row["id"] for row in conn.execute("SELECT id FROM conversation")]
        for conversation_id in conversations:
            cur = conn.execute(
                "DELETE FROM conversation_message WHERE conversation_id = ? AND id NOT IN "
                "(SELECT id FROM conversation_message WHERE conversation_id = ? "
                " ORDER BY id DESC LIMIT ?)",
                (conversation_id, conversation_id, int(keep)),
            )
            deleted += max(cur.rowcount, 0)
    return deleted


def prune_generation_steps(keep_days: int = STEP_KEEP_DAYS) -> int:
    """异常遗留的检查点：对应 run 早就不用了，留着只是占地方。"""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM generation_step WHERE created_at < datetime('now', 'localtime', ?)",
            (f"-{int(keep_days)} days",),
        )
        return max(cur.rowcount, 0)


def prune_all() -> dict[str, int]:
    return {
        "agent_trace": prune_agent_trace(),
        "conversation_message": prune_messages(),
        "generation_step": prune_generation_steps(),
    }
