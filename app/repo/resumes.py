"""简历的读写。

写入策略：整份替换（删旧章节再插新）。单人单机场景下最简单，
也不会出现「改了一半」的中间态。
"""
from __future__ import annotations

import json
from typing import Any

from ..db import get_conn, now_local
from ..models import Bullet, Item, ResumeDoc, Section


def ensure_profile(name: str = "") -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM profile ORDER BY id LIMIT 1").fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute("INSERT INTO profile(name) VALUES (?)", (name,))
        return int(cur.lastrowid or 0)


def find_by_source_hash(source_hash: str) -> int | None:
    """同一份 PDF 重复导入时，找到之前那条简历。"""
    if not source_hash:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM resume WHERE source_hash = ? ORDER BY id LIMIT 1", (source_hash,)
        ).fetchone()
    return int(row["id"]) if row else None


def _reusable_id(kind: str, title: str, base_resume_id: int | None) -> int | None:
    """哪些情况下应该复用已有简历，而不是新建一份。

    - generated（从零创建）：始终复用同一份空白工作区，避免满屏「新简历」
    - tailored（定制版）：同一份基础简历 + 同一个岗位标题 → 复用（重新生成覆盖）
    - imported：不复用，由调用方用文件指纹决定（见 find_by_source_hash）
    """
    with get_conn() as conn:
        if kind == "generated":
            row = conn.execute(
                "SELECT id FROM resume WHERE kind = 'generated' ORDER BY id LIMIT 1").fetchone()
        elif kind == "tailored":
            row = conn.execute(
                "SELECT id FROM resume WHERE kind = 'tailored' AND title = ? "
                "AND IFNULL(base_resume_id, -1) = IFNULL(?, -1) ORDER BY id LIMIT 1",
                (title, base_resume_id)).fetchone()
        else:
            row = None
    return int(row["id"]) if row else None


def create_resume(
    doc: ResumeDoc,
    *,
    source_file: str | None = None,
    kind: str = "imported",
    title: str = "",
    profile_id: int | None = None,
    base_resume_id: int | None = None,
    source_hash: str | None = None,
    reuse: int | None = None,
) -> int:
    """新建一份简历；只在对的位置复用已有记录。

    多份简历并存是正常状态：导入两份不同的 PDF 就是两份简历。
    """
    pid = profile_id or ensure_profile(doc.name)
    base_title = title or (doc.name + " 的简历")

    resume_id = reuse or _reusable_id(kind, base_title, base_resume_id)
    with get_conn() as conn:
        if resume_id:
            conn.execute(
                "UPDATE resume SET title = ?, source_file = ?, parse_status = 'parsed', "
                "base_resume_id = ?, source_hash = COALESCE(?, source_hash), created_at = ? "
                "WHERE id = ?",
                (base_title, source_file, base_resume_id, source_hash, now_local(), resume_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO resume(profile_id, kind, title, source_file, parse_status, "
                "base_resume_id, source_hash, created_at) VALUES (?, ?, ?, ?, 'parsed', ?, ?, ?)",
                (pid, kind, base_title, source_file, base_resume_id, source_hash, now_local()),
            )
            resume_id = int(cur.lastrowid or 0)
    # 解析出的联系方式必须落库，否则校对界面里就是空的
    update_profile(resume_id, doc)
    replace_doc(resume_id, doc)
    return resume_id


def _unique_title(conn, base: str) -> str:
    """同名校验：重名时追加序号，避免列表里分不清两份简历。"""
    title = base
    n = 1
    while conn.execute("SELECT 1 FROM resume WHERE title = ?", (title,)).fetchone():
        n += 1
        title = f"{base}（{n}）"
    return title


def replace_doc(resume_id: int, doc: ResumeDoc) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM resume_section WHERE resume_id = ?", (resume_id,))
        for s_index, section in enumerate(doc.sections):
            cur = conn.execute(
                "INSERT INTO resume_section(resume_id, type, title_override, order_index) "
                "VALUES (?, ?, ?, ?)",
                (resume_id, section.type, section.title, s_index),
            )
            section_id = int(cur.lastrowid or 0)
            for i_index, item in enumerate(section.items):
                cur = conn.execute(
                    "INSERT INTO resume_item(section_id, org, role, location, start_date, "
                    "end_date, meta_json, order_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        section_id,
                        item.org,
                        item.role,
                        item.location,
                        item.start,
                        item.end,
                        json.dumps(item.meta, ensure_ascii=False),
                        i_index,
                    ),
                )
                item_id = int(cur.lastrowid or 0)
                for b_index, bullet in enumerate(item.bullets):
                    conn.execute(
                        "INSERT INTO resume_bullet(item_id, text, order_index, tags_json, "
                        "strength, metrics_json, source, user_utterance, source_bullet_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            item_id,
                            bullet.text,
                            b_index,
                            json.dumps(bullet.tags, ensure_ascii=False),
                            bullet.strength,
                            json.dumps(bullet.metrics, ensure_ascii=False),
                            bullet.source,
                            bullet.user_utterance,
                            bullet.source_bullet_id,
                        ),
                    )
        conn.execute(
            "UPDATE resume SET parse_status = 'parsed' WHERE id = ?", (resume_id,)
        )


