"""事实白名单。

双 agent 的「grounding」：把简历里每条 bullet 连同它的数字、实体、责任强度
抽出来，作为起草和评审 agent 都必须遵守的事实基准。

评审 agent 的价值不在于「另一个 LLM 说好」，而在于「拿这份清单对账」：
只能指出偏离事实、表述弱、漏了 JD 关键词，不能提议清单里不存在的东西。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import ResumeDoc
from .guard import extract_entities, extract_numbers, strength_level


@dataclass
class Fact:
    source_id: str          # "section_type:item:bullet"
    label: str              # 上下文，如「项目 · 基于YOLOv8 的手语翻译系统」
    text: str
    numbers: list[str]
    entities: list[str]
    strength: int

    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "label": self.label,
            "text": self.text,
            "numbers": self.numbers,
            "entities": self.entities,
            "strength": self.strength,
        }


def extract_facts(doc: ResumeDoc) -> list[Fact]:
    facts: list[Fact] = []
    for section in doc.sections:
        if section.type not in ("project", "experience"):
            continue
        for item_index, item in enumerate(section.items):
            head = " / ".join(x for x in (item.org, item.role) if x) or "未命名"
            label = f"{section.type} · {head}"
            for bullet_index, bullet in enumerate(item.bullets):
                text = bullet.text.strip()
                if not text:
                    continue
                facts.append(
                    Fact(
                        source_id=f"{section.type}:{item_index}:{bullet_index}",
                        label=label,
                        text=text,
                        numbers=extract_numbers(text),
                        entities=sorted(extract_entities(text)),
                        strength=strength_level(text),
                    )
                )
    return facts


def whitelist_text(facts: list[Fact], with_length: bool = False) -> str:
    """给 agent 看的紧凑白名单，用 `||` 分隔便于解析。

    with_length=True 时附带每条的字数上限（起草用），让模型有明确的长度硬约束。
    """
    lines = []
    for f in facts:
        meta = "、".join(f.numbers) or "无"
        ents = "、".join(f.entities) or "无"
        line = (
            f"{f.source_id} || {f.label} || 强度{f.strength} || 数字:{meta} || 名词:{ents} || {f.text}"
        )
        if with_length:
            limit = max(int(len(f.text) * 1.2), len(f.text) + 6)
            line += f" || 原文字数:{len(f.text)} || 改写上限:{limit}"
        lines.append(line)
    return "\n".join(lines)


def find_fact(facts: list[Fact], source_id: str) -> Fact | None:
    return next((f for f in facts if f.source_id == source_id), None)
