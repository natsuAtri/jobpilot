"""对话产出的结构化增量。

关键约束：模型只能产出 patch，不能直接改文档。
patch 先落库等用户确认，确认后才写入 ResumeDoc——
这样「模型理解错了」的代价只是多看一眼，而不是简历被悄悄改坏。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..models import Bullet, Item, Section, ResumeDoc

PROFILE_FIELDS = {"name", "phone", "email", "location", "intents"}

Op = Literal["set_profile", "add_item", "add_bullet", "set_meta"]


class Patch(BaseModel):
    op: Op
    # set_profile
    field: str | None = None
    value: str | None = None
    # 定位
    section: str | None = None
    item: int | None = None
    # add_item
    org: str | None = None
    role: str | None = None
    start: str | None = None
    end: str | None = None
    # add_bullet / set_meta
    text: str | None = None
    key: str | None = None

    def describe(self) -> str:
        if self.op == "set_profile":
            names = {"name": "姓名", "phone": "电话", "email": "邮箱",
                     "location": "城市", "intents": "求职意向"}
            return f"基本信息 · {names.get(self.field or '', self.field)} → {self.value}"
        if self.op == "add_item":
            bits = [x for x in (self.org, self.role, self.start, self.end) if x]
            return f"新增{_section_cn(self.section)}条目：{' / '.join(bits)}"
        if self.op == "add_bullet":
            return f"新增要点：{self.text}"
        if self.op == "set_meta":
            return f"补充 {self.key}：{self.value or self.text}"
        return self.op


def _section_cn(name: str | None) -> str:
    return {
        "education": "教育", "experience": "工作", "project": "项目",
        "skill": "技能", "award": "荣誉", "cert": "证书", "other": "其他",
    }.get(name or "", "")


class PatchError(ValueError):
    pass


def parse_patches(raw: list | None) -> list[Patch]:
    """把模型返回的 JSON 数组转成 Patch；不合法的逐条丢弃而不是整体失败。"""
    patches: list[Patch] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        try:
            patches.append(Patch(**entry))
        except ValidationError:
            continue
    return patches


def _find_section(doc: ResumeDoc, name: str | None) -> Section | None:
    return next((s for s in doc.sections if s.type == name), None)


def _ensure_item(doc: ResumeDoc, name: str | None) -> Item:
    """拿到（必要时创建）目标条目。

    从零创建时简历是空的，用户说第一句话时既没有章节也没有条目——
    这时候要就地建出来，而不是报「找不到对应条目」把内容丢掉。
    真实使用里第一条信息往往就是这么来的。
    """
    section = _find_section(doc, name)
    if section is None:
        if not name:
            raise PatchError("没有指定章节")
        section = Section(type=name, order_index=len(doc.sections))
        doc.sections.append(section)
    if not section.items:
        section.items.append(Item(order_index=0))
    return section.items[-1]


def apply_patches(doc: ResumeDoc, patches: list[Patch]) -> tuple[list[str], list[str]]:
    """把 patch 写进文档，返回 (已应用说明, 被拒绝原因)。

    每一项都单独校验：一条不合法不影响其他条。
    """
    applied: list[str] = []
    rejected: list[str] = []

    for patch in patches:
        try:
            if patch.op == "set_profile":
                if patch.field not in PROFILE_FIELDS:
                    raise PatchError(f"未知字段 {patch.field}")
                value = (patch.value or "").strip()
                if not value:
                    raise PatchError("值为空")
                setattr(doc, patch.field, value)

            elif patch.op == "add_item":
                section = _find_section(doc, patch.section)
                if section is None:
                    section = Section(type=patch.section, order_index=len(doc.sections))
                    doc.sections.append(section)
                section.items.append(
                    Item(
                        org=(patch.org or "").strip() or None,
                        role=(patch.role or "").strip() or None,
                        start=(patch.start or "").strip() or None,
                        end=(patch.end or "").strip() or None,
                        order_index=len(section.items),
                    )
                )

            elif patch.op == "add_bullet":
                section = _find_section(doc, patch.section)
                text = (patch.text or "").strip()
                if not text:
                    raise PatchError("内容为空")
                if section is None or not section.items:
                    item = _ensure_item(doc, patch.section)
                else:
                    index = patch.item if patch.item is not None else len(section.items) - 1
                    if not 0 <= index < len(section.items):
                        raise PatchError("条目序号越界")
                    item = section.items[index]
                item.bullets.append(
                    Bullet(text=text, order_index=len(item.bullets), source="chat")
                )

            elif patch.op == "set_meta":
                key = (patch.key or "").strip()
                value = (patch.value or patch.text or "").strip()
                if not key or not value:
                    raise PatchError("键或值为空")
                section = _find_section(doc, patch.section)
                if section is not None and section.items:
                    index = patch.item if patch.item is not None else len(section.items) - 1
                    if not 0 <= index < len(section.items):
                        raise PatchError("条目序号越界")
                    item = section.items[index]
                else:
                    item = _ensure_item(doc, patch.section)
                item.meta[key] = value

            else:
                raise PatchError(f"未知操作 {patch.op}")

            applied.append(patch.describe())
        except PatchError as exc:
            rejected.append(f"{patch.describe()} —— {exc}")

    return applied, rejected
