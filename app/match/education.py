"""学历判定。

为什么单独判定、不进加权分：学历是门槛不是质量，它和其他维度之间
不是可加和关系。一条「硕士及以上」用分数折算成 60 分，比直接标一句
「要求硕士」更糊涂。

为什么区分软硬：「硕士优先」和「硕士及以上」在现实里差别巨大，
一律当门槛会把大量实际可投的岗位误判掉。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import ResumeDoc

LEVELS: dict[str, int] = {
    "博士": 5, "博士研究生": 5,
    "硕士": 4, "研究生": 4,
    "本科": 3, "学士": 3,
    "大专": 2, "专科": 2, "高职": 2,
    "高中": 1, "中专": 1,
}

LEVEL_WORDS = "|".join(sorted(LEVELS, key=len, reverse=True))

# 「本科及以上」「硕士以上」「本科或以上」
HARD_RE = re.compile(rf"({LEVEL_WORDS})\s*(?:学历)?\s*(?:及以上|或以上|以上)")
# 「硕士优先」「本科及以上优先」「硕士加分」
SOFT_RE = re.compile(rf"({LEVEL_WORDS})[^。；\n]{{0,6}}?(?:优先|加分|更佳|者优先)")

STATUS_CN = {
    "below": "不满足",
    "meet": "符合",
    "exceed": "高于要求",
    "unknown": "无法判断",
}


@dataclass
class EducationCheck:
    requirement: str
    candidate: str
    status: str          # below | meet | exceed | unknown
    soft: bool = False
    note: str = ""

    @property
    def status_cn(self) -> str:
        return STATUS_CN.get(self.status, self.status)

    def as_dict(self) -> dict:
        return {
            "requirement": self.requirement,
            "candidate": self.candidate,
            "status": self.status,
            "soft": self.soft,
            "note": self.note,
        }


def parse_requirement(jd_text: str) -> tuple[int | None, bool, str]:
    """返回 (要求的学历等级, 是否软性, 原文片段)。"""
    if not jd_text:
        return None, False, ""

    # 软性优先匹配：先看「优先/加分」的写法
    soft = SOFT_RE.search(jd_text)
    hard = HARD_RE.search(jd_text)

    if soft and (not hard or soft.start() <= hard.start()):
        return LEVELS[soft.group(1)], True, soft.group(0).strip()

    if hard:
        return LEVELS[hard.group(1)], False, hard.group(0).strip()

    # 只写了学历词，没写「及以上」
    bare = re.search(rf"({LEVEL_WORDS})\s*(?:学历)", jd_text)
    if bare:
        return LEVELS[bare.group(1)], False, bare.group(0).strip()
    return None, False, ""


def candidate_level(doc: ResumeDoc) -> tuple[int | None, str]:
    """从简历教育经历里取最高学历。"""
    best: int | None = None
    label = ""
    for section in doc.sections:
        if section.type != "education":
            continue
        for item in section.items:
            text = " ".join(x for x in (item.role, item.org) if x)
            for word, level in LEVELS.items():
                if word in text and (best is None or level > best):
                    best, label = level, word
    return best, label


def judge(doc: ResumeDoc, jd_text: str) -> EducationCheck:
    need_level, soft, snippet = parse_requirement(jd_text)
    have_level, have_label = candidate_level(doc)

    if need_level is None:
        return EducationCheck("未提及", have_label or "未知", "unknown",
                              note="JD 没有写明学历要求")
    if have_level is None:
        return EducationCheck(snippet, "未知", "unknown",
                              soft=soft, note="简历里没有可识别的学历信息")

    if have_level > need_level:
        status = "exceed"
    elif have_level == need_level:
        status = "meet"
    else:
        status = "below"

    note = ""
    if status == "below":
        note = "要求「优先」而非硬门槛，可以投" if soft else "这条是硬门槛，投了大概率被筛"
    return EducationCheck(snippet, have_label, status, soft, note)
