"""匹配打分。

原则：能算的不问模型。技能覆盖、年限、语言成绩都是确定性计算，
只有「领域相关性」和「职级匹配」需要语义理解，留给后续 LLM 补。

分数只由可解释的维度加权，不让模型直接吐一个总分——
否则你永远不知道 72 分是哪里来的，也没法针对性地改。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..models import ResumeDoc
from . import skills as skills_mod
from .education import EducationCheck, judge

# 各维度默认权重；某个维度算不出来时会被剔除并重新归一化
WEIGHTS: dict[str, float] = {
    # 会几门语言不重要，项目对不对口才重要——权重按这个原则重排
    "project": 0.45,      # 项目对口：JD 要求有多少能被简历项目接住
    "experience": 0.25,   # 年限（硬门槛）
    "direction": 0.15,    # 方向一致性
    "skills": 0.10,       # 关键词覆盖（编程语言等，参考价值有限）
    "language": 0.05,     # 语言证书
}

DIMENSION_CN = {
    "project": "项目对口",
    "skills": "技能覆盖",
    "experience": "年限",
    "language": "语言",
    "domain": "领域相关",
    "direction": "方向相关",
    "seniority": "职级",
}

DATE_RE = re.compile(r"(\d{4})\s*[.\-/年]\s*(\d{1,2})?")

FRESH_GRAD = ("应届", "在校", "毕业生", "实习")
YEARS_RANGE = re.compile(r"(\d+)\s*[-–~至]\s*(\d+)\s*年")
YEARS_MIN = re.compile(r"(\d+)\s*年以上")
YEARS_EXACT = re.compile(r"(\d+)\s*年")
END_RESUME_SKILL = {"skill"}


@dataclass
class Dimension:
    name: str
    value: float            # 0..1
    weight: float
    note: str = ""

    @property
    def label(self) -> str:
        return DIMENSION_CN.get(self.name, self.name)


@dataclass
class MatchResult:
    total: float
    dimensions: list[Dimension] = field(default_factory=list)
    education: EducationCheck | None = None
    keyword_coverage: dict[str, list[str]] = field(default_factory=dict)
    gaps: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total": round(self.total, 1),
            "dimensions": [
                {"name": d.name, "label": d.label, "value": round(d.value, 3),
                 "weight": round(d.weight, 3), "note": d.note}
                for d in self.dimensions
            ],
            "education": self.education.as_dict() if self.education else None,
            "keyword_coverage": self.keyword_coverage,
            "gaps": self.gaps,
            "notes": self.notes,
        }


# --------------------------------------------------------------- 年限
def _parse_ym(value: str | None) -> float | None:
    if not value:
        return None
    match = DATE_RE.search(value)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2) or 6)
    return year + (month - 1) / 12.0


def _span(doc: ResumeDoc, types: set[str], today: date | None = None) -> float:
    starts: list[float] = []
    ends: list[float] = []
    for section in doc.sections:
        if section.type not in types:
            continue
        for item in section.items:
            start = _parse_ym(item.start)
            end = _parse_ym(item.end)
            if start is not None:
                starts.append(start)
            if end is not None:
                ends.append(end)
            elif start is not None:
                now = today or date.today()
                ends.append(now.year + (now.month - 1) / 12.0)
    if not starts:
        return 0.0
    return max(0.0, max(ends or starts) - min(starts))


def candidate_years(doc: ResumeDoc, today: date | None = None) -> float:
    """工作年限，只算工作经历。

    教育经历绝不能计入——把在校时间算成工作经验，会让「要求 1 年经验」
    的岗位对一名应届生显示「完全满足」，这是实打实的误判。
    """
    return _span(doc, {"experience"}, today)


def project_years(doc: ResumeDoc, today: date | None = None) -> float:
    """项目经历跨度，仅在完全没有工作经历时作为近似。"""
    return _span(doc, {"project"}, today)


def parse_experience_requirement(jd_text: str) -> tuple[float, float | None] | None:
    """返回 (最低年限, 最高年限或 None)。"""
    if any(word in jd_text for word in FRESH_GRAD) and "年" not in jd_text:
        return (0.0, None)
    match = YEARS_RANGE.search(jd_text)
    if match:
        return (float(match.group(1)), float(match.group(2)))
    match = YEARS_MIN.search(jd_text)
    if match:
        return (float(match.group(1)), None)
    match = YEARS_EXACT.search(jd_text)
    if match:
        return (float(match.group(1)), float(match.group(1)))
    return None


def _experience_dimension(doc: ResumeDoc, jd_text: str, job_type: str = "unknown") -> Dimension | None:
    requirement = parse_experience_requirement(jd_text)
    if requirement is None:
        return None
    need_min, need_max = requirement

    work = candidate_years(doc)
    projects = project_years(doc)
    # 校招/实习岗对应届生放开年限：这类岗位写的「N 年」通常是加分项不是门槛
    if job_type in ("campus", "intern") and work <= 0:
        return Dimension("experience", 1.0, WEIGHTS["experience"],
                         "校招/实习岗，无工作经历也符合预期")

    span = f"{need_min:g}" + (f"–{need_max:g}" if need_max and need_max != need_min else "")
    if need_min == 0:
        return Dimension("experience", 1.0, WEIGHTS["experience"], "经验要求不限")

    # 无工作经历：项目跨度折半最多顶到 0.5，要求 2 年及以上直接判 0——
    # 不能再用「项目经验折半 ≈ 0.83」糊弄过去，那会让应届生把「要求 3 年」
    # 的社招岗位排到前面。
    if work <= 0:
        if need_min >= 2:
            return Dimension("experience", 0.0, WEIGHTS["experience"],
                             f"要求 {span} 年，简历无工作经历")
        have = projects * 0.5
        value = min(0.5, have / need_min)
        return Dimension("experience", value, WEIGHTS["experience"],
                         f"要求 {span} 年；无工作经历，项目跨度折半估算 {have:.1f} 年")

    value = min(1.0, work / need_min)
    note = f"要求 {span} 年，简历工作经历 {work:.1f} 年"
    if need_max and work > need_max:
        note += "（超出上限，可能被认为资历过高）"
    return Dimension("experience", value, WEIGHTS["experience"], note)


# --------------------------------------------------------------- 语言
CET6 = ("六级", "cet-6", "cet6")
CET4 = ("四级", "cet-4", "cet4")
IELTS = ("雅思", "ielts")


def _language_dimension(doc: ResumeDoc, jd_text: str) -> Dimension | None:
    wants = None
    for word in CET6 + IELTS + ("英语", "英文"):
        if word in jd_text.lower():
            wants = word
            break
    if wants is None:
        return None

    text = " ".join(
        b.text + " " + " ".join(f"{k}{v}" for k, v in item.meta.items())
        for section in doc.sections
        for item in section.items
        for b in item.bullets
    ).lower()
    text += " " + " ".join(
        " ".join(item.meta.values()) for section in doc.sections for item in section.items
    ).lower()

    if wants in ("英语", "英文"):
        value = 1.0 if (any(k in text for k in CET4 + CET6 + IELTS)) else 0.0
        return Dimension("language", value, WEIGHTS["language"], "岗位要求英语能力")
    if wants in CET6:
        value = 1.0 if any(k in text for k in CET6) else 0.0
        return Dimension("language", value, WEIGHTS["language"], "岗位要求英语六级")
    if wants in IELTS:
        value = 1.0 if any(k in text for k in IELTS) else 0.0
        return Dimension("language", value, WEIGHTS["language"], "岗位要求雅思")
    return None


# --------------------------------------------------------------- 汇总
def evaluate(doc: ResumeDoc, jd_text: str, job_type: str = "unknown",
             direction: float | None = None,
             project: float | None = None) -> MatchResult:
    from ..rag.chunk import requirement_text

    # 只对「要求」类段落打分，团队介绍/工作职责不算要求
    req_text = requirement_text(jd_text)

    result = MatchResult(total=0.0)
    result.education = judge(doc, req_text)

    required = skills_mod.extract(req_text)
    pool = skills_mod.resume_pool(doc)
    cov = skills_mod.coverage(required, pool)
    result.keyword_coverage = cov

    dimensions: list[Dimension] = []
    if required:
        dimensions.append(
            Dimension("skills", skills_mod.ratio(cov), WEIGHTS["skills"],
                      f"命中 {len(cov['hit'])} · 部分 {len(cov['partial'])} · 缺 {len(cov['miss'])}")
        )

    if project is not None:
        dimensions.append(
            Dimension("project", project, WEIGHTS["project"],
                      f"岗位要求中有 {project:.0%} 能被简历项目接住")
        )
    if direction is not None:
        dimensions.append(
            Dimension("direction", direction, WEIGHTS["direction"],
                      f"简历方向与岗位方向相似度 {direction:.0%}")
        )

    exp = _experience_dimension(doc, req_text, job_type)
    if exp:
        dimensions.append(exp)
    lang = _language_dimension(doc, req_text)
    if lang:
        dimensions.append(lang)

    # 岗位类型的独立提示，不参与分数
    if job_type == "social" and candidate_years(doc) <= 0:
        result.notes.append("社招岗位：简历没有工作经历，通常以校招/实习为主，投递前权衡")

    if not dimensions:
        result.notes.append("JD 里没有可量化的要求，无法打分")
        return result

    total_weight = sum(d.weight for d in dimensions)
    total = sum(d.value * d.weight for d in dimensions) / total_weight
    for d in dimensions:
        d.weight = d.weight / total_weight
    result.dimensions = dimensions
    result.total = total * 100

    # 差距：缺失的技能要标明是「没写」还是「没有」
    # 判定口径必须和覆盖逻辑一致：只有可替代性强的类别（语言/框架/算法）
    # 才能说「你有同类技能只是没写」，工具类缺失一律按「无法判断」处理。
    substitutable = {
        skills_mod.CATEGORY.get(s) for s in pool
        if skills_mod.CATEGORY.get(s) in skills_mod.PARTIAL_CATEGORIES
    }
    for skill in cov["miss"]:
        category = skills_mod.CATEGORY.get(skill)
        if category and category in substitutable:
            kind, action = "未体现", "简历里有同类技能但没写这个，建议补充具体使用场景"
        else:
            kind, action = "无法判断", "简历里没有任何相关线索，投递前先确认自己是否具备"
        result.gaps.append({"what": skill, "type": kind, "action": action})

    for skill in cov["partial"]:
        result.gaps.append({
            "what": skill,
            "type": "未体现",
            "action": "简历里有同类技能，但没有直接写这个，建议明确写出来",
        })

    if result.education and result.education.status == "below" and not result.education.soft:
        result.notes.append("学历低于硬性要求，即使分数高也建议谨慎投递")

    return result
