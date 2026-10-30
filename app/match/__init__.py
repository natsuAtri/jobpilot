"""匹配：学历判定、技能覆盖、维度打分。"""
from .education import EducationCheck, candidate_level, judge, parse_requirement
from .scoring import Dimension, MatchResult, candidate_years, evaluate, project_years
from .skills import coverage, extract, ratio, resume_pool

__all__ = [
    "candidate_level",
    "candidate_years",
    "coverage",
    "Dimension",
    "EducationCheck",
    "evaluate",
    "extract",
    "judge",
    "MatchResult",
    "parse_requirement",
    "project_years",
    "ratio",
    "resume_pool",
]
