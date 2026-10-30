"""对话会话与轮次的持久化。"""
from __future__ import annotations

import json
from typing import Any

from ..db import get_conn


def create_session(profile_id: int, resume_id: int, mode: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO dialogue_session(profile_id, resume_id, mode, state_json) "
            "VALUES (?, ?, ?, '{}')",
            (profile_id, resume_id, mode),
        )
        return int(cur.lastrowid)


def get_session(session_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM dialogue_session WHERE id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def latest_session(resume_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM dialogue_session WHERE resume_id = ? ORDER BY id DESC LIMIT 1",
            (resume_id,),
        ).fetchone()
    return dict(row) if row else None


def add_turn(
    session_id: int,
    role: str,
    content: str,
    *,
    patch_json: str | None = None,
    accepted: int | None = None,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO dialogue_turn(session_id, role, content, patch_json, accepted) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, patch_json, accepted),
        )
        return int(cur.lastrowid)


def list_turns(session_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM dialogue_turn WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["patches"] = json.loads(item["patch_json"]) if item["patch_json"] else []
        out.append(item)
    return out


def get_turn(turn_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM dialogue_turn WHERE id = ?", (turn_id,)).fetchone()
    return dict(row) if row else None


def set_accepted(turn_id: int, accepted: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE dialogue_turn SET accepted = ? WHERE id = ?", (accepted, turn_id))


def update_state(session_id: int, state: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE dialogue_session SET state_json = ? WHERE id = ?",
            (json.dumps(state, ensure_ascii=False), session_id),
        )
