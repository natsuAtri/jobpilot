"""改写策略层：先决定「改哪些、为什么」，再动笔。

这是双 agent 之前的一步：没有策略，起草只能逐条盲改，最后大部分是
无意义的措辞调整。策略层用「JD 要求 + 简历要点 + 匹配差距 + 语义证据」
挑出真正值得改的少数条目。
"""
from __future__ import annotations

from ..llm import client
from .facts import Fact


def _resume_brief(facts: list[Fact]) -> str:
    return "\n".join(f"{f.source_id} || {f.text}" for f in facts)


def _evidence_text(evidence: list[dict]) -> str:
    if not evidence:
        return "（无语义证据）"
    return "\n".join(
        f"JD「{e.get('jd', '')[:60]}」 → 简历「{e.get('resume', '')[:60]}」（{e.get('label', '')}）"
        for e in evidence[:10]
    )


async def plan(
    requirement_text: str,
    facts: list[Fact],
    *,
    gaps: list[dict] | None = None,
    evidence: list[dict] | None = None,
) -> dict:
    """产出改写策略。失败时返回空策略（调用方回退到全量改写）。"""
    system = """你是简历改写策略师。根据 JD 要求和候选人简历，决定「哪些经历值得改写、为什么」。

硬约束：
1. 只依据给出的信息，不得编造简历里没有的经历或 JD 里没有的要求。
2. 只挑真正能提升匹配度的条目，最多 5 条；无关或已经够贴的放进 skip。
3. source_id 必须来自给出的简历要点。

只输出 JSON：
{
  "key_requirements": ["JD 最看重的 3-5 条要求"],
  "strengths": ["候选人对应的强项"],
  "missing": ["JD 要求但简历没有的（警告：不要编造）"],
  "focus": [{"source_id": "项目:0:1", "why": "这条能体现 JD 的 X 要求"}],
  "skip": [{"source_id": "项目:0:2", "why": "与 JD 无关或已经够贴"}]
}"""

    gap_text = "；".join(g.get("what", "") for g in (gaps or [])) or "（无）"
    user = (
        f"# JD 要求\n{requirement_text[:1500] or '（JD 正文缺失）'}\n\n"
        f"# 简历要点\n{_resume_brief(facts)}\n\n"
        f"# 匹配差距\n{gap_text}\n\n"
        f"# 语义证据\n{_evidence_text(evidence or [])}\n\n"
        "请给出改写策略。"
    )

    try:
        data = await client.complete_json(
            "resume_strategy",
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
    except Exception:  # noqa: BLE001 - 策略失败不该拖垮整条生成
        return {"focus": [], "skip": [], "key_requirements": [], "strengths": [],
                "missing": [], "failed": True}

    valid = {f.source_id for f in facts}
    focus = [
        {"source_id": item.get("source_id"), "why": (item.get("why") or "").strip()}
        for item in (data.get("focus") or [])
        if item.get("source_id") in valid
    ][:5]
    skip = [
        {"source_id": item.get("source_id"), "why": (item.get("why") or "").strip()}
        for item in (data.get("skip") or [])
        if item.get("source_id") in valid
    ][:20]

    return {
        "focus": focus,
        "skip": skip,
        "key_requirements": [str(x) for x in (data.get("key_requirements") or [])][:6],
        "strengths": [str(x) for x in (data.get("strengths") or [])][:6],
        "missing": [str(x) for x in (data.get("missing") or [])][:6],
        "failed": False,
    }
