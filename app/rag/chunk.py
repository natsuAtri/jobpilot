"""分块：把简历和 JD 切成可检索的片段。

粒度要落到「一句对一句」：简历按 bullet 切（附所属条目的上下文），
JD 按行切（职责和要求的每一行都是独立证据单元）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import ResumeDoc

_SPLIT = re.compile(r"[。；;\n]+")

# 这些标题下的段落不是「要求」，不该参与匹配/检索
_JD_SKIP_MARKERS = (
    "介绍", "职责", "福利", "公司", "团队", "环境", "我们", "优势", "文化",
    "规模", "地址", "关于", "亮点", "薪酬", "待遇", "发展", "前景", "平台",
    "理念", "荣誉", "关怀",
)

# 标题里出现这些词，才认为这一行是「段落标题」而不是一条列表项
_HEADING_WORDS = (
    "职责", "要求", "资格", "描述", "内容", "介绍", "技术栈", "技能", "能力",
    "福利", "待遇", "公司", "团队", "环境", "加分", "优先", "任职", "岗位",
    "项目", "任务", "背景", "地点", "信息",
)

_HEADING_PATTERNS = (
    re.compile(r"^【([^】]{1,24})】\s*$"),                       # 【岗位职责】
    re.compile(r"^#{1,4}\s*(.{1,24})$"),                        # ## 岗位职责
    re.compile(r"^[一二三四五六七八九十]+\s*[、.．]\s*(.{1,24})$"),  # 一、岗位职责
    re.compile(r"^[（(][一二三四五六七八九十]+[）)]\s*(.{1,24})$"),  # （一）岗位职责
    re.compile(r"^\d{1,2}\s*[、.．]\s*(.{1,24})$"),              # 1、任职要求
)


def _heading_title(line: str) -> str | None:
    """判断一行是不是段落标题，是则返回标题文字。"""
    text = line.strip()
    if not text or len(text) > 30:
        return None
    for pattern in _HEADING_PATTERNS:
        match = pattern.match(text)
        if match:
            title = match.group(1).strip()
            if any(word in title for word in _HEADING_WORDS):
                return title
    return None


def _split_jd_sections(description: str) -> list[tuple[str | None, str]]:
    """按标题（【】/编号/markdown）切分 JD，返回 (标题, 正文) 列表。

    不依赖某一种标记：没有标题的 JD 退化成一个整段，仍可正常分块。
    """
    sections: list[tuple[str | None, str]] = []
    current_title: str | None = None
    body: list[str] = []
    for line in description.split("\n"):
        title = _heading_title(line)
        if title is not None:
            if current_title is not None or any(b.strip() for b in body):
                sections.append((current_title, "\n".join(body)))
            current_title = title
            body = []
            continue
        body.append(line)
    if current_title is not None or any(b.strip() for b in body):
        sections.append((current_title, "\n".join(body)))
    return sections or [(None, description)]


def requirement_text(description: str) -> str:
    """只保留 JD 的「要求」类段落，去掉团队介绍/工作职责等。

    这些段落（团队与项目介绍、工作职责、公司福利）写的是「这家公司/岗位
    是什么」，不是「要你具备什么」——把它们喂进匹配只会产生假技能和
    无关的语义证据。
    """
    if not description:
        return ""
    sections = _split_jd_sections(description)
    if len(sections) == 1 and sections[0][0] is None:
        return description  # 没有【】标记，无法区分，原样返回
    kept: list[str] = []
    for title, body in sections:
        if title is not None and any(k in title for k in _JD_SKIP_MARKERS):
            continue
        kept.append(body)
    return "\n".join(kept)


@dataclass
class Chunk:
    text: str
    label: str          # 上下文标签，如「项目 · 基于YOLOv8 的手语翻译系统」
    kind: str           # resume | jd
    ref_id: int | None = None

    def as_dict(self) -> dict:
        return {"text": self.text, "label": self.label, "kind": self.kind, "ref_id": self.ref_id}


def chunk_resume(doc: ResumeDoc) -> list[Chunk]:
    """每条 bullet 一个 chunk；技能章节把标签和内容拼成一条。"""
    chunks: list[Chunk] = []
    for section in doc.sections:
        for item in section.items:
            head = " / ".join(x for x in (item.org, item.role) if x) or "未命名"
            label = f"{section.type} · {head}"
            for bullet in item.bullets:
                text = bullet.text.strip()
                if text:
                    chunks.append(Chunk(text=text, label=label, kind="resume"))
            # 技能类条目本身（org 是标签、bullet 是内容）已包含在 bullet 里；
            # 教育类条目若没有 bullet，把 meta 逐条切开，别拼成一个巨块
            if not item.bullets and item.meta:
                for key, value in item.meta.items():
                    if value.strip():
                        chunks.append(Chunk(text=f"{key}：{value}", label=label, kind="resume"))
    return chunks


def chunk_jd(description: str, ref_id: int | None = None) -> list[Chunk]:
    """JD 按行/句切，过滤掉过短和纯标点的碎片，并剔除「介绍/职责」类段落。"""
    if not description:
        return []
    pieces = [p.strip() for p in _SPLIT.split(requirement_text(description))]
    chunks: list[Chunk] = []
    for piece in pieces:
        piece = re.sub(r"\s+", " ", piece).strip(" ··-—")
        if len(piece) >= 6:
            chunks.append(Chunk(text=piece, label="jd", kind="jd", ref_id=ref_id))
    return chunks