def load_doc(resume_id: int) -> ResumeDoc | None:
    with get_conn() as conn:
        resume = conn.execute("SELECT * FROM resume WHERE id = ?", (resume_id,)).fetchone()
        if resume is None:
            return None
        profile = conn.execute(
            "SELECT * FROM profile WHERE id = ?", (resume["profile_id"],)
        ).fetchone()
        sections = conn.execute(
            "SELECT * FROM resume_section WHERE resume_id = ? ORDER BY order_index",
            (resume_id,),
        ).fetchall()

        doc = ResumeDoc(
            name=profile["name"] if profile else "",
            phone=profile["phone"] if profile else None,
            email=profile["email"] if profile else None,
            location=profile["location"] if profile else None,
            links=json.loads(profile["links_json"]) if profile else {},
        )
        for srow in sections:
            section = Section(
                type=srow["type"],  # 由 Pydantic 按 Literal 校验
                title=srow["title_override"],
                order_index=srow["order_index"],
            )
            items = conn.execute(
                "SELECT * FROM resume_item WHERE section_id = ? ORDER BY order_index",
                (srow["id"],),
            ).fetchall()
            for irow in items:
                item = Item(
                    org=irow["org"],
                    role=irow["role"],
                    location=irow["location"],
                    start=irow["start_date"],
                    end=irow["end_date"],
                    meta=json.loads(irow["meta_json"] or "{}"),
                    order_index=irow["order_index"],
                )
                bullets = conn.execute(
                    "SELECT * FROM resume_bullet WHERE item_id = ? ORDER BY order_index",
                    (irow["id"],),
                ).fetchall()
                for brow in bullets:
                    item.bullets.append(
                        Bullet(
                            text=brow["text"],
                            order_index=brow["order_index"],
                            tags=json.loads(brow["tags_json"] or "[]"),
                            strength=brow["strength"],
                            metrics=json.loads(brow["metrics_json"] or "[]"),
                            source=brow["source"],
                            user_utterance=brow["user_utterance"],
                            source_bullet_id=brow["source_bullet_id"],
                        )
                    )
                section.items.append(item)
            doc.sections.append(section)
    return doc


def list_resumes() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT r.*, p.name AS profile_name, "
            "  (SELECT COUNT(*) FROM resume_section s WHERE s.resume_id = r.id) AS section_count "
            "FROM resume r JOIN profile p ON p.id = r.profile_id "
            "ORDER BY r.created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def reset_derived(resume_id: int) -> dict[str, int]:
    """简历内容被替换后，所有从它派生的东西都必须一起失效。

    create_resume 是「同一 kind 只保留一份、原地覆盖」：重新导入时 resume_id 不变，
    但章节内容全换了。不失效的话会出现「新简历 + 旧追问」：
    对话补充页还在问你旧简历里的项目，终审页还在列旧简历的改写。
    """
    with get_conn() as conn:
        dialogue = conn.execute(
            "DELETE FROM dialogue_session WHERE resume_id = ?", (resume_id,)).rowcount
        # generation_step 没有外键，得手动按 run 清，否则会留下孤儿检查点
        conn.execute(
            "DELETE FROM generation_step WHERE run_id IN "
            "(SELECT id FROM generation_run WHERE base_resume_id = ?)", (resume_id,))
        runs = conn.execute(
            "DELETE FROM generation_run WHERE base_resume_id = ?", (resume_id,)).rowcount
        matches = conn.execute(
            "DELETE FROM match_result WHERE resume_id = ?", (resume_id,)).rowcount
    return {
        "dialogue_session": max(dialogue, 0),
        "generation_run": max(runs, 0),
        "match_result": max(matches, 0),
    }


def delete_resume(resume_id: int) -> int:
    """删除简历，连带删除引用它的投递记录。

    application.resume_id 是 ON DELETE RESTRICT，不先删投递记录会触发外键错误。
    返回一并删掉的投递记录数。
    """
    with get_conn() as conn:
        apps = conn.execute(
            "SELECT COUNT(*) c FROM application WHERE resume_id = ?", (resume_id,)
        ).fetchone()["c"]
        conn.execute("DELETE FROM application WHERE resume_id = ?", (resume_id,))
        conn.execute("DELETE FROM resume WHERE id = ?", (resume_id,))
        return int(apps)


def add_suggested_item(resume_id: int, section_type: str, title: str, detail: str) -> bool:
    """把一条「补写建议」落成简历里的新条目，用户再去编辑填实。"""
    doc = load_doc(resume_id)
    if doc is None:
        return False
    section = doc.section(section_type)  # type: ignore[arg-type]
    if section is None:
        section = Section(type=section_type, items=[])  # type: ignore[arg-type]
        doc.sections.append(section)
    section.items.append(
        Item(org=title.strip() or "（待补写）",
             bullets=[Bullet(text=(detail or "（在这里补充具体内容）").strip(), source="manual")])
    )
    replace_doc(resume_id, doc)
    return True


def update_profile(resume_id: int, doc: ResumeDoc) -> None:
    """更新共享档案。

    只写入非空值——profile 是全库共享的一行，简历却可能有多份。
    从零创建一份空白简历时，如果按「空值即清空」写回去，会把主档案的
    姓名、电话一起抹掉，所有简历跟着遭殃。这里用合并语义：
    空字符串表示「这份简历没填」，而不是「这个人没有」。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT profile_id FROM resume WHERE id = ?", (resume_id,)
        ).fetchone()
        if row is None:
            return
        current = conn.execute(
            "SELECT * FROM profile WHERE id = ?", (row["profile_id"],)
        ).fetchone()
        if current is None:
            return

        def pick(new: str | None, old: str | None) -> str | None:
            value = (new or "").strip()
            return value or old

        conn.execute(
            "UPDATE profile SET name = ?, phone = ?, email = ?, location = ?, "
            "links_json = ?, updated_at = datetime('now') WHERE id = ?",
            (
                pick(doc.name, current["name"]),
                pick(doc.phone, current["phone"]),
                pick(doc.email, current["email"]),
                pick(doc.location, current["location"]),
                json.dumps(doc.links, ensure_ascii=False) if doc.links else current["links_json"],
                row["profile_id"],
            ),
        )


def mark_verified(resume_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE resume SET parse_status = 'verified' WHERE id = ?", (resume_id,)
        )
