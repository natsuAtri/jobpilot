"""表单 → 文档的重建逻辑。

表格行可以在前端任意增删，下标因此不连续（删掉第 1 条后，第 2 条的下标
不会重排）。所以这里不按「遍历文档、按下标取值」更新，而是**按表单重建**：
表单里有什么就生成什么，顺序由下标排序决定。

代价是每条已存在的要点要把自己的标签、强度、指标通过隐藏字段带上来，
否则一次保存就会把这些元数据冲掉。
"""
from __future__ import annotations

import json
import re

from .models import Bullet, Item, ResumeDoc, Section

SECTION_TYPES = {
    "education", "experience", "project", "skill", "award", "cert", "other",
}

SEC_TYPE = re.compile(r"^s:(\d+):type$")
ITEM_FIELD = re.compile(r"^i:(\d+):(\d+):(org|role|start|end|location)$")
META_VALUE = re.compile(r"^m:(\d+):(\d+):(\d+)$")
META_LABEL = re.compile(r"^ml:(\d+):(\d+):(\d+)$")
BULLET_TEXT = re.compile(r"^b:(\d+):(\d+):(\d+)$")
BULLET_META = re.compile(r"^bm:(\d+):(\d+):(\d+)$")


def _default_bullet_meta() -> dict:
    return {"tags": [], "strength": 3, "metrics": [], "source": "manual"}


def apply_form(doc: ResumeDoc, form) -> ResumeDoc:
    profile = {
        "name": (form.get("p:name") or "").strip(),
        "phone": (form.get("p:phone") or "").strip() or None,
        "email": (form.get("p:email") or "").strip() or None,
        "location": (form.get("p:location") or "").strip() or None,
    }

    sec_type: dict[int, str] = {}
    items: dict[tuple[int, int], dict] = {}
    meta: dict[tuple[int, int], dict[int, list[str]]] = {}
    bullets: dict[tuple[int, int], dict[int, str]] = {}
    bullet_meta: dict[tuple[int, int, int], dict] = {}

    for key in form.keys():
        value = (form.get(key) or "").strip()

        if m := SEC_TYPE.match(key):
            if value in SECTION_TYPES:
                sec_type[int(m.group(1))] = value
            continue

        if m := ITEM_FIELD.match(key):
            si, ii, field = int(m.group(1)), int(m.group(2)), m.group(3)
            items.setdefault((si, ii), {})[field] = value or None
            continue

        if m := META_VALUE.match(key):
            si, ii, mi = map(int, m.groups())
            slot = meta.setdefault((si, ii), {}).setdefault(mi, ["", ""])
            slot[1] = value
            continue

        if m := META_LABEL.match(key):
            si, ii, mi = map(int, m.groups())
            slot = meta.setdefault((si, ii), {}).setdefault(mi, ["", ""])
            slot[0] = value
            continue

        if m := BULLET_TEXT.match(key):
            si, ii, bi = map(int, m.groups())
            bullets.setdefault((si, ii), {})[bi] = value
            continue

        if m := BULLET_META.match(key):
            si, ii, bi = map(int, m.groups())
            try:
                bullet_meta[(si, ii, bi)] = json.loads(value) if value else {}
            except json.JSONDecodeError:
                bullet_meta[(si, ii, bi)] = {}

    sections: list[Section] = []
    for si in sorted(sec_type):
        section = Section(type=sec_type[si], order_index=len(sections))  # type: ignore[arg-type]
        for ii in sorted(i for (s, i) in items if s == si):
            data = items[(si, ii)]
            item = Item(
                org=data.get("org"),
                role=data.get("role"),
                start=data.get("start"),
                end=data.get("end"),
                location=data.get("location"),
                order_index=len(section.items),
            )
            for mi in sorted(meta.get((si, ii), {})):
                label, value = meta[(si, ii)][mi]
                label, value = label.strip(), value.strip()
                if label and value:
                    item.meta[label] = value

            for bi in sorted(bullets.get((si, ii), {})):
                text = bullets[(si, ii)][bi]
                if not text:
                    continue
                info = bullet_meta.get((si, ii, bi), {}) or _default_bullet_meta()
                item.bullets.append(
                    Bullet(
                        text=text,
                        order_index=len(item.bullets),
                        tags=info.get("tags") or [],
                        strength=int(info.get("strength") or 3),
                        metrics=info.get("metrics") or [],
                        source=info.get("source") or "manual",
                        user_utterance=info.get("utterance"),
                        source_bullet_id=info.get("src_bullet"),
                    )
                )
            # 完全空白的行不保留
            if item.org or item.role or item.bullets or item.meta:
                section.items.append(item)
        if section.items:
            sections.append(section)

    doc.name = profile["name"] or doc.name
    doc.phone = profile["phone"]
    doc.email = profile["email"]
    doc.location = profile["location"]
    doc.sections = sections
    return doc
