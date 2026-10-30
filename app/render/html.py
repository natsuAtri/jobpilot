"""HTML 渲染。"""
from __future__ import annotations

from ..models import ResumeDoc
from ..templating import templates
from .context import build_context


def render_html(doc: ResumeDoc, *, theme: str = "ink", variant: str = "design") -> str:
    context = build_context(doc, theme=theme, variant=variant)
    template = templates.get_template("resume/document.html")
    return template.render(**context)
