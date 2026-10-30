"""LLM 精修：规则抽骨架，模型补结构。

规则层对版式很敏感（缩进、侧栏、装饰符号），一旦版式不常见就会把
章节/条目切歪。这里把「带坐标的视觉行」交给模型，让它输出结构化结果，
再用模型结果覆盖规则结果——失败则保留规则结果，不阻塞导入。
"""
from __future__ import annotations

from ..llm import client
from ..models import Bullet, Item, ResumeDoc, Section
from .pdf import PageText, to_prompt_text

VALID_TYPES = {"education", "experience", "project", "skill", "award", "cert", "other"}


def needs_refine(doc: ResumeDoc) -> bool:
    """规则结果明显不对时才花 token 精修。"""
    if not doc.name:
        return True
    if len(doc.sections) < 3:
        return True
    if not any(s.type == "education" for s in doc.sections):
        return True
    total = sum(len(s.items) for s in doc.sections)
    empty = sum(1 for s in doc.sections for it in s.items if not it.org and not it.bullets)
    return bool(total) and empty / total > 0.4


def _skeleton(doc: ResumeDoc) -> str:
    lines = [f"姓名：{doc.name or '?'}　电话：{doc.phone or '?'}　邮箱：{doc.email or '?'}"]
    for sec in doc.sections:
        lines.append(f"[{sec.type}]")
        for it in sec.items:
            head = " ".join(x for x in (it.org, it.role, it.start, it.end) if x)
            lines.append(f"  - {head or '(空)'}")
            for b in it.bullets:
                lines.append(f"      · {b.text[:60]}")
    return "\n".join(lines)


SYSTEM = """你在做简历结构化。给你的是 PDF 的视觉行（格式 `y|字号| x:文本 | x:文本`，
同一行多个 x 代表不同列），以及规则解析的粗略结果。

把它整理成干净的 JSON：
{
  "name": "姓名",
  "phone": "电话",
  "email": "邮箱",
  "location": "城市",
  "sections": [
    {"type": "education|experience|project|skill|award|cert|other",
     "items": [
       {"org": "公司/学校/项目名", "role": "职位/学位/角色",
        "start": "2023.09", "end": "2024.01",
        "meta": {"专业": "机械工程"},
        "bullets": ["一条要点", "另一条要点"]}
     ]}
  ]
}

规则：
1. 姓名取顶部字号最大的中文姓名，不要取「个人简历」这类标题。
2. 章节按原顺序，type 只能从给定枚举里选；识别不了的归 other。
3. 教育章节：org=学校，role=学位（硕士/学士/本科…），meta["专业"]=专业。
4. 去掉装饰符号（. ⚫ ➢ 等）、去掉重复的标题文字。
5. 每条要点独立成一条，不要合并；不要把一条要点拆成两条。
6. 原文没有的信息不要编造，留空字符串。
只输出 JSON。"""


def _to_doc(data: dict, fallback: ResumeDoc) -> ResumeDoc:
    doc = ResumeDoc()
    doc.name = (data.get("name") or fallback.name or "").strip()
    doc.phone = (data.get("phone") or fallback.phone or "").strip()
    doc.email = (data.get("email") or fallback.email or "").strip()
    doc.location = (data.get("location") or fallback.location or "").strip()
    doc.links = dict(fallback.links)

    for raw in (data.get("sections") or []):
        if not isinstance(raw, dict):
            continue
        type_ = raw.get("type")
        if type_ not in VALID_TYPES:
            type_ = "other"
        section = Section(type=type_, order_index=len(doc.sections))
        for ri, raw_item in enumerate(raw.get("items") or []):
            if not isinstance(raw_item, dict):
                continue
            meta = raw_item.get("meta") if isinstance(raw_item.get("meta"), dict) else {}
            item = Item(
                org=(raw_item.get("org") or "").strip() or None,
                role=(raw_item.get("role") or "").strip() or None,
                start=(raw_item.get("start") or "").strip() or None,
                end=(raw_item.get("end") or "").strip() or None,
                meta={str(k): str(v) for k, v in meta.items() if v},
                order_index=ri,
            )
            for bi, text in enumerate(raw_item.get("bullets") or []):
                text = (text or "").strip()
                if text:
                    item.bullets.append(Bullet(text=text, order_index=bi))
            if item.org or item.role or item.meta or item.bullets:
                section.items.append(item)
        if section.items:
            doc.sections.append(section)
    return doc


async def refine(pages: list[PageText], rule_doc: ResumeDoc) -> ResumeDoc:
    """让模型重排结构。失败时返回原规则结果。"""
    user = (
        f"# 视觉行\n{to_prompt_text(pages)[:12000]}\n\n"
        f"# 规则解析的粗略结果（供参考，可能切错）\n{_skeleton(rule_doc)}\n\n"
        "请输出整理后的 JSON。"
    )
    try:
        data = await client.complete_json(
            "resume_parse",
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        )
    except Exception:  # noqa: BLE001 - 精修失败就用规则结果
        return rule_doc

    refined = _to_doc(data, rule_doc)
    if not refined.sections:
        return rule_doc
    # 模型漏掉的姓名/电话，用规则结果补回
    refined.name = refined.name or rule_doc.name
    refined.phone = refined.phone or rule_doc.phone
    refined.email = refined.email or rule_doc.email
    refined.warnings = list(rule_doc.warnings)
    return refined
