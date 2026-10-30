"""渲染层：ResumeDoc → HTML → PDF → 回读校验。"""
from .context import build_context, expected_fragments, plain_text
from .html import render_html
from .verify import VerifyResult, verify_pdf

__all__ = [
    "build_context",
    "expected_fragments",
    "plain_text",
    "render_html",
    "VerifyResult",
    "verify_pdf",
]
