"""导出后的回读校验。

为什么必须有这一步：
Chromium 生成 PDF 时，某些中文字体的 ToUnicode 映射会写错码位——
Noto Sans SC 就会把「工」「大」写成康熙部首 U+2F27 / U+2F24，
ATS 读出来是「西北⼯业⼤学」，关键词直接失配。

这类问题肉眼完全看不出来，只能靠「把 PDF 再读一遍，和源文本比对」。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium

A4_SIZE = (595.0, 842.0)
A4_TOLERANCE = 3.0
WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """去空白与零宽字符。

    不合并全角/半角、不做形近字归一——那会掩盖真正的码位错误。
    """
    text = WHITESPACE.sub("", text)
    return "".join(c for c in text if unicodedata.category(c) not in ("Cf", "Cc"))


def extract_text(pdf_path: str | Path) -> tuple[str, int, tuple[float, float]]:
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        pages = len(pdf)
        width, height = pdf[0].get_size()
        chunks: list[str] = []
        for index in range(pages):
            chunks.append(pdf[index].get_textpage().get_text_range())
        return "\n".join(chunks), pages, (round(width, 1), round(height, 1))
    finally:
        pdf.close()


@dataclass
class VerifyResult:
    ok: bool
    pages: int
    page_size: tuple[float, float]
    checked: int = 0
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "pages": self.pages,
            "page_size": self.page_size,
            "checked": self.checked,
            "missing": self.missing[:20],
            "notes": self.notes,
            "message": self.message,
        }


def verify_pdf(pdf_path: str | Path, fragments: list[str]) -> VerifyResult:
    """逐条校验：每个片段都必须作为子串出现在从 PDF 抽回的文本里。

    顺序无关，因此不受渲染器抽取顺序影响；丢字、错字、整段丢失都能抓到。
    """
    raw, pages, size = extract_text(pdf_path)
    haystack = normalize(raw)

    missing = [f for f in fragments if normalize(f) and normalize(f) not in haystack]

    issues: list[str] = []
    if missing:
        issues.append(f"{len(missing)}/{len(fragments)} 段文字没有出现在 PDF 里")
    if abs(size[0] - A4_SIZE[0]) > A4_TOLERANCE or abs(size[1] - A4_SIZE[1]) > A4_TOLERANCE:
        issues.append(f"页面尺寸不是 A4：{size}")
    # 单页模板装不下就顺延到第 2 页，别因此拦下导出；超过 2 页说明该精简了
    notes: list[str] = []
    if pages > 2:
        issues.append(f"输出 {pages} 页，超过 2 页上限，建议精简内容")
    elif pages == 2:
        notes.append("内容较多，输出 2 页")

    ok = not issues
    message = "通过：全文可提取，字符与源一致" if ok else "；".join(issues)
    if ok and notes:
        message += "（" + "；".join(notes) + "）"
    return VerifyResult(ok, pages, size, len(fragments), missing, notes, message)
