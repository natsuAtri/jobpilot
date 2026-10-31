"""PDF → 视觉行。

用 pypdfium2（BSD）取字符级坐标，自己做行聚类与分段。
为什么不直接用现成的段落抽取：简历的价值全在版面位置上——右对齐的日期、
两栏的技能栏、缩进的 bullet。丢了坐标，结构化就无从谈起。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2 as pdfium

# 行聚类：垂直重叠超过较矮者高度的这个比例，判为同一行
LINE_OVERLAP_RATIO = 0.45
# 同一行内，水平间距超过 (基准 * size) 或该绝对值，切分为独立段
SEGMENT_GAP_MIN = 14.0
SEGMENT_GAP_RATIO = 1.1
# 缺失空格时的补救：拉丁字符之间的间距超过该比例补一个空格（CJK 不补）
SPACE_GAP_RATIO = 0.22


@dataclass
class Char:
    ch: str
    x0: float
    x1: float
    bottom: float
    top: float

    @property
    def height(self) -> float:
        return self.top - self.bottom


@dataclass
class Segment:
    x0: float
    x1: float
    text: str


@dataclass
class Line:
    y: float                 # 基线近似（字符底边众数）
    top: float
    bottom: float
    size: float
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)

    @property
    def x0(self) -> float:
        return self.segments[0].x0 if self.segments else 0.0

    @property
    def x1(self) -> float:
        return self.segments[-1].x1 if self.segments else 0.0


@dataclass
class PageText:
    index: int
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)


def _read_chars(textpage) -> list[Char]:
    chars: list[Char] = []
    count = textpage.count_chars()
    for i in range(count):
        ch = textpage.get_text_range(i, 1)
        if not ch or ch in ("\x00", "\ufffd"):
            continue
        try:
            left, bottom, right, top = textpage.get_charbox(i)
        except Exception:  # noqa: BLE001 - 个别字符取不到框，跳过
            continue
        # 空白字符保留：PDF 文本流里的空格比「用间距反推」可靠得多
        chars.append(Char(ch, left, right, bottom, top))
    return chars


def _is_cjk(ch: str) -> bool:
    for c in ch:
        code = ord(c)
        if 0x4E00 <= code <= 0x9FFF or 0x3000 <= code <= 0x303F or 0xFF00 <= code <= 0xFFEF:
            return True
    return False


def _overlap(a_bottom: float, a_top: float, b_bottom: float, b_top: float) -> float:
    return min(a_top, b_top) - max(a_bottom, b_bottom)


def _cluster_lines(chars: list[Char]) -> list[list[Char]]:
    if not chars:
        return []
    ordered = sorted(chars, key=lambda c: (-c.top, c.x0))
    lines: list[list[Char]] = []
    current: list[Char] = [ordered[0]]
    cur_top, cur_bottom = ordered[0].top, ordered[0].bottom

    for c in ordered[1:]:
        height = max(min(c.height, cur_top - cur_bottom), 0.1)
        if _overlap(c.bottom, c.top, cur_bottom, cur_top) >= LINE_OVERLAP_RATIO * height:
            current.append(c)
            cur_top = max(cur_top, c.top)
            cur_bottom = min(cur_bottom, c.bottom)
        else:
            lines.append(current)
            current = [c]
            cur_top, cur_bottom = c.top, c.bottom
    lines.append(current)
    return lines


_TIGHTEN = (
    # 字间距被误判成空格：数字/日期内部不该有空格
    (re.compile(r"(?<=\d)\s+(?=[\d.\-/年月日])"), ""),
    (re.compile(r"(?<=[.\-/])\s+(?=\d)"), ""),
    # 中文之间的空格基本是字间距，不是词间空格
    (re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff：:（(])"), ""),
)


def _tighten(text: str) -> str:
    for pattern, repl in _TIGHTEN:
        text = pattern.sub(repl, text)
    return text


def _build_segments(chars: list[Char], size: float) -> list[Segment]:
    ordered = sorted(chars, key=lambda c: c.x0)
    gap_limit = max(SEGMENT_GAP_MIN, SEGMENT_GAP_RATIO * size)
    space_limit = SPACE_GAP_RATIO * size

    segments: list[Segment] = []
    buf: list[str] = []
    prev: Char | None = None
    seg_x0 = ordered[0].x0
    seg_x1 = ordered[0].x1

    for c in ordered:
        if prev is not None:
            gap = c.x0 - prev.x1
            if gap > gap_limit:
                segments.append(Segment(seg_x0, seg_x1, _tighten("".join(buf).strip())))
                buf = []
                seg_x0 = c.x0
            elif (
                gap > space_limit
                and buf
                and not buf[-1].endswith(" ")
                and not c.ch.isspace()
                and not _is_cjk(prev.ch)
                and not _is_cjk(c.ch)
            ):
                # 只给拉丁字符补空格；CJK 的字形间距本来就该为零
                buf.append(" ")
        buf.append(c.ch)
        seg_x1 = max(seg_x1, c.x1)
        prev = c
    if buf:
        segments.append(Segment(seg_x0, seg_x1, _tighten("".join(buf).strip())))

    return [s for s in segments if s.text]


def extract(path: str | Path) -> list[PageText]:
    """解析整份 PDF，返回每页的视觉行。"""
    pdf = pdfium.PdfDocument(str(path))
    pages: list[PageText] = []
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            width, height = page.get_size()
            textpage = page.get_textpage()
            chars = _read_chars(textpage)
            result = PageText(index=index, width=width, height=height)

            for group in _cluster_lines(chars):
                size = max(c.height for c in group)
                # 用出现最多的字符高度作为正文字号，避免被个别大字影响
                heights = sorted(round(c.height, 1) for c in group)
                median_h = heights[len(heights) // 2] if heights else size
                segments = _build_segments(group, median_h)
                if not segments:
                    continue
                result.lines.append(
                    Line(
                        y=min(c.bottom for c in group),
                        top=max(c.top for c in group),
                        bottom=min(c.bottom for c in group),
                        size=round(size, 2),
                        segments=segments,
                    )
                )
            result.lines.sort(key=lambda ln: -ln.top)
            pages.append(result)
    finally:
        pdf.close()
    return pages


def render_png(path: str | Path, index: int, scale: float = 2.0) -> bytes:
    """把某页渲染成 PNG，供校对界面左侧显示。"""
    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[index]
        bitmap = page.render(scale=scale)
        buffer = io.BytesIO()
        bitmap.to_pil().save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    finally:
        pdf.close()


def extract_photo(path: str | Path) -> tuple[bytes, str] | None:
    """从 PDF 首页提取照片，返回 (数据, 扩展名)。

    简历里的证件照通常是页面里唯一的一张位图。取「像素面积最大」的那张，
    比按坐标猜位置更稳；DCTDecode 的直接拿原始 JPEG，其它格式转 JPEG。
    """
    pdf = pdfium.PdfDocument(str(path))
    try:
        if len(pdf) == 0:
            return None
        page = pdf[0]
        images = [obj for obj in page.get_objects() if isinstance(obj, pdfium.PdfImage)]
        if not images:
            return None

        def area(obj) -> int:
            try:
                width, height = obj.get_px_size()
                return int(width) * int(height)
            except Exception:  # noqa: BLE001
                return 0

        best = max(images, key=area)
        if area(best) < 4000:  # 太小，多半是图标或线条
            return None

        try:
            filters = best.get_filters() or []
        except Exception:  # noqa: BLE001
            filters = []
        if "DCTDecode" in filters:
            data = bytes(best.get_data())
            if data[:2] == b"\xff\xd8":  # JPEG 魔数
                return data, "jpg"

        bitmap = best.get_bitmap()
        buffer = io.BytesIO()
        bitmap.to_pil().convert("RGB").save(buffer, format="JPEG", quality=92)
        return buffer.getvalue(), "jpg"
    except Exception:  # noqa: BLE001 - 提照片失败不影响解析
        return None
    finally:
        pdf.close()


def to_prompt_text(pages: list[PageText]) -> str:
    """把视觉行压成 LLM 可读的紧凑表示。

    每行：`y|size| x:text | x:text`
    保留 x 是因为右对齐的日期、两栏布局只能靠坐标区分。
    """
    out: list[str] = []
    for page in pages:
        out.append(f"=== page {page.index + 1} ({page.width:.0f}x{page.height:.0f}) ===")
        for line in page.lines:
            segs = " | ".join(f"{s.x0:.0f}:{s.text}" for s in line.segments)
            out.append(f"{line.y:.0f}|{line.size:.0f}| {segs}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for page in extract(sys.argv[1]):
        print(f"===== page {page.index + 1} {page.width:.0f}x{page.height:.0f} =====")
        for line in page.lines:
            segs = "  ||  ".join(f"[x{s.x0:.0f}] {s.text}" for s in line.segments)
            print(f"y{line.y:6.1f} s{line.size:5.1f} | {segs}")
