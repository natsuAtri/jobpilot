"""解析层入口：PDF → ResumeDoc。"""
from .pdf import Line, PageText, Segment, extract as extract_pages, render_png, to_prompt_text
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
