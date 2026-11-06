"""ResumeDoc → 渲染上下文。

把「模型长什么样」和「版面怎么排」隔开：模板只认 label / period / kind，
不关心数据库字段。
"""
from __future__ import annotations

import re

from ..models import SECTION_LABELS, ResumeDoc, Section

DATE_RE = re.compile(r"(\d{4})\s*[.\-/年]\s*(\d{1,2})")
PRESENT = ("至今", "今", "现在", "present", "Present")


def _fmt_date(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    if value in PRESENT:
        return "至今"
    m = DATE_RE.search(value)
    if m:
        return f"{m.group(1)}.{int(m.group(2)):02d}"
    return value


def format_period(start: str | None, end: str | None) -> str:
    s, e = _fmt_date(start), _fmt_date(end)
    if s and e:
        return f"{s} — {e}"
    return s or e


def _section_kind(section: Section) -> str:
    if section.type == "skill":
        return "kv"
    if section.type in ("award", "cert", "other"):
        return "bullets"
    return "items"


def _section_label(section: Section) -> str:
    if section.type == "other" and section.title:
        return section.title
    return SECTION_LABELS.get(section.type, "其他")


def build_context(doc: ResumeDoc, *, theme: str = "ink",
                  photo: str | None = None) -> dict:
    sections = []
    for section in sorted(doc.sections, key=lambda s: s.order_index):
        kind = _section_kind(section)
        items = []
        for item in sorted(section.items, key=lambda i: i.order_index):
            bullets = [b for b in item.bullets if b.text.strip()]
            bullet_texts = [{"text": b.text} for b in bullets]
            # 奖项/证书这类章节模板只渲染 bullets；如果解析结果把内容放在
            # org/role 里（「竞赛名 + 一等奖」），拼成一行兜底，别整节省略掉
            if not bullet_texts and kind == "bullets":
                fallback = " ".join(x for x in (item.org, item.role) if x)
                if fallback:
                    bullet_texts = [{"text": fallback}]
            items.append(
                {
                    "org": item.org,
                    "role": item.role,
                    "period": format_period(item.start, item.end),
                    "meta": {k: v for k, v in item.meta.items() if v},
                    "bullets": bullet_texts,
                }
            )
        if not items:
            continue
        sections.append(
            {
                "type": section.type,
                "label": _section_label(section),
                "kind": kind,
                "items": items,
            }
        )

    return {
        "doc": doc,
        "sections": sections,
        "theme": theme,
        "photo": photo,
    }


def expected_fragments(doc: ResumeDoc) -> list[str]:
    """简历上每一段应当可见的文字，逐条返回。

    刻意复用 build_context：日期格式化、空条目过滤、章节名映射都走同一条路径，
    否则校验会拿「2021.9」去比「2021.09」，永远报错。

    为什么是「逐条」而不是一整段：PDF 的文本抽取顺序由渲染器决定，
    整篇序列比对会因为顺序差异产生大量假阳性；逐条子串匹配只关心
    「这句话在不在」，与顺序无关，且同样能抓出丢字与错字。
    """
    ctx = build_context(doc)
    fragments: list[str] = []

    if ctx["doc"].name:
        fragments.append(ctx["doc"].name)
    if ctx["doc"].intents:
        fragments.append(ctx["doc"].intents)
    for value in (ctx["doc"].phone, ctx["doc"].email, ctx["doc"].location):
        if value:
            fragments.append(value)

    for section in ctx["sections"]:
        fragments.append(section["label"])
        for item in section["items"]:
            if section["kind"] == "kv":
                if item["org"]:
                    fragments.append(item["org"])
                fragments.extend(b["text"] for b in item["bullets"])
            elif section["kind"] == "bullets":
                fragments.extend(b["text"] for b in item["bullets"])
            else:
                if item["org"]:
                    fragments.append(item["org"])
                if item["role"]:
                    fragments.append(item["role"])
                if item["period"]:
                    fragments.append(item["period"])
                fragments.extend(item["meta"].values())
                fragments.extend(b["text"] for b in item["bullets"])
    return [f.strip() for f in fragments if f and f.strip()]


def plain_text(doc: ResumeDoc) -> str:
    return "\n".join(expected_fragments(doc))
