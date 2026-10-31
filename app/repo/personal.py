"""个人数据库。

一个「语义字段 → 值」的可扩展库。数据来源分三种：
    resume  从简历解析自动填充
    manual  用户手动填
    apply   投递填表时遇到缺失字段，用户补填后回写

字段用语义名（key）组织，投递填表时按 key 取值，不关心具体来自哪个来源。
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from ..config import UPLOADS_DIR
from ..db import get_conn, now_local
from ..models import ResumeDoc

# 字段目录：key、中文名、是否敏感
FIELD_CATALOG: tuple[tuple[str, str, bool], ...] = (
    # 基本信息
    ("name", "姓名", False),
    ("phone", "手机号", False),
    ("email", "邮箱", False),
    ("city", "城市", False),
    ("political_status", "政治面貌", False),
    ("hometown", "籍贯", False),
    ("birthday", "出生日期", False),
    # 教育
    ("edu_school", "学校", False),
    ("edu_major", "专业", False),
    ("edu_degree", "学历", False),
    ("edu_start", "入学时间", False),
    ("edu_end", "毕业时间", False),
    # 技能与经历
    ("skills", "专业技能", False),
    ("languages", "语言能力", False),
    ("certificates", "证书", False),
    ("job_intent", "求职意向", False),
    ("self_eval", "自我评价", False),
)

FIELD_LABELS = {key: label for key, label, _ in FIELD_CATALOG}
FIELD_SENSITIVE = {key: sensitive for key, _, sensitive in FIELD_CATALOG}


def get(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM personal_data WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_value(key: str, value: str, source: str = "manual") -> None:
    value = (value or "").strip()
    if not value:
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO personal_data(key, value, source, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "source = excluded.source, updated_at = excluded.updated_at",
            (key, value, source, now_local()),
        )


def all_values() -> dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM personal_data").fetchall()
    return {r["key"]: r["value"] for r in rows}


def catalog_rows() -> list[dict[str, Any]]:
    """字段目录 + 当前值 + 是否缺失。"""
    values = all_values()
    rows = []
    for key, label, sensitive in FIELD_CATALOG:
        value = values.get(key, "")
        rows.append({
            "key": key,
            "label": label,
            "sensitive": sensitive,
            "value": value,
            "filled": bool(value),
        })
    return rows


def missing_keys() -> list[str]:
    return [r["key"] for r in catalog_rows() if not r["filled"]]


def build_from_resume(doc: ResumeDoc) -> int:
    """从简历解析结果自动填充个人数据库，返回写入的字段数。"""
    data: dict[str, str] = {}
    if doc.name:
        data["name"] = doc.name
    if doc.phone:
        data["phone"] = doc.phone
    if doc.email:
        data["email"] = doc.email
    if doc.location:
        data["city"] = doc.location
    if doc.intents:
        data["job_intent"] = doc.intents

    edu = doc.section("education")
    if edu and edu.items:
        item = edu.items[0]
        if item.org:
            data["edu_school"] = item.org
        if item.meta.get("专业"):
            data["edu_major"] = item.meta["专业"]
        if item.start:
            data["edu_start"] = item.start
        if item.end:
            data["edu_end"] = item.end
        # 学历：从 role/org 文本里找
        for degree in ("博士", "硕士", "本科", "大专"):
            if degree in (item.role or "") or degree in (item.org or ""):
                data["edu_degree"] = degree
                break

    skills = [
        item.org for section in doc.sections if section.type == "skill"
        for item in section.items if item.org
    ]
    if skills:
        data["skills"] = "、".join(skills)

    for key, value in data.items():
        set_value(key, value, source="resume")
    return len(data)


def apply_to_doc(doc: ResumeDoc) -> int:
    """用个人数据库补全简历里为空的字段，返回补了几处。"""
    from ..models import Item, Section

    values = all_values()
    filled = 0
    for key, attr in (("name", "name"), ("phone", "phone"),
                      ("email", "email"), ("city", "location")):
        if not getattr(doc, attr, None) and values.get(key):
            setattr(doc, attr, values[key])
            filled += 1

    if doc.section("education") is None and values.get("edu_school"):
        doc.sections.append(Section(type="education", items=[Item(
            org=values["edu_school"],
            role=values.get("edu_degree"),
            start=values.get("edu_start"),
            end=values.get("edu_end"),
            meta={"专业": values["edu_major"]} if values.get("edu_major") else {},
        )]))
        filled += 1
    return filled

# ---------------------------------------------------------------- 照片
PHOTO_KEY = "photo"


def photo_path() -> Path | None:
    """个人库里的照片文件路径（没有就返回 None）。"""
    name = get(PHOTO_KEY)
    if not name:
        return None
    path = Path(UPLOADS_DIR) / name
    return path if path.exists() else None


def set_photo(data: bytes, ext: str = "jpg", source: str = "manual") -> str:
    """保存照片到 data/uploads，并在个人库记下文件名。"""
    ext = "png" if ext.lower() == "png" else "jpg"
    name = f"photo.{ext}"
    path = Path(UPLOADS_DIR) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    # 换格式时清掉另一份，避免目录里堆垃圾
    other = Path(UPLOADS_DIR) / ("photo.png" if ext == "jpg" else "photo.jpg")
    if other.exists():
        other.unlink(missing_ok=True)
    set_value(PHOTO_KEY, name, source=source)
    return name


def delete_photo() -> None:
    path = photo_path()
    if path is not None:
        path.unlink(missing_ok=True)
    with get_conn() as conn:
        conn.execute("DELETE FROM personal_data WHERE key = ?", (PHOTO_KEY,))


def photo_data_uri() -> str | None:
    """转成 data URI，模板里直接 <img src="…">，导出时不必再读文件。"""
    path = photo_path()
    if path is None:
        return None
    try:
        raw = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{raw}"
