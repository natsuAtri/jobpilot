"""岗位的读写与匹配结果存储。"""
from __future__ import annotations

import json
from typing import Any

from ..db import get_conn
from ..match import MatchResult


def create_job(
    *,
    title: str,
    company: str = "",
    city: str = "",
    description: str = "",
    url: str = "",
    platform: str = "manual",
    job_type: str = "unknown",
    salary_min: int | None = None,
    salary_max: int | None = None,
    salary_months: int | None = None,
    external_id: str | None = None,
    dedupe_key: str | None = None,
) -> int:
    with get_conn() as conn:
        # 幂等导入：同一 dedupe_key 已存在时直接复用，不重复插入、也不崩
        # （否则 UNIQUE 约束会抛 IntegrityError，把整个导入打成 500）。
        if dedupe_key:
            row = conn.execute(
                "SELECT id FROM job WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
            if row:
                return int(row["id"])
        cur = conn.execute(
            "INSERT INTO job(platform, url, title, company, city, description_raw, source, "
            "job_type, salary_min, salary_max, salary_months, external_id, dedupe_key) "
            "VALUES (?, ?, ?, ?, ?, ?, 'search', ?, ?, ?, ?, ?, ?)",
            (platform, url or None, title.strip(), company.strip() or None,
             city.strip() or None, description, job_type, salary_min, salary_max,
             salary_months, external_id, dedupe_key),
        )
        return int(cur.lastrowid or 0)


def list_jobs() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT j.*, "
            "  (SELECT COUNT(*) FROM match_result m WHERE m.job_id = j.id) AS match_count "
            "FROM job j ORDER BY j.id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_job(job_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM job WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def find_by_dedupe_key(dedupe_key: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM job WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
    return dict(row) if row else None


def delete_job(job_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM job WHERE id = ?", (job_id,))


def save_match(job_id: int, resume_id: int, result: MatchResult, evidence: list | None = None) -> None:
    data = result.as_dict()
    education = data.get("education") or {}
    evidence = evidence or []
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO match_result(job_id, resume_id, total, dimensions_json, weights_json, "
            "education_status, education_soft, strengths_json, gaps_json, notes_json, evidence_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id, resume_id) DO UPDATE SET "
            "total = excluded.total, dimensions_json = excluded.dimensions_json, "
            "weights_json = excluded.weights_json, education_status = excluded.education_status, "
            "education_soft = excluded.education_soft, strengths_json = excluded.strengths_json, "
            "gaps_json = excluded.gaps_json, notes_json = excluded.notes_json, "
            "evidence_json = excluded.evidence_json, created_at = datetime('now')",
            (
                job_id,
                resume_id,
                data["total"],
                json.dumps(data["dimensions"], ensure_ascii=False),
                json.dumps({d["name"]: d["weight"] for d in data["dimensions"]}, ensure_ascii=False),
                education.get("status", "unknown"),
                1 if education.get("soft") else 0,
                json.dumps(data["keyword_coverage"], ensure_ascii=False),
                json.dumps(data["gaps"], ensure_ascii=False),
                json.dumps(data["notes"], ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
            ),
        )


def get_match(job_id: int, resume_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM match_result WHERE job_id = ? AND resume_id = ?",
            (job_id, resume_id),
        ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["dimensions"] = json.loads(out["dimensions_json"])
    out["keyword_coverage"] = json.loads(out["strengths_json"] or "{}")
    out["gaps"] = json.loads(out["gaps_json"] or "[]")
    out["notes"] = json.loads(out["notes_json"] or "[]")
    out["evidence"] = json.loads(out["evidence_json"] or "[]")
    return out
