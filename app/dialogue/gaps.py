"""简历缺口分析。

对话补录的核心不是「聊天」，而是「问对问题」。所以先算出简历缺什么，
再让模型在缺口里挑一个最有价值的问——而不是让模型自由发挥。

这套规则同时是降级路径：没配模型时，界面仍然能按缺口逐条提示。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import ResumeDoc

DIGIT = re.compile(r"\d")
# 语气词堆砌、无信息量的 bullet
EMPTY_WORDS = ("负责", "参与", "协助", "完成", "相关工作", "等")

# 数字越大越靠后；设计文档里的追问优先级
PRIORITY = {
    "basic": 0,
    "start": 1,      # 从零开始：先搭骨架
    "time": 2,       # 时间区间
    "role": 3,       # 角色与职责边界
    "metrics": 4,    # 量化结果
    "tech": 5,       # 技术栈
    "team": 6,       # 团队规模
}


@dataclass
class Gap:
    key: str
    question: str
    priority: int
    section: str | None = None
    item: int | None = None
    hint: str = ""

    @property
    def label(self) -> str:
        return self.question


@dataclass
class GapReport:
    gaps: list[Gap] = field(default_factory=list)

    @property
    def top(self) -> Gap | None:
        return self.gaps[0] if self.gaps else None

    def as_text(self) -> str:
        if not self.gaps:
            return "（简历看起来已经比较完整）"
        return "\n".join(f"{i + 1}. {g.question}" for i, g in enumerate(self.gaps[:6]))


def _item_label(item, index: int) -> str:
    name = (item.org or "").strip() or f"第 {index + 1} 条"
    return f"{name}（{item.role}）" if item.role else name


def analyze(doc: ResumeDoc) -> GapReport:
    report = GapReport()

    for field_name, question in (
        ("name", "怎么称呼你？简历上要写的姓名是？"),
        ("phone", "你的手机号是多少？"),
        ("email", "你的邮箱是多少？"),
    ):
        if not getattr(doc, field_name):
            report.gaps.append(Gap(key=f"profile:{field_name}", question=question,
                                   priority=PRIORITY["basic"]))

    if not doc.sections:
        report.gaps.append(
            Gap(key="scratch:start", question="先说说你的教育背景：学校、专业、学历、起止时间？",
                priority=PRIORITY["start"])
        )
        return report

    for section in doc.sections:
        # 技能章节没有内容
        if section.type == "skill" and not any(it.bullets for it in section.items):
            report.gaps.append(
                Gap(key="skill", question="专业技能这块怎么分？比如编程语言、框架、数据库分别会什么？",
                    priority=PRIORITY["tech"], section="skill")
            )
            continue

        for index, item in enumerate(section.items):
            label = _item_label(item, index)
            if section.type in ("education", "experience", "project"):
                if not item.start:
                    report.gaps.append(
                        Gap(key=f"{section.type}:{index}:start",
                            question=f"「{label}」是什么时候开始的？到什么时候结束？",
                            priority=PRIORITY["time"], section=section.type, item=index)
                    )
                elif not item.role and section.type in ("experience", "project"):
                    report.gaps.append(
                        Gap(key=f"{section.type}:{index}:role",
                            question=f"在「{label}」里你担任什么角色，主要负责哪部分？",
                            priority=PRIORITY["role"], section=section.type, item=index)
                    )

            if section.type in ("project", "experience") and not item.bullets:
                report.gaps.append(
                    Gap(key=f"{section.type}:{index}:detail",
                        question=f"「{label}」你具体做了什么？说两三件事就行。",
                        priority=PRIORITY["role"], section=section.type, item=index)
                )
                continue

            if section.type not in ("project", "experience"):
                continue

            for bullet in item.bullets:
                text = bullet.text.strip()
                if len(text) < 8:
                    report.gaps.append(
                        Gap(key=f"{section.type}:{index}:thin",
                            question=f"「{label}」里这条写得太简略，能多说一点具体做了什么吗？",
                            priority=PRIORITY["role"], section=section.type, item=index)
                    )
                    break
                if not DIGIT.search(text) and not any(w in text for w in ("设计", "搭建", "实现")):
                    report.gaps.append(
                        Gap(key=f"{section.type}:{index}:metrics",
                            question=f"「{label}」这段经历有数字吗？比如数据量、耗时、提升幅度。",
                            priority=PRIORITY["metrics"], section=section.type, item=index)
                    )
                    break

            if "技术栈" not in item.meta:
                report.gaps.append(
                    Gap(key=f"{section.type}:{index}:tech",
                        question=f"「{label}」用了哪些技术或工具？",
                        priority=PRIORITY["tech"], section=section.type, item=index)
                )

    report.gaps.sort(key=lambda g: g.priority)

    # 去重：同类问题最多留 2 条，避免「有数字吗」刷屏淹没真正重要的缺口
    kept: list[Gap] = []
    per_kind: dict[str, int] = {}
    for gap in report.gaps:
        kind = gap.key.split(":")[-1]
        if per_kind.get(kind, 0) >= 2:
            continue
        per_kind[kind] = per_kind.get(kind, 0) + 1
        kept.append(gap)
    report.gaps = kept
    return report
