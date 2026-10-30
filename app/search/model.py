"""搜索结果的数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field


def infer_job_type(experience: str) -> str:
    """从年限文本推断岗位类型（校招/社招/实习）。"""
    if any(w in experience for w in ("应届", "在校", "毕业生")):
        return "campus"
    if "实习" in experience:
        return "intern"
    if experience and experience not in ("经验不限", "不限"):
        return "social"
    return "unknown"


@dataclass
class SearchJob:
    title: str
    company: str = ""
    salary: str = ""
    location: str = ""
    experience: str = ""       # 年限，如「3年及以上」
    education: str = ""        # 学历，如「本科」
    url: str = ""
    platform: str = "51job"
    source_id: str = ""        # 平台侧唯一 id，用于去重
    description: str = ""      # 完整 JD 正文
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "company": self.company,
            "salary": self.salary,
            "location": self.location,
            "experience": self.experience,
            "education": self.education,
            "url": self.url,
            "platform": self.platform,
            "source_id": self.source_id,
            "description": self.description,
            "tags": self.tags,
        }


@dataclass
class SearchResult:
    keyword: str
    city: str
    jobs: list[SearchJob] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.jobs)
