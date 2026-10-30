"""双 agent 生成引擎：策略 → 起草 → 评审 → 修订 → 三守恒校验。

不是「全量改写 + 事后审错」，而是：
1. 策略层先挑出「值得改的少数条目」（focus），其余不动；
2. 起草只改 focus 里的条目；
3. 评审判断每条改动的「收益」（ok/edit/noop），noop 直接还原；
4. 三守恒兜底，违规回退；
5. 终审按优先级排序。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..llm import client
from ..models import ResumeDoc
from . import prompt
from . import strategy as strategy_mod
from .facts import Fact, extract_facts, find_fact
from .guard import CheckResult, check

# 去掉标点、空白、大小写后的「实质内容」，用于判断改写是否真有变化。
_SUBSTANCE_STRIP = re.compile(r"[\s。；;，,、！!？?：:·•\-—…\.\"'\"'（）()【】\[\]「」『』<>《》/\\|]+")


def _substance(text: str) -> str:
    return _SUBSTANCE_STRIP.sub("", text or "").lower()


@dataclass
class Proposal:
    source_id: str
    label: str
    original: str
    rewritten: str
    checks: list[CheckResult] = field(default_factory=list)
    critique: str = ""
    blocked: bool = False
    priority: str = "normal"     # high | normal | low
    value: str = ""              # 评审给的收益判断 high | low

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def changed(self) -> bool:
        return _substance(self.rewritten) != _substance(self.original)

    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "label": self.label,
            "original": self.original,
            "rewritten": self.rewritten,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
            "critique": self.critique,
            "blocked": self.blocked,
            "priority": self.priority,
        }


@dataclass
class GenerationResult:
    proposals: list[Proposal] = field(default_factory=list)
    rounds: int = 0
    strategy: dict = field(default_factory=dict)
    missed: list[dict] = field(default_factory=list)
    suggestions: list[dict] = field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return sum(1 for p in self.proposals if p.ok and p.changed)


def _facts_to_proposals(facts: list[Fact]) -> list[Proposal]:
    return [
        Proposal(source_id=f.source_id, label=f.label, original=f.text, rewritten=f.text)
        for f in facts
    ]


def _apply_rewrites(proposals: list[Proposal], rewrites: list[dict],
                    *, only_if_valid: bool = False) -> None:
    by_id = {p.source_id: p for p in proposals}
    for r in rewrites:
        source_id = r.get("source_id") or ""
        text = (r.get("text") or "").strip()
        proposal = by_id.get(source_id)
        if proposal is None or not text:
            continue
        # 只改标点/空白/大小写的「假改写」直接回退原文
        if _substance(text) == _substance(proposal.original):
            proposal.rewritten = proposal.original
            proposal.checks = []
            proposal.blocked = False
            continue
        new_checks = check(proposal.original, text)
        new_blocked = not all(c.ok for c in new_checks)
        # 修订轮：不能把已经合格的改写覆盖成违规的
        if only_if_valid and new_blocked and proposal.changed and not proposal.blocked:
            continue
        proposal.rewritten = text
        proposal.checks = new_checks
        proposal.blocked = new_blocked


def _apply_critiques(proposals: list[Proposal], reviews: list[dict]) -> None:
    by_id = {p.source_id: p for p in proposals}
    for r in reviews:
        proposal = by_id.get(r.get("source_id") or "")
        if proposal is None:
            continue
        verdict = (r.get("verdict") or "").strip()
        proposal.value = (r.get("value") or "").strip()
        reason = (r.get("reason") or "").strip()
        suggestion = (r.get("suggestion") or "").strip()
        if verdict == "noop":
            # 不强制还原：评审觉得收益有限只做提醒，最终由用户在终审页决定
            proposal.priority = "low"
            proposal.critique = reason or "评审认为收益有限，可自行决定是否采用"
            continue
        if verdict == "edit" or reason or suggestion:
            proposal.critique = "；".join(x for x in (reason, suggestion) if x)
        if proposal.value == "high":
            proposal.priority = "high"


async def generate(
    doc: ResumeDoc,
    jd_text: str,
    *,
    max_rounds: int = 1,
    task_draft: str = "resume_draft",
    task_critique: str = "resume_critique",
    opinion: str = "",
    trace=None,
    evidence: list[dict] | None = None,
    gaps: list[dict] | None = None,
) -> GenerationResult:
    def t(stage: str, message: str, detail: list | None = None) -> None:
        if trace is not None:
            trace(stage, message, detail)

    facts = extract_facts(doc)
    t("facts", f"抽取事实白名单 {len(facts)} 条", [f.text for f in facts[:8]])
    if not facts:
        t("finish", "简历里没有可改写的事实，跳过")
        return GenerationResult(proposals=[], rounds=0)

    proposals = _facts_to_proposals(facts)

    # ---- 策略层：先决定改哪些
    from ..rag.chunk import requirement_text

    req_text = requirement_text(jd_text)
    t("strategy", "策略层正在分析 JD 与简历，决定改哪些…")
    plan = await strategy_mod.plan(req_text, facts, gaps=gaps, evidence=evidence)
    focus_ids = [item["source_id"] for item in plan.get("focus") or []]
    if focus_ids:
        t(
            "strategy",
            f"策略：聚焦 {len(focus_ids)} 条"
            + (f"（JD 最看重：{'、'.join(plan.get('key_requirements') or [])[:40]}）"
               if plan.get("key_requirements") else ""),
            [{"title": item["source_id"], "company": item.get("why", "")}
             for item in plan["focus"]],
        )
        draft_facts = [f for f in facts if f.source_id in focus_ids]
        for p in proposals:
            if p.source_id in focus_ids:
                p.priority = "high"
    else:
        t("strategy", "策略没有给出聚焦条目，回退到逐条判断")
        draft_facts = facts

    # ---- 起草
    t("draft", f"起草 agent 正在改写 {len(draft_facts)} 条…（调用强模型）")
    draft_data = await client.complete_json(
        task_draft,
        prompt.draft_messages(draft_facts, jd_text, opinion=opinion,
                              strategy=plan, evidence=evidence),
    )
    _apply_rewrites(proposals, draft_data.get("rewrites") or [])
    t("draft", f"起草完成：{sum(1 for p in proposals if p.changed)} 条有实质改动",
      [{"original": p.original, "rewritten": p.rewritten, "blocked": p.blocked}
       for p in proposals if p.changed][:10])

    # ---- 评审（总是执行）
    missed: list[dict] = []
    changed = [p for p in proposals if p.changed]
    if changed:
        t("critique", "评审 agent 正在判断每条改动的收益…")
        review_data = await client.complete_json(
            task_critique,
            prompt.critique_messages(facts, jd_text, _rewrite_payload(changed), strategy=plan),
        )
        reviews = review_data.get("reviews") or []
        missed = review_data.get("missed") or []
        _apply_critiques(proposals, reviews)
        t("critique", f"评审完成：{len(reviews)} 条意见" + (f"、{len(missed)} 条漏改" if missed else ""),
          [{"source_id": r.get("source_id"), "verdict": r.get("verdict"),
            "reason": r.get("reason", "")} for r in reviews if r.get("verdict") in ("edit", "noop")][:10])

        # ---- 自动修订（默认关闭：实测容易把合格改写改坏，交给人工「按意见改一轮」）
        need_revise = [p for p in proposals if p.critique and p.priority != "low" and p.changed]
        if max_rounds > 1 and need_revise:
            t("revise", f"起草 agent 根据 {len(need_revise)} 条评审批次修订…")
            revise_data = await client.complete_json(
                task_draft,
                prompt.draft_messages(
                    [f for f in facts if f.source_id in {p.source_id for p in need_revise}],
                    jd_text, strategy=plan, evidence=evidence,
                ) + [{"role": "user", "content": (
                    "# 评审意见\n"
                    + "\n".join(f"{p.source_id}: {p.critique}" for p in need_revise)
                    + "\n\n请据此修订，仍然只输出 JSON。"
                )}],
            )
            _apply_rewrites(proposals, revise_data.get("rewrites") or [], only_if_valid=True)
    else:
        t("critique", "没有改动，跳过评审")

    # ---- 补写建议：说明「该补什么」，这是改写之外更有价值的产出
    suggestions: list[dict] = []
    try:
        from . import suggest as suggest_mod

        t("suggest", "正在生成「该补什么」的建议…")
        suggestions = await suggest_mod.suggest(
            doc, req_text, missing=plan.get("missing") or [], missed=missed
        )
        if suggestions:
            t("suggest", f"补写建议 {len(suggestions)} 条",
              [c["title"] for c in suggestions])
    except Exception:  # noqa: BLE001 - 建议是增强项
        suggestions = []

    blocked = [p for p in proposals if p.blocked]
    passed = [p for p in proposals if p.ok and p.changed]
    t("guard", f"三守恒校验：{len(passed)} 条通过，{len(blocked)} 条被拦截",
      [{"text": p.original, "reason": "、".join(c.name for c in p.checks if not c.ok)}
       for p in blocked][:10])
    t("finish", f"生成完成：{len(passed)} 条有实质改动，可在终审页逐条处理")
    return GenerationResult(proposals=proposals, rounds=max_rounds,
                            strategy=plan, missed=missed, suggestions=suggestions)


def _rewrite_payload(proposals: list[Proposal]) -> list[dict]:
    return [{"source_id": p.source_id, "text": p.rewritten} for p in proposals]


def locate_bullet(doc: ResumeDoc, source_id: str):
    """按 source_id（section_type:item:bullet）定位一条 bullet。"""
    parts = source_id.split(":")
    if len(parts) != 3:
        return None
    section_type, item_index, bullet_index = parts
    try:
        item_index, bullet_index = int(item_index), int(bullet_index)
    except ValueError:
        return None
    section = doc.section(section_type)  # type: ignore[arg-type]
    if section is None or item_index >= len(section.items):
        return None
    item = section.items[item_index]
    if bullet_index >= len(item.bullets):
        return None
    return item.bullets[bullet_index]


def apply_to_doc(doc: ResumeDoc, proposals: list[Proposal]) -> int:
    """把被接受的改写写回文档（按 source_id 定位）。"""
    changed = 0
    for proposal in proposals:
        if not proposal.ok or not proposal.changed:
            continue
        bullet = locate_bullet(doc, proposal.source_id)
        if bullet is not None:
            bullet.text = proposal.rewritten
            changed += 1
    return changed


def apply_items_to_doc(doc: ResumeDoc, items: list[dict]) -> int:
    """终审落地：manual 优先，其次接受的改写，否则原文。"""
    changed = 0
    for item in items:
        if item.get("manual"):
            final = item["manual"]
        elif item.get("accepted") == 1 and not item.get("blocked"):
            final = item["rewritten"]
        else:
            final = item["original"]
        bullet = locate_bullet(doc, item.get("source_id") or "")
        if bullet is not None and bullet.text.strip() != final.strip():
            bullet.text = final
            changed += 1
    return changed
