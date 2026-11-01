"""投递记录。"""
from __future__ import annotations

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
        return int(cur.lastrowid or 0)


def upsert_application(job_id: int, resume_id: int, channel: str = "",
                       notes: str = "") -> int:
    """一个岗位只保留一条投递记录；已存在则更新（不会插入第二行）。"""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO application(job_id, resume_id, status, channel, notes, submitted_at) "
            "VALUES (?, ?, 'submitted', ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET "
            "  status = 'submitted', channel = excluded.channel, "
            "  notes = excluded.notes, submitted_at = excluded.submitted_at, "
            "  updated_at = ?",
            (job_id, resume_id, channel, notes, now_local(), now_local()),
        )
        row = conn.execute(
            "SELECT id FROM application WHERE job_id = ?", (job_id,)).fetchone()
    return int(row["id"]) if row else 0


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


def find_by_job(job_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM application WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)
        ).fetchone()
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
