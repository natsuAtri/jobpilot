"""会话与消息的存取。"""
from __future__ import annotations

import json
from typing import Any

from ..db import get_conn, now_local


def get_active() -> int:
    """单用户：固定使用最早的一条会话，没有就创建。"""
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM conversation ORDER BY id LIMIT 1").fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute("INSERT INTO conversation(state_json) VALUES ('{}')")
        return int(cur.lastrowid or 0)


def get_state(conversation_id: int) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT state_json FROM conversation WHERE id = ?", (conversation_id,)
        ).fetchone()
    if row is None:
        return {}
    try:
        return json.loads(row["state_json"] or "{}")
    except json.JSONDecodeError:
        return {}


def set_state(conversation_id: int, state: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE conversation SET state_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(state, ensure_ascii=False), now_local(), conversation_id),
        )


def add_message(conversation_id: int, role: str, content: str,
                payload: dict | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO conversation_message(conversation_id, role, content, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conversation_id, role, content,
             json.dumps(payload or {}, ensure_ascii=False), now_local()),
        )
        return int(cur.lastrowid or 0)


def list_messages(conversation_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM conversation_message WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            item["payload"] = {}
        out.append(item)
    return out


def clear_messages(conversation_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM conversation_message WHERE conversation_id = ?", (conversation_id,)
        )
