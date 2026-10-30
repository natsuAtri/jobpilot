"""投递记录。"""
from __future__ import annotations

import json
from typing import Any

from ..db import get_conn, now_local

STATUS_CN = {
    "draft": "准备中", "submitted": "已投递", "viewed": "已查看",
    "replied": "有回复", "interview": "面试", "offer": "Offer",
    "rejected": "已拒绝", "withdrawn": "已撤回",
}


def create_application(job_id: int, resume_id: int, channel: str = "", notes: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO application(job_id, resume_id, status, channel, notes, submitted_at) "
            "VALUES (?, ?, 'draft', ?, ?, ?)",
            (job_id, resume_id, channel or None, notes or None, now_local()),
        )
        return int(cur.lastrowid)


def list_applications() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.*, j.title AS job_title, j.company AS company, "
            "r.title AS resume_title "
            "FROM application a "
            "JOIN job j ON j.id = a.job_id "
            "JOIN resume r ON r.id = a.resume_id "
            "ORDER BY a.submitted_at DESC, a.id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_application(app_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM application WHERE id = ?", (app_id,)).fetchone()
    return dict(row) if row else None


def set_status(app_id: int, status: str, note: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE application SET status = ?, notes = ?, updated_at = ? WHERE id = ?",
            (status, note or None, now_local(), app_id),
        )
        conn.execute(
            "INSERT INTO application_event(application_id, at, type, note) VALUES (?, ?, ?, ?)",
            (app_id, now_local(), status, note or None),
        )


def record_submit(app_id: int, notes: str = "") -> None:
    set_status(app_id, "submitted", notes)


def delete_application(app_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM application WHERE id = ?", (app_id,))
