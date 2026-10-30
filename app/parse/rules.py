"""规则抽取：把视觉行切成 章节 → 条目 → 要点。

为什么先做规则版：
1. 没有配置模型时也能跑通整条链路，M1 可独立验证；
2. 章节标题、日期区间、bullet 前缀这些是确定性信号，不该花 token；
3. 规则抽出的骨架交给 LLM 精修，比让 LLM 从零乱猜更稳。

规则做不好的部分（把「某某大学软件工程/本科」拆成学校与专业）留给 LLM。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import Bullet, Item, ResumeDoc, Section, SectionType
from .pdf import Line, PageText

# ---------------------------------------------------------------- 章节词典
SECTION_KEYWORDS: dict[SectionType, tuple[str, ...]] = {
    "education": ("教育经历", "教育背景", "学习经历", "学历背景", "教育情况", "education"),
    "experience": ("工作经历", "工作经验", "实习经历", "实习经验", "职业经历", "工作履历",
                   "工作背景", "experience"),
    "project": ("项目经历", "项目经验", "科研经历", "实践经历", "项目实践", "科研项目",
                "project"),
    "skill": ("专业技能", "技能特长", "个人技能", "技术栈", "技能", "英语能力及技能",
              "英语能力", "语言能力", "技能证书", "语言与技能", "skills"),
    "award": ("荣誉奖项", "获奖情况", "荣誉证书", "奖励荣誉", "获奖", "荣誉",
              "竞赛经历", "竞赛获奖", "获奖经历", "比赛经历", "awards"),
    "cert": ("证书", "资质", "认证", "certificates"),
    "other": ("自我评价", "个人总结", "校园经历", "社团经历", "其他", "简介", "自我描述"),
}

# 章节标题行不应过长，也不该带句号
HEADER_MAX_LEN = 16

DATE_TOKEN = re.compile(
    # 注意交替顺序：先 1[0-2] 再 0?[1-9]，否则 "2023.10" 会被截成 "2023.1"
    r"(?:19|20)\d{2}\s*[.\-/年]\s*(?:1[0-2]|0?[1-9])\s*(?:[.\-/月]\s*(?:3[01]|[12]\d|0?[1-9]))?\s*日?"
)
DATE_RANGE = re.compile(
    rf"(?P<start>{DATE_TOKEN.pattern})\s*[-–—~～至到]+\s*(?P<end>{DATE_TOKEN.pattern}|至今|今|现在)"
)
PRESENT = re.compile(r"至今|今|现在")

BULLET_PREFIX = re.compile(
    r"^\s*(?:\d{1,2}\s*[.、)）]|[•·▪◦●○◆◇⚫⚪■□▫★☆➢➤▸►▶✓✔]|[-—*]|\(\d{1,2}\)|（\d{1,2}）)\s*"
)
KV_SPLIT = re.compile(r"[:：]")

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+?86[-\s]?)?1[3-9]\d{9}")
URL = re.compile(r"(?:https?://)?(?:github\.com|gitee\.com|linkedin\.com|[\w-]+\.(?:com|cn|io|me|dev))/[\w\-./#?=&%]+")


@dataclass
class RawSection:
    type: SectionType
    title: str
    lines: list[Line] = field(default_factory=list)


def _norm(text: str) -> str:
    return re.sub(r"[\s:：·・\-—_/|]+", "", text).lower()


def classify_header(line: Line) -> SectionType | None:
    """判断一行是否是章节标题。

    先精确匹配，再允许「短行包含关键词」——像「职务荣誉」「校园经历」
    这类组合词，字典不可能穷举。
    """
    # 装饰性标点（. · |）不参与标题判断，否则「教育背景 . . .」会判失败
    meaningful = [s.text for s in line.segments if re.search(r"[\w\u4e00-\u9fff]", s.text)]
    text = re.sub(r"[\s.·•・|:：\-—_/]+$", "", " ".join(meaningful).strip())
    if not text or len(text) > HEADER_MAX_LEN:
        return None
    if text.endswith(("。", "；", "，", ".", ";")):
        return None
    key = _norm(text)
    for type_, words in SECTION_KEYWORDS.items():
        for word in words:
            if key == _norm(word):
                return type_
    if len(key) <= 6:
        for type_, words in SECTION_KEYWORDS.items():
            for word in words:
                norm_word = _norm(word)
                if len(norm_word) >= 2 and norm_word in key:
                    return type_
    return None


def _left_margin(lines: list[Line]) -> float:
    if not lines:
        return 0.0
    xs = sorted(ln.x0 for ln in lines)
    return xs[len(xs) // 10]


def _merge_continuations(lines: list[Line], page_width: float) -> list[Line]:
    """把因换行被切开的段落接回去。

    判据：上一行越过了右侧安全区，下一行从左边界重新开始，且不是新条目。
    """
    if not lines:
        return []
    base_x = _left_margin(lines)
    merged: list[Line] = [lines[0]]
    for line in lines[1:]:
        prev = merged[-1]
        prev_end = prev.segments[-1].x1 if prev.segments else 0.0
        prev_is_bullet = bool(BULLET_PREFIX.match(prev.text))
        # 续行要么回到左边界（普通段落），要么对齐上一行正文起点（bullet 之后）
        if prev_is_bullet and len(prev.segments) >= 2:
            align_x = prev.segments[-1].x0
            aligned = abs(line.x0 - align_x) <= 4.0
        else:
            align_x = base_x
            aligned = False
        starts_left = abs(line.x0 - base_x) <= 3.0 or aligned
        no_prefix = not BULLET_PREFIX.match(line.text)
        not_header = classify_header(line) is None
        near_right_edge = prev_end >= page_width - 64
        # 只有「单段」的满行才可能是换行续写；带右对齐日期的标题行不能接
        single_run = len(prev.segments) == 1
        if starts_left and no_prefix and not_header and near_right_edge and (single_run or prev_is_bullet):
            prev.segments[-1].text += line.text
            continue
        merged.append(line)
    return merged


def _split_dates(text: str) -> tuple[str, str | None, str | None]:
    """从标题行剥离日期区间，返回 (剩余文本, start, end)。"""
    m = DATE_RANGE.search(text)
    if not m:
        single = DATE_TOKEN.search(text)
        if single:
            rest = (text[: single.start()] + text[single.end():]).strip()
            return rest, single.group(0).strip(), None
        return text, None, None
    rest = (text[: m.start()] + text[m.end():]).strip()
    return rest, m.group("start").strip(), m.group("end").strip()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" \t·•,，、;；")


def _strip_gpa(text: str) -> str:
    """「GPA: 86/100」这类尾巴不该参与学校/专业切分。"""
    return re.sub(r"(?i)\bGPA\s*[:：]?\s*[\d.\-–/ ]+", " ", text)


def _split_title(rest: str) -> tuple[str | None, str | None]:
    """把「学校/公司 + 专业/职位」粗分一下。规则只能做到这里，精修交给 LLM。"""
    rest = _clean(rest)
    if not rest:
        return None, None
    for sep in ("/", "｜", "|", " - ", " – ", "—"):
        if sep in rest:
            left, right = rest.split(sep, 1)
            return _clean(left) or None, _clean(right) or None
    return rest, None


_SCHOOL_SUFFIX = re.compile(r"(大学|学院|学校|University|College|School)")


def _split_school_major(org: str) -> tuple[str, str]:
    """「某某大学软件工程」→ 学校=某某大学，专业=软件工程。

    以学校后缀（大学/学院/学校）为界：学校是「第一个后缀及之前」，
    专业是其后剩余部分；没有后缀则原样返回、专业留空。
    """
    for m in _SCHOOL_SUFFIX.finditer(org):
        end = m.end()
        major = org[end:].strip(" ·•,/、;；")
        if major:
            return org[:end].strip(), major
    return org, ""


def _bullet_text(line: Line) -> str:
    return _clean(BULLET_PREFIX.sub("", line.text))


def _is_bullet(line: Line, base_x: float) -> bool:
    if BULLET_PREFIX.match(line.text):
        return True
    return line.x0 > base_x + 4.5


def _build_items(section: RawSection, base_x: float) -> list[Item]:
    if section.type == "skill":
        return _build_skill_items(section)
    if section.type in ("award", "cert", "other"):
        # 这类章节通常就是若干条，没有「条目」层级
        bullets = [
            Bullet(text=_bullet_text(ln), order_index=i)
            for i, ln in enumerate(section.lines)
            if _bullet_text(ln)
        ]
        return [Item(bullets=bullets)] if bullets else []

    items: list[Item] = []
    current: Item | None = None

    def _org_role(rest: str) -> tuple[str | None, str | None, dict[str, str]]:
        org, role = _split_title(rest)
        meta: dict[str, str] = {}
        # 教育经历：把「学校+专业」粘连的 org 拆开，专业单独存 meta
        if section.type == "education" and org:
            school, major = _split_school_major(org)
            if major:
                org, meta["专业"] = school, major
        return org, role, meta

    def start_item(line: Line) -> Item:
        rest, start, end = _split_dates(_strip_gpa(line.text))
        org, role, meta = _org_role(rest)
        item = Item(org=org, role=role, start=start, end=end, meta=meta,
                    order_index=len(items))
        items.append(item)
        return item

    for line in section.lines:
        text = _clean(line.text)
        if not text:
            continue

        has_date = bool(DATE_RANGE.search(text) or DATE_TOKEN.search(text))
        kv = KV_SPLIT.search(text)
        is_kv = bool(kv) and len(text[: kv.start()].strip()) <= 8

        if _is_bullet(line, base_x):
            if current is None:
                current = Item()
                items.append(current)
            body = _bullet_text(line)
            if body:
                current.bullets.append(Bullet(text=body, order_index=len(current.bullets)))
            continue

        if is_kv:
            # 「技术栈：…」「语言能力：…」属于条目的附属信息，不是新条目
            if current is None:
                current = Item()
                items.append(current)
            label = _clean(text[: kv.start()])
            value = _clean(text[kv.end():])
            if label in ("主要工作", "工作内容", "项目职责", "职责", "负责内容"):
                continue  # 只是后续要点的引导语
            if value:
                current.meta[label] = value
            continue

        # 带日期 → 一定是新条目；否则，若当前条目已有要点，也视为新条目
        if has_date or current is None or current.bullets:
            current = start_item(line)
        elif not current.org:
            rest, start, end = _split_dates(text)
            current.org, current.role, meta = _org_role(rest)
            current.meta.update(meta)
            current.start, current.end = start, end
        else:
            current.bullets.append(
                Bullet(text=text, order_index=len(current.bullets))
            )

    return [it for it in items if it.org or it.bullets or it.meta]


def _build_skill_items(section: RawSection) -> list[Item]:
    """技能章节常见两种排法：

        编程语言：C++/Java/Python        ← 标签与内容同一行
        编程语言                          ← 标签单独一行，内容在下一行
        掌握 C++/Java/Python
    """
    items: list[Item] = []
    pending_label: str | None = None

    def add(label: str | None, value: str) -> None:
        items.append(
            Item(
                org=label,
                bullets=[Bullet(text=value, order_index=0)],
                order_index=len(items),
            )
        )

    for line in section.lines:
        text = BULLET_PREFIX.sub("", _clean(line.text))
        if not text:
            continue
        m = KV_SPLIT.search(text)
        if m and len(text[: m.start()].strip()) <= 10:
            label = _clean(text[: m.start()])
            value = _clean(text[m.end():])
            if value:
                add(label or None, value)
                pending_label = None
            else:
                pending_label = label
        elif pending_label:
            add(pending_label, text)
            pending_label = None
        elif len(text) <= 10 and not re.search(r"[，。；、,;]", text):
            pending_label = text          # 疑似标签，等下一行内容
        else:
            add(None, text)
    if pending_label:
        add(pending_label, "")
    return items


def extract_contact(lines: list[Line], doc: ResumeDoc) -> None:
    """从页首区域抓姓名、邮箱、电话、链接。"""
    for line in lines[:4]:
        text = line.text
        if not doc.email:
            m = EMAIL.search(text)
            if m:
                doc.email = m.group(0)
        if not doc.phone:
            m = PHONE.search(text.replace(" ", ""))
            if m:
                doc.phone = m.group(0)
        for m in URL.finditer(text):
            url = m.group(0)
            key = "github" if "github" in url else "link"
            doc.links.setdefault(key, url)
    if not doc.name and lines:
        # 姓名通常是页首字号最大的 2~4 个汉字；按 segment 取，别被
        # 「个人简历 张三」这种同行多段、或「⚫ 民族：汉」这种标签行带偏
        name_stop = {"个人简历", "简历", "求职简历", "个人信息", "联系方式", "基本信息"}
        best: tuple[float, str] | None = None
        for line in lines[:6]:
            for seg in line.segments:
                text = seg.text.strip()
                if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", text):
                    continue
                if text in name_stop:
                    continue
                if best is None or line.size > best[0]:
                    best = (line.size, text)
        if best:
            doc.name = best[1]


def extract(pages: list[PageText]) -> ResumeDoc:
    """把整份 PDF 抽成 ResumeDoc 草稿（不做 LLM 精修）。"""
    doc = ResumeDoc()
    all_lines = [ln for page in pages for ln in page.lines]
    if not all_lines:
        doc.warnings.append("PDF 中没有提取到文本层，可能是扫描件。")
        return doc

    page_width = pages[0].width
    lines = _merge_continuations(all_lines, page_width)
    extract_contact(lines, doc)

    # 切章节
    sections: list[RawSection] = []
    current: RawSection | None = None
    preamble: list[Line] = []
    for line in lines:
        type_ = classify_header(line)
        if type_ is not None:
            current = RawSection(type=type_, title=line.text.strip())
            sections.append(current)
            continue
        if current is None:
            preamble.append(line)
        else:
            current.lines.append(line)

    base_x = _left_margin([ln for s in sections for ln in s.lines])

    for raw in sections:
        # 每个章节单独算左边界：整篇取最小值会把「本身就有缩进」的章节
        # 里所有行都判成 bullet（实测：项目经历整体缩进 6pt 就全乱了）
        sec_base = _left_margin(raw.lines) if raw.lines else base_x
        items = _build_items(raw, sec_base)
        doc.sections.append(
            Section(type=raw.type, items=items, order_index=len(doc.sections))
        )

    if not doc.sections:
        doc.warnings.append("没识别出标准章节标题，已把全文放入「其他」。")

    # 联系方式行不应留在正文里
    if doc.sections and doc.sections[0].items:
        first = doc.sections[0].items[0]
        if first.bullets and EMAIL.search(first.bullets[0].text):
            first.bullets.pop(0)

    return doc
