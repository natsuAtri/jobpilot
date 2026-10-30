"""三守恒校验器（从已删除的定制改写模块恢复，服务双 agent 生成）。

双 agent 里，起草 agent 可能加戏、评审 agent 可能提不实建议——
所以每一轮修订都必须过这四道确定性闸门。模型负责「换更贴 JD 的说法」，
闸门负责「不许乱说」。这四道都是正则 + 词表，不需要模型参与。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_LENGTH_RATIO = 1.25

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#._/-]{1,}")
CHINESE_ORG_RE = re.compile(r"[\u4e00-\u9fa5]{2,10}(?:公司|大学|学院|研究院|实验室|研究所|集团)")

STRENGTH_TERMS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (5, ("从零搭建", "从0到1", "从零到一", "独立设计", "开创", "从无到有")),
    (4, ("主导", "牵头", "带领", "主持", "负责人", "负责整个")),
    (3, ("负责", "独立完成", "主要承担", "承担")),
    (2, ("配合", "协助", "参与部分", "支持")),
    (1, ("参与", "跟随", "学习")),
)

ENTITY_IGNORE = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
    "api", "ui", "app", "web", "os", "ai", "ml", "it", "pm",
}


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def extract_numbers(text: str) -> list[str]:
    found: list[str] = []
    for raw in NUMBER_RE.findall(text):
        token = raw.replace(",", "")
        if "." in token:
            token = token.rstrip("0").rstrip(".")
        else:
            token = token.lstrip("0") or "0"
        found.append(token)
    return sorted(found)


def extract_entities(text: str) -> set[str]:
    entities = {m.lower() for m in LATIN_TOKEN_RE.findall(text)}
    entities |= {m for m in CHINESE_ORG_RE.findall(text)}
    return {e for e in entities if e not in ENTITY_IGNORE and len(e) >= 2}


def strength_level(text: str) -> int:
    for level, terms in STRENGTH_TERMS:
        if any(term in text for term in terms):
            return level
    return 0


def _level_name(level: int) -> str:
    return {0: "无强度词", 1: "参与", 2: "配合", 3: "负责", 4: "主导", 5: "从零搭建"}.get(level, f"等级{level}")


def check_numbers(source: str, rewritten: str) -> CheckResult:
    src, dst = extract_numbers(source), extract_numbers(rewritten)
    added = [n for n in dst if n not in src]
    dropped = [n for n in src if n not in dst]
    if added:
        return CheckResult("数字守恒", False, f"新增了原文没有的数字 {added}")
    if dropped:
        return CheckResult("数字守恒", False, f"丢了原文的数字 {dropped}")
    return CheckResult("数字守恒", True, "")


def check_entities(source: str, rewritten: str) -> CheckResult:
    added = extract_entities(rewritten) - extract_entities(source)
    if added:
        return CheckResult("实体守恒", False, f"新增了原文没有的名词 {sorted(added)[:5]}")
    return CheckResult("实体守恒", True, "")


def check_strength(source: str, rewritten: str) -> CheckResult:
    src_level, dst_level = strength_level(source), strength_level(rewritten)
    if dst_level > src_level:
        return CheckResult(
            "强度守恒", False,
            f"责任强度被抬高：{_level_name(src_level)} → {_level_name(dst_level)}",
        )
    return CheckResult("强度守恒", True, "")


def check_length(source: str, rewritten: str) -> CheckResult:
    # 短条目按比例太苛刻（12 字只能多 3 字），留一个绝对余量
    limit = max(len(source) * MAX_LENGTH_RATIO, len(source) + 6)
    if len(rewritten) > limit:
        return CheckResult(
            "长度约束", False,
            f"改写后 {len(rewritten)} 字，超过原文 {len(source)} 字的允许上限 {limit:.0f} 字",
        )
    return CheckResult("长度约束", True, "")


def check(source: str, rewritten: str) -> list[CheckResult]:
    if not rewritten.strip():
        return [CheckResult("内容非空", False, "改写结果为空")]
    if not source.strip():
        return [CheckResult("原文非空", False, "源条目为空")]
    return [
        check_numbers(source, rewritten),
        check_entities(source, rewritten),
        check_strength(source, rewritten),
        check_length(source, rewritten),
    ]


def passed(results: list[CheckResult]) -> bool:
    return all(r.ok for r in results)


def summarize(results: list[CheckResult]) -> str:
    bad = [r for r in results if not r.ok]
    return "通过" if not bad else "；".join(f"{r.name}：{r.detail}" for r in bad)
