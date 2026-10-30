"""双 agent 生成提案的存取。"""
from __future__ import annotations

import json
from typing import Any

from ..db import get_conn
from ..generate.engine import Proposal


def get_or_create_run(base_resume_id: int, job_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM generation_run WHERE base_resume_id = ? AND job_id = ?",
            (base_resume_id, job_id),
        ).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO generation_run(base_resume_id, job_id) VALUES (?, ?)",
            (base_resume_id, job_id),
        )
        return int(cur.lastrowid)


def find_run(base_resume_id: int, job_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM generation_run WHERE base_resume_id = ? AND job_id = ?",
            (base_resume_id, job_id),
        ).fetchone()
    return dict(row) if row else None


def replace_items(run_id: int, proposals: list[Proposal]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM generation_item WHERE run_id = ?", (run_id,))
        for p in proposals:
            conn.execute(
                "INSERT INTO generation_item(run_id, source_id, label, original, rewritten, "
                "checks_json, blocked, critique, priority) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    p.source_id,
                    p.label,
                    p.original,
                    p.rewritten,
                    json.dumps([{"name": c.name, "ok": c.ok, "detail": c.detail} for c in p.checks],
                               ensure_ascii=False),
                    1 if p.blocked else 0,
                    p.critique or None,
                    p.priority,
                ),
            )


def list_items(run_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM generation_item WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["checks"] = json.loads(item["checks_json"] or "[]")
        out.append(item)
    return out


def set_accepted(item_id: int, accepted: int | None) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE generation_item SET accepted = ? WHERE id = ?", (accepted, item_id))


def accept_all_passed(run_id: int) -> int:
    """一键采用：所有过闸门（未被拦截）且有实质改动的条目。返回采用条数。"""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE generation_item SET accepted = 1 "
            "WHERE run_id = ? AND blocked = 0 AND rewritten <> original",
            (run_id,),
        )
        return int(cur.rowcount)


def set_manual(item_id: int, text: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE generation_item SET manual = ? WHERE id = ?", (text.strip(), item_id))


def mark_saved(run_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE generation_run SET status = 'saved' WHERE id = ?", (run_id,))


def save_suggestions(run_id: int, suggestions: list[dict]) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE generation_run SET suggestions_json = ? WHERE id = ?",
            (json.dumps(suggestions, ensure_ascii=False), run_id),
        )


def get_suggestions(run_id: int) -> list[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT suggestions_json FROM generation_run WHERE id = ?", (run_id,)
        ).fetchone()
    if row is None:
        return []
    try:
        return json.loads(row["suggestions_json"] or "[]")
    except json.JSONDecodeError:
        return []
