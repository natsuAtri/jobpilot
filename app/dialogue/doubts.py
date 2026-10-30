"""解析后答疑：从一份刚解析的简历里，列出 Agent 的疑问。

和「缺口分析」的区别：缺口只问「缺什么」，答疑还要问「我解析出来的
对不对」。解析永远会有错——学校和专业粘在一起、日期对不上、标题混进
正文——这些必须在用户动笔改之前先摆出来。

分四类：
    confirm    解析结果存疑，需要确认
    missing    关键信息缺失
    thin       内容太简略
    artifact   解析痕迹（标题、序号混进了正文）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import ResumeDoc
from .gaps import analyze

ARTIFACT_MARKERS = ("主要工作", "工作内容", "项目职责", "技术栈", "任职要求", "岗位职责")
DATE = re.compile(r"\d{4}[.\-/]\d{1,2}")


@dataclass
class Doubt:
    id: str
    category: str            # confirm | missing | thin | artifact
    question: str
    context: str = ""
    section: str | None = None
    item: int | None = None

    @property
    def category_cn(self) -> str:
        return {
            "confirm": "确认",
            "missing": "缺失",
            "thin": "简略",
            "artifact": "解析痕迹",
        }.get(self.category, self.category)


@dataclass
class DoubtReport:
    doubts: list[Doubt] = field(default_factory=list)

    def by_category(self, category: str) -> list[Doubt]:
        return [d for d in self.doubts if d.category == category]

    @property
    def confirm(self) -> list[Doubt]:
        return self.by_category("confirm")

    @property
    def others(self) -> list[Doubt]:
        return [d for d in self.doubts if d.category != "confirm"]


def _has_artifact(text: str) -> str | None:
    for marker in ARTIFACT_MARKERS:
        if marker in text and len(text) > len(marker) + 6:
            return marker
    return None


def generate(doc: ResumeDoc) -> DoubtReport:
    report = DoubtReport()

    # 先收集「确认」类疑问，避免后面缺口重复问同一件事
    confirmed_role: set[tuple[str, int]] = set()

    for section in doc.sections:
        for index, item in enumerate(section.items):
            label = item.org or item.role or f"第 {index + 1} 条"

            # 有职位没机构，或有机构没职位
            if item.org and not item.role and section.type in ("experience", "project"):
                confirmed_role.add((section.type, index))
                report.doubts.append(
                    Doubt(id=f"confirm-{section.type}-{index}-role",
                          category="confirm",
                          question=f"「{label}」里你担任什么职位 / 角色？",
                          section=section.type, item=index)
                )
            if item.role and not item.org:
                report.doubts.append(
                    Doubt(id=f"confirm-{section.type}-{index}-org",
                          category="confirm",
                          question=f"「{item.role}」是哪个学校 / 公司 / 项目的？",
                          section=section.type, item=index)
                )

            # 日期只写了半个
            if item.start and not item.end and section.type in ("education", "experience", "project"):
                report.doubts.append(
                    Doubt(id=f"confirm-{section.type}-{index}-end",
                          category="confirm",
                          question=f"「{label}」只有开始时间 {item.start}，结束时间是什么？",
                          section=section.type, item=index)
                )

            # 解析痕迹
            for bullet in item.bullets:
                marker = _has_artifact(bullet.text)
                if marker:
                    report.doubts.append(
                        Doubt(id=f"artifact-{section.type}-{index}",
                              category="artifact",
                              question=f"「{label}」里有条内容混进了「{marker}」这样的标题，我帮你拆开还是删掉？",
                              context=bullet.text, section=section.type, item=index)
                    )
                    break

    # 缺失 / 简略 复用缺口分析，但跳过已由「确认」问过的角色
    for gap in analyze(doc).gaps:
        if gap.key.endswith(":role") and (gap.section, gap.item) in confirmed_role:
            continue
        category = {
            "basic": "missing", "start": "missing", "time": "missing",
            "role": "missing", "detail": "missing", "metrics": "thin",
            "tech": "missing", "team": "missing", "thin": "thin",
        }.get(gap.key.split(":")[-1], "missing")
        report.doubts.append(
            Doubt(id=f"gap-{gap.key}", category=category, question=gap.question,
                  section=gap.section, item=gap.item)
        )

    # 优先级：确认 > 解析痕迹 > 缺失 > 简略
    order = {"confirm": 0, "artifact": 1, "missing": 2, "thin": 3}
    report.doubts.sort(key=lambda d: order[d.category])
    return report
