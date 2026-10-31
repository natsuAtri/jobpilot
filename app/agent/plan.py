"""规划：把「模糊的一句话」变成可执行的搜索条件。

用户说「找几个和我简历匹配的岗位」时，关键词不在这句话里，而在简历里。
让模型从简历推出 2-3 个招聘网站能用的关键词，比从原句里抠字靠谱得多。
"""
from __future__ import annotations

from ..llm import client
from ..models import ResumeDoc


def resume_brief(doc: ResumeDoc) -> str:
    parts: list[str] = []
    if doc.intents:
        parts.append(f"求职意向：{doc.intents}")
    edu = doc.section("education")
    if edu and edu.items:
        item = edu.items[0]
        parts.append(f"教育：{item.org or ''} {item.role or ''} {item.meta.get('专业', '')}".strip())
    skills = [it.org for section in doc.sections if section.type == "skill"
              for it in section.items if it.org]
    if skills:
        parts.append("技能：" + "、".join(skills[:10]))
    bullets = [b.text for section in doc.sections for item in section.items
               for b in item.bullets if b.text.strip()]
    if bullets:
        parts.append("经历：" + "；".join(bullets[:10]))
    return "\n".join(parts)


async def search_keywords(doc: ResumeDoc, limit: int = 3) -> list[str]:
    """根据简历给出适合在招聘网站搜索的岗位关键词。失败返回空列表。"""
    system = """你是求职顾问。根据候选人的简历，给出最适合在招聘网站（如前程无忧）
搜索的岗位名称关键词。

要求：
1. 输出 2-3 个，按匹配度从高到低排序
2. 用招聘网站上真实存在的岗位名，不要写技能名（写「算法工程师」而不是「Python」）
3. 关键词要具体到能筛出对口岗位，但别太窄导致没结果
只输出 JSON：{"keywords": ["…", "…"]}"""
    try:
        data = await client.complete_json(
            "search_plan",
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"# 简历\n{resume_brief(doc)}\n\n给出搜索关键词。"},
            ],
        )
    except Exception:  # noqa: BLE001 - 规划失败就退回规则
        return []

    out: list[str] = []
    for item in (data.get("keywords") or [])[:limit]:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def fallback_keywords(doc: ResumeDoc) -> list[str]:
    """模型不可用时的兜底：求职意向 > 技能 > 空。"""
    if doc.intents:
        return [doc.intents.strip()]
    skills = [it.org for section in doc.sections if section.type == "skill"
              for it in section.items if it.org]
    return skills[:1]
