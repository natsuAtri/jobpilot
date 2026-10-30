"""补写建议：针对目标岗位，简历该补什么。

这是双 agent 真正的价值所在——改写只能换说法，补写才能提升竞争力。
建议必须具体到「补什么、为什么、怎么写」，且不能建议编造没有的经历。
"""
from __future__ import annotations

from ..llm import client
from ..models import ResumeDoc


def _resume_brief(doc: ResumeDoc) -> str:
    lines = []
    education = doc.section("education")
    if education and education.items:
        item = education.items[0]
        lines.append(f"教育：{item.org or ''} {item.meta.get('专业', '')} {item.role or ''}".strip())
    skills = [it.org for section in doc.sections if section.type == "skill"
              for it in section.items if it.org]
    if skills:
        lines.append("技能：" + "、".join(skills))
    bullets = [b.text for section in doc.sections for item in section.items
               for b in item.bullets if b.text.strip()]
    if bullets:
        lines.append("经历：" + "；".join(bullets[:12]))
    return "\n".join(lines)


async def general_review(doc: ResumeDoc) -> list[dict]:
    """不针对具体岗位，只看简历本身还能补什么。"""
    system = """你是简历顾问。看这份简历，指出「本身可以补什么」。
不要聊具体岗位，只谈简历质量。

硬约束：
1. 不得建议编造经历；建议是「如果你有，就补上」或「去补这个能力」。
2. 具体到「补什么、为什么、怎么写」。
3. 最多 4 条，按重要性排序。

只输出 JSON：
{"suggestions": [
  {"title": "给项目补充量化结果", "why": "现在只有做了什么，没有做到什么程度", "how": "补上准确率、耗时、数据规模等数字", "section": "project"}
]}
section 取值：project / experience / skill / award / other"""
    try:
        data = await client.complete_json(
            "resume_review",
            [
                {"role": "system", "content": system},
                {"role": "user", "content": f"# 简历\n{_resume_brief(doc)}\n\n请给出补充建议。"},
            ],
        )
    except Exception:  # noqa: BLE001 - 建议是增强项
        return []
    return _clean(data)


def _clean(data: dict) -> list[dict]:
    out = []
    valid_sections = {"project", "experience", "skill", "award", "other"}
    for item in (data.get("suggestions") or [])[:4]:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "why": (item.get("why") or "").strip(),
            "how": (item.get("how") or "").strip(),
            "section": item.get("section") if item.get("section") in valid_sections else "project",
        })
    return out


async def suggest(
    doc: ResumeDoc,
    requirement_text: str,
    *,
    missing: list[str] | None = None,
    missed: list[dict] | None = None,
) -> list[dict]:
    """返回 [{title, why, how, section}]，最多 4 条。失败返回空列表。"""
    system = """你是求职顾问。根据 JD 要求和候选人现有简历，给出「该补什么」的可执行建议。

硬约束：
1. 只针对 JD 明确要求、而简历没有体现的内容提建议。
2. 不得建议编造经历；建议应该是「如果你做过，就补上；没做过，去补这个能力」。
3. 建议要具体到「补什么、为什么、怎么写」，不要空话。
4. 最多 4 条，按重要性排序。

只输出 JSON：
{"suggestions": [
  {"title": "补一段 RAG 应用项目经历",
   "why": "JD 明确要求 RAG 与向量数据库，简历完全没有体现",
   "how": "写清楚：用了什么向量库（如 FAISS/Chroma）、检索效果、如何评估",
   "section": "project"}
]}
section 取值：project / experience / skill / award / other"""

    context = ""
    if missing:
        context += "# 匹配分析指出的缺口\n" + "、".join(missing[:6]) + "\n\n"
    if missed:
        context += "# 评审指出的漏改\n" + "\n".join(
            f"- {m.get('requirement', '')}：{m.get('suggestion', '')}" for m in missed[:6]
        ) + "\n\n"

    user = (
        f"# JD 要求\n{requirement_text[:1500] or '（JD 正文缺失）'}\n\n"
        f"# 候选人简历\n{_resume_brief(doc)}\n\n"
        f"{context}"
        "请给出该补什么的建议。"
    )
    try:
        data = await client.complete_json(
            "resume_gaps_suggest",
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
    except Exception:  # noqa: BLE001 - 建议是增强项，失败不影响生成
        return []

    return _clean(data)
