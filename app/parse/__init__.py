"""解析层入口：PDF → ResumeDoc。"""
from .pdf import Line, PageText, Segment, render_png, to_prompt_text
from .pdf import extract as extract_pages
from .rules import extract as extract_rules

__all__ = [
    "Line",
    "PageText",
    "Segment",
    "extract_pages",
    "extract_rules",
    "render_png",
    "to_prompt_text",
]
