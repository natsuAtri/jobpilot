"""规则意图识别：把一条用户消息判断成要做什么。

意图优先级：再找 > 排除 > 定制 > 投递 > 查询 > 搜索 > 闲聊。
认不出的走 chat，由上层决定用 LLM 还是固定回复。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_INDEX = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*个")


def _index(text: str) -> int | None:
    match = _INDEX.search(text)
    if match:
        raw = match.group(1)
        return int(raw) if raw.isdigit() else CN_NUM.get(raw)
    match = re.search(r"^\s*(\d+)\s*$", text)
    return int(match.group(1)) if match else None



_INDEX_ALL = re.compile(r"第\s*([一二三四五六七八九十\d]{1,3})\s*个")
_ELLIPSIS = re.compile(r"^\s*(?:那|那么)?\s*第?\s*([一二三四五六七八九十\d]{1,3})\s*个?\s*(?:呢|怎么样|如何)?\s*[？?]?\s*$")


def indices(text: str) -> list[int]:
    """一句话里提到的所有序号（「第二个和第三个」→ [2, 3]）。"""
    out: list[int] = []
    for raw in _INDEX_ALL.findall(text or ""):
        idx = int(raw) if raw.isdigit() else CN_NUM.get(raw)
        if idx and idx not in out:
            out.append(idx)
    return out


def ellipsis_index(text: str) -> int | None:
    """「第三个呢」这种省略句，返回它指向的序号。"""
    match = _ELLIPSIS.match(text or "")
    if not match:
        return None
    raw = match.group(1)
    return int(raw) if raw.isdigit() else CN_NUM.get(raw)


@dataclass
class Intent:
    name: str
    params: dict = field(default_factory=dict)


def classify(text: str) -> Intent:
    t = (text or "").strip()
    if not t:
        return Intent("chat", {"text": t})

    if re.search(r"(再找|再来|多找|还要|再来几个)", t):
        count = re.search(r"(\d+)\s*个", t)
        return Intent("more", {"count": int(count.group(1)) if count else 5})

    if re.search(r"(排除|不要|去掉|筛掉|过滤掉|剔除)", t):
        match = re.search(r"(?:排除|不要|去掉|筛掉|过滤掉|剔除)\s*(.+)", t)
        return Intent("exclude", {"words": match.group(1).strip() if match else ""})

    # 问「简历本身该怎么改」（不针对某个岗位）——先于 tailor，避免被当成改简历动作
    if re.search(r"简历", t) and re.search(
        r"(补充|建议|优化|改进|提升|怎么样|评价|缺什么|缺啥|问题|不够)", t
    ) and not re.search(r"(定制|生成简历|第\s*[一二三四五六七八九十\d]+\s*个)", t):
        return Intent("advice", {"text": t})

    # 问「这是什么意思」属于求助，不要当成动作（「定制是什么意思」）
    if not re.search(r"(第\s*[一二三四五六七八九十\d]+\s*个|这个|那个|它)", t) and re.search(
        r"(什么意思|是什么意思|怎么用|怎么操作|有什么用|能做什么)", t
    ):
        return Intent("chat", {"text": t})

    # 针对某个岗位优化/改写简历。
    # 注意：「我打算投第三个，帮我把简历优化一下」是改写，不是投递——
    # 「优化/润色/改改」这类动词出现时，优先按 tailor 处理。
    if re.search(r"(定制|改简历|生成简历|适配一下|优化|润色|打磨)", t) or (
        re.search(r"简历", t) and re.search(r"(修改|改一改|改改|改下|改一下|调整)", t)
    ):
        index = _index(t)
        if index is None:
            bare = re.search(r"(?:定制|优化|润色|打磨|改)\s*(\d{1,2})\s*(?:个|号)?", t)
            if bare:
                index = int(bare.group(1))
        return Intent("tailor", {"index": index, "text": t})

    # 判断适配度：「这个岗位适合我吗」「投这个有戏吗」——判断优先于投递动作
    if re.search(r"(适合|合适|匹配吗|能不能投|够不够|胜算|竞争力|有戏|分析)", t) and re.search(
        r"(第\s*[一二三四五六七八九十\d]+\s*个|这个|那个|它)", t
    ):
        return Intent("judge", {"index": _index(t), "indices": indices(t), "text": t})

    # 「投第 2 个 / 投这个」是投递；「投 3 个」是数量，算搜索参数
    if re.search(r"(投递|投)\s*(第\s*[一二三四五六七八九十\d]+\s*个|这个|那个|它)", t):
        return Intent("apply", {"index": _index(t), "text": t})

    # 「投好了 / 已投递」——记录投递，不是打开投递页
    if re.search(r"(投好了|投完了|已投递|刚投完|投过了|已经投了|投好啦)", t):
        return Intent("record", {"index": _index(t), "text": t})

    # 问某个候选的详情/要求/公司/薪资
    if re.search(r"(第\s*[一二三四五六七八九十\d]+\s*个|这个|那个|它|第一个)", t) and re.search(
        r"(要求|需求|职责|干什么|做什么|详情|介绍|公司|薪资|工资|地点|在哪|经验|学历|怎么样|是什么|内容)",
        t,
    ):
        return Intent("ask_detail", {"index": _index(t), "indices": indices(t), "text": t})

    if re.search(r"(现在|当前|有什么|队列|状态|看看|列表|进度)", t):
        return Intent("status", {})

    if re.search(r"(找|搜|搜索|帮我找|有没有|岗位|职位)", t):
        return Intent("search", {"text": t})

    return Intent("chat", {"text": t})


async def classify_llm(text: str) -> Intent | None:
    """规则认不出时，让模型兜底分类。失败返回 None（保持 chat）。"""
    from ..llm import client

    system = """把用户的一句话归到下面某个意图，并提取参数。只输出 JSON：
{"intent": "search|more|exclude|tailor|apply|ask_detail|judge|status|advice|chat",
 "keyword": "搜索关键词（search 用）",
 "city": "城市（search 用）",
 "min_score": 70,
 "count": 5,
 "index": 1,
 "words": "要排除的词"}

意图说明：
- search 找岗位；more 再找几个；exclude 排除某类；tailor 定制简历；
- apply 投递指定岗位；ask_detail 问某个岗位的要求/公司/薪资；
- judge 判断是否适合；status 查看当前候选；
- advice 问简历本身该怎么改（不针对具体岗位）；chat 其他闲聊。
参数缺失就留空。"""
    try:
        data = await client.complete_json(
            "intent_route",
            [{"role": "system", "content": system}, {"role": "user", "content": text}],
        )
    except Exception:  # noqa: BLE001 - 兜底失败就用原规则结果
        return None

    name = (data.get("intent") or "").strip()
    valid = {"search", "more", "exclude", "tailor", "apply", "ask_detail", "judge",
             "status", "advice"}
    if name not in valid:
        return None
    params: dict = {"text": text}
    for key, cast in (("keyword", str), ("city", str), ("words", str)):
        if data.get(key):
            params[key] = cast(data[key]).strip()
    for key in ("min_score", "count", "index"):
        if data.get(key):
            try:
                params[key] = int(data[key])
            except (TypeError, ValueError):
                pass
    return Intent(name, params)
