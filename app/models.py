"""领域模型（Pydantic）。

这里是简历结构的唯一真源：解析产出它、编辑修改它、渲染消费它、
对话补录往它里面写 patch。所有环节共用同一套字段，避免转换层。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SectionType = Literal[
    "education", "experience", "project", "skill", "award", "cert", "other"
]

SECTION_LABELS: dict[str, str] = {
    "education": "教育经历",
    "experience": "工作经历",
    "project": "项目经历",
    "skill": "专业技能",
    "award": "荣誉奖项",
    "cert": "证书资质",
    "other": "其他",
}


class Bullet(BaseModel):
    """一条经历描述。改写的最小单位，也是三守恒的校验对象。"""

    text: str
    order_index: int = 0
    tags: list[str] = Field(default_factory=list)          # 技能标签
    strength: int = 3                                       # 责任强度 1..5
    metrics: list[str] = Field(default_factory=list)        # 抽出的数字
    source: Literal["parse", "chat", "generate", "manual"] = "parse"
    user_utterance: str | None = None                       # 对话来源的原话
    source_bullet_id: int | None = None                     # 改写溯源
    confidence: float = 1.0


class Item(BaseModel):
    """一段经历：学校 / 公司 / 项目。"""

    org: str | None = None
    role: str | None = None
    location: str | None = None
    start: str | None = None
    end: str | None = None
    meta: dict[str, str] = Field(default_factory=dict)      # 技术栈、语言成绩等
    bullets: list[Bullet] = Field(default_factory=list)
    order_index: int = 0
    confidence: float = 1.0


class Section(BaseModel):
    type: SectionType
    title: str | None = None                                # 覆盖默认章节名
    items: list[Item] = Field(default_factory=list)
    order_index: int = 0
    confidence: float = 1.0


class ResumeDoc(BaseModel):
    name: str = ""
    phone: str | None = None
    email: str | None = None
    location: str | None = None
    links: dict[str, str] = Field(default_factory=dict)
    intents: str | None = None                              # 求职意向
    sections: list[Section] = Field(default_factory=list)

    # 解析产物附带的信息，不参与渲染
    warnings: list[str] = Field(default_factory=list)
    confidence: float = 1.0

    def section(self, type_: SectionType) -> Section | None:
        return next((s for s in self.sections if s.type == type_), None)

    def bullet_count(self) -> int:
        return sum(len(i.bullets) for s in self.sections for i in s.items)


class ParsedLine(BaseModel):
    """PDF 中的一个视觉行（供校对界面与 LLM 使用）。"""

    page: int
    y: float
    size: float
    segments: list["ParsedSegment"]


class ParsedSegment(BaseModel):
    x0: float
    x1: float
    text: str


ParsedLine.model_rebuild()
