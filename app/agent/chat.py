"""对话式 agent：一条消息 → 意图 → 动作 → 自然语言回复。

控制流保持确定性（规则意图），LLM 只用于简历改写本身——
这样每一步都能复现、能 debug。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field

from .. import trace
from ..apply import open_job
from ..llm.client import LLMError
from ..rag.chunk import _split_jd_sections
from ..repo import jobs as job_repo
from ..repo import resumes as resume_repo
from ..search.cities import code_for
from ..search.fiftyone import search as search_51job
from ..search.model import infer_job_type
from ..search.runner import _score_job
from . import conversation as conv
from . import goal as goal_mod
from . import intent as intent_mod
from . import judge as judge_mod
from . import plan as plan_mod
from . import tools as tools_mod
from . import verify as verify_mod
from .tools import ToolResult

MAX_CANDIDATES = 30


@dataclass
class TurnBudget:
    """一轮对话的资源上限。

    单次 chat_with_tools 有 90s 超时、内部最多重试 3 次，MAX_STEPS=3，
    最坏能占住一个线程池线程十几分钟。用户关掉页面也没法取消，
    所以这里加总时长 / 总调用数的硬闸门。
    """

    max_seconds: float = 150.0
    max_llm_calls: int = 6
    max_tool_calls: int = 8
    started: float = field(default_factory=time.monotonic)
    llm_calls: int = 0
    tool_calls: int = 0

    def exhausted(self) -> bool:
        return (time.monotonic() - self.started > self.max_seconds
                or self.llm_calls >= self.max_llm_calls
                or self.tool_calls >= self.max_tool_calls)

# 每个意图回答后给出的「下一步」建议（点击即发送）
SUGGESTIONS: dict[str, list[str]] = {
    "search": ["定制第 1 个", "排除外包", "看第 2 个的要求"],
    "more": ["定制第 1 个", "看第 2 个的要求"],
    "exclude": ["定制第 1 个", "看第 2 个的要求"],
    "tailor": ["投第 1 个", "看第 2 个的要求"],
    "apply": ["看第 2 个的要求", "现在有什么"],
    "record": ["看第 2 个的要求", "现在有什么"],
    "ask_detail": ["定制第 1 个", "投第 1 个"],
    "judge": ["定制第 1 个", "投第 1 个"],
    "status": ["定制第 1 个", "排除外包"],
    "chat": ["找北京的 AI 应用岗位", "定制是什么意思", "现在有什么"],
}


# ---------------------------------------------------------------- 入口
# 只有「短、单动作、无连词」的精确指令走规则快路径，其余一律交给 agent。
# 规则一旦参与理解，就会在「3 个」「第二个和第三个」「第三个呢」这类表达上翻车。
_FAST_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:投|投递|申请)\s*(?:第\s*)?([一二三四五六七八九十\d]{1,3})\s*个?$"), "apply"),
    (re.compile(r"^(?:定制|优化|改)\s*(?:第\s*)?([一二三四五六七八九十\d]{1,3})\s*个?$"), "tailor"),
    (re.compile(r"^再(?:找|来)\s*(?:(\d{1,2}|几|些)\s*个)?$"), "more"),
    (re.compile(r"^(?:排除|不要)\s*(.{1,12})$"), "exclude"),
    (re.compile(r"^(?:投好了|投完了|已投递|刚投完|已经投了|投过了)\s*(?:第\s*)?"
                r"([一二三四五六七八九十\d]{1,3})?\s*个?$"), "record"),
    (re.compile(r"^(?:现在有什么|看看候选|有哪些|列出候选|候选列表)$"), "status"),
    (re.compile(r"^第?\s*([一二三四五六七八九十\d]{1,3})\s*个\s*(?:的)?\s*(?:要求|职责|薪资|公司|介绍)"), "ask_detail"),
]

_FAST_BLOCK = re.compile(r"(和|、|然后|顺便|同时|以及|再帮|之前|之后)")
# 「定制 1 和 2」「定制第 1、2 个」：明确的指令，直接执行
_FAST_MULTI = re.compile(
    r"^(?:定制|优化|改)\s*(?:第\s*)?([一二三四五六七八九十\d]{1,3})"
    r"\s*(?:和|、|,|，|&)\s*([一二三四五六七八九十\d]{1,3})\s*个?$")


def fast_intent(text: str) -> intent_mod.Intent | None:
    """精确指令 → 直接执行（0 次模型调用）。其余返回 None。"""
    t = (text or "").strip()
    if not t or len(t) > 16:
        return None
    multi = _FAST_MULTI.match(t)
    if multi:
        idxs = []
        for raw in multi.groups():
            idx = int(raw) if raw.isdigit() else intent_mod.CN_NUM.get(raw)
            if idx:
                idxs.append(idx)
        return intent_mod.Intent("tailor", {"indices": idxs, "text": t})
    if _FAST_BLOCK.search(t):
        return None
    for pattern, name in _FAST_PATTERNS:
        match = pattern.match(t)
        if not match:
            continue
        if name in ("apply", "tailor", "ask_detail", "record"):
            raw = match.group(1)
            idx = (int(raw) if raw.isdigit() else intent_mod.CN_NUM.get(raw)) if raw else None
            return intent_mod.Intent(
                name, {"index": idx, "indices": [idx] if idx else [], "text": t})
        if name == "more":
            count = match.group(1)
            return intent_mod.Intent(
                name, {"count": int(count) if count and count.isdigit() else None, "text": t})
        if name == "exclude":
            return intent_mod.Intent(name, {"words": match.group(1), "text": t})
        return intent_mod.Intent(name, {"text": t})
    return None


def _list_candidates(candidates: list[dict], limit: int = 20, note: bool = True) -> list[str]:
    """把候选列表格式化成编号行。"""
    lines = []
    for number, item in enumerate(candidates[:limit], start=1):
        lines.append(
            f"{number}. {item['title']}（{item.get('company') or '公司未填'}）"
            f"{item.get('location') or ''} {item.get('score')} 分"
        )
    if note and len(candidates) > limit:
        lines.append(f"…还有 {len(candidates) - limit} 个")
    return lines


def handle(conversation_id: int, text: str, resume_id: int) -> dict:
    """一条用户消息 → 结果。agent 优先，精确指令走快路径，模型不可用时退回规则。"""
    trace.new_turn()
    state = conv.get_state(conversation_id)
    state.setdefault("candidates", [])
    state.setdefault("queue", [])
    state.setdefault("filters", {})
    state.setdefault("goal", {})
    state["resume_id"] = resume_id
    state.setdefault("cid_seq", 0)
    state.setdefault("last_cid", None)
    state["performed"] = []
    _ensure_cids(state)

    intent = fast_intent(text)
    if intent is None:
        # 写操作必须由用户显式发起：先认「投好了」这类记录意图，别丢给模型
        guess = intent_mod.classify(text)
        if guess.name == "record":
            intent = guess
    if intent is not None:
        trace.record("intent", intent.name, args={"text": text[:160], "mode": "fast-path"})
        reply = _dispatch(intent, text, state, resume_id)
        used = intent.name
    else:
        trace.record("intent", "agent", args={"text": text[:160], "mode": "agent-loop"})
        result = _agent_turn(conversation_id, text, state, resume_id)
        if result is None:
            fallback = intent_mod.classify(text)
            trace.record("fallback", "agent", result={"intent": fallback.name})
            reply = _dispatch(fallback, text, state, resume_id)
            used = fallback.name
        else:
            reply, used = result

    if used in ("judge", "ask_detail", "tailor", "apply"):
        state["last_action"] = used
    state["last_user_text"] = text[:120]
    issues = verify_mod.check_reply(reply or "", state)
    if issues:
        trace.record("verify", used, args={"issues": issues},
                     result={"reply": (reply or "")[:200]})
    trace.record("result", used, result={"chars": len(reply or ""), "reply": (reply or "")[:200]})
    conv.set_state(conversation_id, state)
    return {"reply": reply, "intent": used,
            "suggestions": SUGGESTIONS.get(used, [])}


def _help_text() -> str:
    return (
        "我能帮你做这些：\n"
        "· **找岗位**：说「找北京的 AI 应用岗位」，可按城市/关键词搜\n"
        "· **再找几个**：翻下一页，看更多岗位\n"
        "· **排除**：说「排除外包」，筛掉不想要的\n"
        "· **问岗位**：说「第 2 个的要求是什么」「这个岗位薪资多少」\n"
        "· **定制简历**：针对某个岗位，把你简历里相关的经历改写得更贴合它的要求"
        "（只改说法、不编造，还要你终审确认）\n"
        "· **判断适合度**：说「第 2 个适合我吗」\n"
        "· **投递**：说「投第 1 个」，会在你登录的浏览器里打开岗位页\n"
        "直接说就行。"
    )


def _dispatch(intent, text: str, state: dict, resume_id: int) -> str:
    """把意图落到具体动作上（快路径与工具循环共用）。"""
    name = intent.name
    params = intent.params
    if name == "search":
        return _do_search(state, text, resume_id, params)
    if name == "more":
        return _do_more(state, resume_id, params.get("count") or 5)
    if name == "exclude":
        return _do_exclude(state, params.get("words") or "")
    if name == "tailor":
        idxs = params.get("indices") or ([params["index"]] if params.get("index") else [])
        return _do_tailor_multi(state, idxs, resume_id, params.get("text", ""))
    if name == "apply":
        return _do_apply(state, params.get("index"), params.get("text", ""))
    if name == "ask_detail":
        return _do_ask_detail_multi(
            state, params.get("indices") or ([params["index"]] if params.get("index") else []), text)
    if name == "judge":
        return _do_judge_multi(
            state, params.get("indices") or ([params["index"]] if params.get("index") else []), text, resume_id)
    if name == "record":
        return _do_record_application(state, params.get("index"), params.get("note") or "")
    if name == "advice":
        return _do_advice(resume_id)
    if name == "status":
        return _do_status(state)
    return _help_text()


# ---------------------------------------------------------------- 工具循环
def _context_block(state: dict, resume_id: int) -> str:
    """给模型的「当前状态」：候选、上次动作、简历摘要。"""
    lines = ["# 当前状态"]
    candidates = state.get("candidates") or []
    if candidates:
        lines.append(f"候选 {len(candidates)} 个（序号从 1 开始）：")
        for number, cand in enumerate(candidates[:10], start=1):
            lines.append(f"  {number}. {cand['title']}"
                         f"（{cand.get('company') or '公司未填'}）"
                         f"{cand.get('location') or ''} 匹配 {cand.get('score')} 分")
        if len(candidates) > 10:
            lines.append(f"  …还有 {len(candidates) - 10} 个")
    else:
        lines.append("候选：暂无（还没搜索过岗位）")
    if state.get("last_action"):
        lines.append(f"上一轮动作：{state['last_action']}")
    if state.get("last_user_text"):
        lines.append(f"上一轮用户请求：{state['last_user_text']}")
    doc = resume_repo.load_doc(resume_id)
    if doc is not None:
        lines.append("# 简历摘要")
        lines.append(plan_mod.resume_brief(doc)[:500])
    return "\n".join(lines)


def _history(conversation_id: int) -> list[dict]:
    """最近 3 轮对话，让「第三个呢」这类省略句有上下文。"""
    try:
        messages = conv.list_messages(conversation_id)[-6:]
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content[:300]})
    return out



def _mark_performed(state: dict, action: str, *cids: str) -> None:
    """有副作用的动作留痕，供 verify 判断「说的话有没有对应事实」。"""
    from datetime import datetime

    state.setdefault("performed", []).append({
        "action": action,
        "cids": [c for c in cids if c],
        "at": datetime.now().isoformat(timespec="seconds"),
    })


def _cand_brief(state: dict, cand: dict) -> dict:
    """给模型看的候选摘要（不含链接与文案）。"""
    return {
        "index": _index_of(state, cand),
        "cid": cand.get("cid"),
        "title": cand.get("title") or "",
        "company": cand.get("company") or "",
        "location": cand.get("location") or "",
        "score": cand.get("score"),
    }


def _search_data(state: dict, before: int, params: dict | None = None) -> dict:
    candidates = state.get("candidates") or []
    goal = state.get("goal") or {}
    params = params or {}
    return {
        "candidates": [_cand_brief(state, c) for c in candidates[:20]],
        "total": len(candidates),
        "added": max(0, len(candidates) - before),
        "keyword": goal.get("keyword") or params.get("keyword") or "",
        "city": goal.get("city") or params.get("city") or "",
        "note": state.get("last_search_note") or "",
    }


# ---------------------------------------------------------------- 工具处理器
# 每个处理器只负责「执行 + 组装 data」，schema / intent / 绑定关系都在 tools.py。
def _tool_search(args: dict, state: dict, resume_id: int, utterance: str) -> ToolResult:
    before = len(state.get("candidates") or [])
    params = {"keyword": args.get("keyword") or "", "city": args.get("city") or ""}
    if args.get("min_score"):
        params["min_score"] = int(args["min_score"])
    if args.get("count"):
        params["count"] = int(args["count"])
    text = _do_search(state, params["keyword"], resume_id, params)
    return ToolResult(True, "search", text, _search_data(state, before, params))


def _tool_more(args: dict, state: dict, resume_id: int, utterance: str) -> ToolResult:
    before = len(state.get("candidates") or [])
    text = _do_more(state, resume_id, int(args.get("count") or 5))
    return ToolResult(True, "more", text, _search_data(state, before))


def _tool_exclude(args: dict, state: dict, resume_id: int, utterance: str) -> ToolResult:
    words = args.get("words") or ""
    text = _do_exclude(state, words)
    candidates = state.get("candidates") or []
    return ToolResult(True, "exclude", text, {
        "excluded": [w for w in words.split() if w],
        "remaining": len(candidates),
        "candidates": [_cand_brief(state, c) for c in candidates[:20]],
    })


def _tool_detail(args: dict, state: dict, resume_id: int, utterance: str) -> ToolResult:
    idxs = [int(i) for i in (args.get("indices") or [args.get("index")]) if i]
    text, jobs = _ask_detail_many(state, idxs, utterance)
    return ToolResult(True, "ask_detail", text, {"jobs": jobs})


def _tool_judge(args: dict, state: dict, resume_id: int, utterance: str) -> ToolResult:
    idxs = [int(i) for i in (args.get("indices") or [args.get("index")]) if i]
    text, judgments = _judge_many(state, idxs, utterance, resume_id)
    return ToolResult(True, "judge", text, {"judgments": judgments})


def _tool_list(args: dict, state: dict, resume_id: int, utterance: str) -> ToolResult:
    candidates = state.get("candidates") or []
    return ToolResult(True, "status", _do_status(state), {
        "total": len(candidates),
        "candidates": [_cand_brief(state, c) for c in candidates[:20]],
    })


def _tool_advice(args: dict, state: dict, resume_id: int, utterance: str) -> ToolResult:
    text, suggestions = _advice_of(resume_id)
    return ToolResult(True, "advice", text, {"suggestions": suggestions})


def _tool_tailor(args: dict, state: dict, resume_id: int, utterance: str) -> ToolResult:
    idxs = [int(i) for i in (args.get("indices") or []) if i]
    if not idxs and args.get("index"):
        idxs = [int(args["index"])]
    candidates = state.get("candidates") or []
    valid = [i for i in idxs if 1 <= i <= len(candidates)]
    proposals = [
        {"index": i, "cid": candidates[i - 1].get("cid"),
         "title": candidates[i - 1].get("title") or ""}
        for i in valid
    ]
    command = ("定制" + "和".join(str(i) for i in valid)) if valid else ""
    return ToolResult(True, "tailor", _propose_tailor(state, idxs), {
        "pending": True,
        "proposals": proposals,
        "confirm_command": command,
    })


def _tool_apply(args: dict, state: dict, resume_id: int, utterance: str) -> ToolResult:
    idx = int(args.get("index") or 0) or None
    cand = _resolve_candidate(state, idx, utterance)
    text = _propose_apply(state, idx, utterance)
    data: dict = {}
    if cand is not None:
        number = _index_of(state, cand)
        data = {"pending": True, "index": number, "cid": cand.get("cid"),
                "title": cand.get("title") or "", "confirm_command": f"投 {number}"}
    return ToolResult(True, "apply", text, data)


_TOOL_HANDLERS: dict[str, tools_mod.Handler] = {
    "search_jobs": _tool_search,
    "more_jobs": _tool_more,
    "exclude_jobs": _tool_exclude,
    "ask_job_detail": _tool_detail,
    "judge_fit": _tool_judge,
    "list_candidates": _tool_list,
    "resume_advice": _tool_advice,
    "tailor_resume": _tool_tailor,
    "apply_job": _tool_apply,
}

# 声明与处理器在这里合流；少绑一个，validate() 会在启动时报错。
tools_mod.bind_all(_TOOL_HANDLERS)


def _run_tool(name: str, args: dict, state: dict, resume_id: int,
              utterance: str = "") -> ToolResult:
    """按注册表分发。text 给用户看，data 给模型看。"""
    tool = tools_mod.get(name)
    if tool is None or tool.handler is None:
        return ToolResult(False, "chat", f"（未知工具：{name}）", {"error": "unknown_tool"})
    try:
        return tool.handler(args, state, resume_id, utterance)
    except Exception as exc:  # noqa: BLE001 - 工具失败也要回话
        trace.record("error", name, args=args, result={"error": str(exc)})
        return ToolResult(False, tool.intent,
                          f"这个操作没成功（{type(exc).__name__}：{exc}）。",
                          {"error": f"{type(exc).__name__}: {exc}"})


def _propose_tailor(state: dict, indices: list[int]) -> str:
    """有副作用的操作只提议，不执行——等用户确认。多个岗位合并成一次确认。"""
    candidates = state.get("candidates") or []
    valid = [i for i in indices if 1 <= i <= len(candidates)]
    if not valid:
        return _clarify_candidate(state, indices[0] if indices else None)
    state["pending"] = {"kind": "tailor", "indices": valid,
                        "command": "定制" + "和".join(str(i) for i in valid)}
    if len(valid) == 1:
        number = valid[0]
        cand = candidates[number - 1]
        return (f"要针对第 {number} 个「{cand['title']}」定制简历吗？\n"
                f"回复「定制{number}」我就开始（约 30 秒，后台进行）。")
    titles = "、".join(f"第 {i} 个「{candidates[i - 1]['title']}」" for i in valid)
    command = "定制" + "和".join(str(i) for i in valid)
    return (f"要针对{titles}定制简历吗？一次确认即可。\n"
            f"回复「{command}」我就开始（约 30 秒，后台并行）。")


def _propose_apply(state: dict, index: int | None, text: str = "") -> str:
    cand = _resolve_candidate(state, index, text)
    if cand is None:
        return _clarify_candidate(state, index)
    number = _index_of(state, cand)
    state["pending"] = {"kind": "apply", "indices": [number], "command": f"投 {number}"}
    return (f"要投递第 {number} 个「{cand['title']}」（{cand.get('company') or ''}）吗？\n"
            f"回复「投 {number}」，我会在你登录的浏览器里打开投递页。")


def _agent_turn(conversation_id: int, text: str, state: dict,
                resume_id: int) -> tuple[str, str] | None:
    """跑工具循环。失败返回 None——上层会退回到规则。"""
    try:
        return asyncio.run(_agent_turn_async(conversation_id, text, state, resume_id))
    except LLMError as exc:
        # 模型不可用是可预期的，降级到规则；其它异常是缺陷，让它冒到全局兜底页
        trace.record("error", "agent_loop",
                     result={"kind": "llm_unavailable", "error": str(exc)[:300]})
        return None


async def _agent_turn_async(conversation_id: int, text: str, state: dict,
                            resume_id: int) -> tuple[str, str]:
    from ..llm import client
    from . import tools as tools_mod

    messages: list[dict] = [
        {"role": "system", "content": tools_mod.SYSTEM_PROMPT},
        {"role": "system", "content": _context_block(state, resume_id)},
        *_history(conversation_id),
        {"role": "user", "content": text},
    ]
    # 一轮对话共用一个连接池：否则 3 步 = 3 次 TCP + TLS 握手
    async with client.session() as http:
        return await _agent_loop(messages, text, state, resume_id, http)


async def _agent_loop(messages: list[dict], text: str, state: dict,
                      resume_id: int, http) -> tuple[str, str]:
    from ..llm import client
    from . import tools as tools_mod

    used: list[str] = []
    produced: list[str] = []
    budget = TurnBudget()

    for _ in range(tools_mod.MAX_STEPS):
        if budget.exhausted():
            break
        reply = await client.chat_with_tools(
            "agent_tools", messages, tools_mod.TOOLS, client=http)
        budget.llm_calls += 1
        calls = reply.tool_calls
        if not calls:
            content = (reply.content or "").strip()
            if content:
                if reply.finish_reason == "length":
                    trace.record("error", "truncated", result={"chars": len(content)})
                    content += "\n\n（回答被长度限制截断了，要完整版可以让我只说结论）"
                return content, (used[0] if used else "chat")
            break
        messages.append({"role": "assistant", "content": reply.content or "",
                         "tool_calls": calls})
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            # 工具内部会 asyncio.run（judge / 关键词规划），放到线程里跑避免嵌套事件循环
            budget.tool_calls += 1
            result = await asyncio.to_thread(
                _run_tool, name, args, state, resume_id, text)
            used.append(result.intent)
            produced.append(result.text)
            trace.record("tool", name, args=args,
                         result={"ok": result.ok, "chars": len(result.text)})
            messages.append({"role": "tool",
                             "tool_call_id": call.get("id") or f"call_{len(used)}",
                             "content": result.as_message()[:4000]})

    # 走到这里说明模型一直在调工具、没给出最终回答：把已有结果给用户并说明原因
    answer = "\n\n".join(produced) if produced else _help_text()
    answer += "\n\n（这一轮的处理时间/调用次数到上限了，先把已经做的结果给你）"
    return answer, (used[0] if used else "chat")


# ---------------------------------------------------------------- 动作
def _do_search(state: dict, text: str, resume_id: int, params: dict | None = None) -> str:
    goal = goal_mod.rule_parse(text)
    params = params or {}
    if params.get("keyword"):
        goal.keyword = params["keyword"]
    if params.get("city"):
        goal.city = params["city"]
    if params.get("min_score"):
        goal.min_score = int(params["min_score"])
    if params.get("count"):
        goal.count = max(1, min(20, int(params["count"])))
    note = ""
    # 「和我简历匹配」——关键词在简历里，让模型从简历推出来
    if goal_mod.wants_resume_match(text) and not (params or {}).get("keyword"):
        doc = resume_repo.load_doc(resume_id)
        keywords: list[str] = []
        if doc is not None:
            keywords = asyncio.run(plan_mod.search_keywords(doc)) or plan_mod.fallback_keywords(doc)
        if keywords:
            goal.keyword = keywords[0]
            note = f"（按你的简历，我搜的是「{goal.keyword}」）"

    if not (goal.keyword or "").strip():
        return "想找哪个方向的岗位？告诉我关键词就行，比如「AI 应用」「后端开发」「测试」。"
    state["goal"] = {
        "keyword": goal.keyword, "city": goal.city,
        "min_score": goal.min_score, "count": goal.count, "page": 1,
    }
    return _search_and_store(state, resume_id, goal, lead_note=note)


def _do_more(state: dict, resume_id: int, count: int) -> str:
    goal_data = state.get("goal") or {}
    if not goal_data:
        return "还没搜过岗位，先说一句「找 xxx 岗位」。"
    next_page = int(goal_data.get("page", 1)) + 1
    goal_data["page"] = next_page
    goal = goal_mod.AgentGoal(
        text="", keyword=goal_data.get("keyword", ""),
        city=goal_data.get("city", ""),
        min_score=int(goal_data.get("min_score", 0)), count=count,
        page=next_page,
    )
    return _search_and_store(state, resume_id, goal, append=True)


def _pick_fresh(jobs, goal, doc, seen: set, excluded: list[str], city: str) -> list[dict]:
    """过滤 + 打分，返回这一轮可用的新候选。"""
    fresh: list[dict] = []
    for job in jobs:
        if job.source_id and job.source_id in seen:
            continue
        if city and city not in (job.location or ""):
            continue
        if any(word and word in (job.title or "") for word in excluded):
            continue
        score = _score_job(job.as_dict(), doc)
        if goal.min_score and score < goal.min_score:
            continue
        fresh.append({
            "source_id": job.source_id, "title": job.title, "company": job.company,
            "location": job.location, "url": job.url, "description": job.description,
            "experience": job.experience, "salary": job.salary,
            "education": job.education, "score": round(score, 1),
        })
    return fresh


def _search_and_store(state: dict, resume_id: int, goal, append: bool = False,
                      lead_note: str = "") -> str:
    doc = resume_repo.load_doc(resume_id)
    if doc is None:
        return "没找到简历，先去「简历」页导入一份。"

    seen = {c.get("source_id") for c in state.get("candidates", [])}
    excluded = state.get("filters", {}).get("exclude") or []
    page = getattr(goal, "page", 1)

    # 搜不到就依次放宽：先去掉城市，再只保留关键词的第一个词。
    # 放宽后一定告诉用户放宽了什么，不能悄悄换条件。
    attempts: list[tuple[str, str]] = [(goal.keyword, goal.city)]
    if goal.city:
        attempts.append((goal.keyword, ""))
    words = (goal.keyword or "").split()
    if len(words) > 1:
        attempts.append((words[0], ""))

    fresh: list[dict] = []
    relax_note = ""
    for keyword, city in attempts:
        jobs = search_51job(keyword, city_code=code_for(city), limit=200, pages=page)
        fresh = _pick_fresh(jobs, goal, doc, seen, excluded, city)
        if fresh:
            if city != goal.city:
                relax_note = f"（「{goal.city}」没搜到，已放宽到全国）"
            elif keyword != goal.keyword:
                relax_note = f"（「{goal.keyword}」没搜到，已放宽到「{keyword}」）"
            break

    if not fresh:
        if append:
            return "又翻了一页，没有新的岗位了。"
        state["candidates"] = []
        return (f"没找到「{goal.keyword}」相关岗位（城市：{goal.city or '不限'}），"
                "放宽条件也没有结果。换个更宽的关键词试试，比如只写方向不写具体岗位名。")

    # 不变量：已经进列表的候选，位置永远不变；新候选只追加到末尾。
    # 否则用户上一句说的「第 2 个」下一句就指向别的岗位了。
    if append:
        existing = state.get("candidates") or []
        room = MAX_CANDIDATES - len(existing)
        if room <= 0:
            return (f"候选已经有 {MAX_CANDIDATES} 个了，先「排除」掉一些，"
                    "或者换个关键词重新搜。")
        fresh.sort(key=lambda c: -c["score"])
        added = fresh[:room]
        for item in added:
            item["cid"] = _new_cid(state)
        state["candidates"] = existing + added
        got = len(added)
    else:
        fresh.sort(key=lambda c: -c["score"])
        kept = fresh[:MAX_CANDIDATES]
        for item in kept:
            item["cid"] = _new_cid(state)
        state["candidates"] = kept
        got = len(kept)

    threshold_note = f"≥{goal.min_score} 分" if goal.min_score else "按匹配分排序"
    head = (f"{('又' if append else '')}找到 {got} 个{'新的' if append else ''}岗位"
            f"（关键词「{goal.keyword}」· 城市「{goal.city or '不限'}」· {threshold_note}）"
            f"{lead_note}{relax_note}：")
    lines = [head]
    # 用户说「找 3 个」就只列 3 个，其余存着，别一屏糊 20 条
    shown = min(goal.count, len(state["candidates"]))
    lines.extend(_list_candidates(state["candidates"], limit=shown, note=False))
    if len(state["candidates"]) > shown:
        lines.append(f"（还有 {len(state['candidates']) - shown} 个，说「再找几个」继续看）")
    state["last_search_note"] = f"{lead_note}{relax_note}"
    lines.append("要针对哪个岗位「定制简历」？说「定制第 N 个」或「定制 1」。")
    return "\n".join(lines)


def _do_exclude(state: dict, words: str) -> str:
    if not words:
        return "要排除什么？比如「排除外包」。"
    filters = state.setdefault("filters", {})
    current = filters.get("exclude") or []
    for word in words.split():
        if word and word not in current:
            current.append(word)
    filters["exclude"] = current

    kept = []
    for cand in state.get("candidates") or []:
        text = (cand.get("title") or "") + (cand.get("company") or "")
        if any(word in text for word in current):
            continue
        kept.append(cand)
    state["candidates"] = kept

    lines = [f"已排除含「{'、'.join(current)}」的岗位，剩下 {len(kept)} 个候选："]
    lines.extend(_list_candidates(kept))
    if kept:
        lines.append("要对哪个定制？说「定制第 N 个」。")
    return "\n".join(lines)


_PRONOUN = re.compile(r"(这个|那个|它|刚才)")


def _new_cid(state: dict) -> str:
    """候选的稳定标识。用会话内递增序号，不依赖列表位置。"""
    n = int(state.get("cid_seq") or 0) + 1
    state["cid_seq"] = n
    return f"c{n}"


def _ensure_cids(state: dict) -> None:
    """老会话里的候选没有 cid，进来先补上，保证「这个」能稳定指代。"""
    for cand in state.get("candidates") or []:
        if not cand.get("cid"):
            cand["cid"] = _new_cid(state)


def _index_of(state: dict, cand: dict | None) -> int:
    """候选在**当前**列表里的显示序号；找不到返回 1。"""
    if cand is None:
        return 1
    cid = cand.get("cid")
    for i, item in enumerate(state.get("candidates") or [], start=1):
        if (cid and item.get("cid") == cid) or item is cand:
            return i
    return 1


def _resolve_candidate(state: dict, index: int | None, text: str = ""):
    """解析「第 N 个 / 这个 / 它」。显式序号按 cid 记下来，供下次「这个」指代。"""
    candidates = state.get("candidates") or []
    if not candidates:
        return None
    if index is not None:
        if 1 <= index <= len(candidates):
            cand = candidates[index - 1]
            state["last_cid"] = cand.get("cid")
            return cand
        return None  # 显式序号越界 → 让调用方向用户澄清，别猜
    if _PRONOUN.search(text or ""):
        cid = state.get("last_cid")
        cand = next((c for c in candidates if cid and c.get("cid") == cid), None)
        if cand is not None:
            return cand
    return candidates[0]




def _clarify_candidate(state: dict, index: int | None) -> str:
    """没有候选、或序号越界时，明确告诉用户该怎么选。"""
    candidates = state.get("candidates") or []
    if not candidates:
        return "还没有候选岗位。先说一句「找北京的 AI 应用岗位」这类，我再帮你筛。"
    if index is not None and not (1 <= index <= len(candidates)):
        return (f"现在只有 {len(candidates)} 个候选，没有第 {index} 个。"
                f"你可以说「第 1 个」到「第 {len(candidates)} 个」。")
    return "没定位到具体岗位，可以说「第 2 个」或「这个」。"


def _do_tailor(state: dict, index: int | None, resume_id: int, text: str = "") -> str:
    cand = _resolve_candidate(state, index, text)
    if cand is None:
        return _clarify_candidate(state, index)
    doc = resume_repo.load_doc(resume_id)
    if doc is None:
        return "没找到简历。"

    job_id = job_repo.create_job(
        title=cand["title"], company=cand.get("company") or "",
        city=(cand.get("location") or "").split("·")[0] if cand.get("location") else "",
        url=cand.get("url") or "", platform="51job",
        job_type=infer_job_type(cand.get("experience") or ""),
        external_id=cand.get("source_id") or None,
        dedupe_key=f"51job:{cand['source_id']}" if cand.get("source_id") else None,
        description=cand.get("description") or "",
    )
    # 生成要跑 30-60 秒，放到后台线程，对话立刻返回，避免请求一直挂着
    from ..routes import generate as gen_route

    run_id = gen_route._start(job_id, resume_id)
    state.pop("pending", None)
    _mark_performed(state, "tailor", cand.get("cid") or "")

    queue = state.setdefault("queue", [])
    if not any(q.get("job_id") == job_id for q in queue):
        queue.append({
            "job_id": job_id, "title": cand["title"],
            "company": cand.get("company") or "", "score": cand.get("score"),
        })
    return (
        f"已为「{cand['title']}」提交定制任务，后台进行中（重复点不会重复跑）。\n"
        f"看进度：[生成过程](/jobs/{job_id}/generate?resume_id={resume_id}&run={run_id})\n"
        f"生成完会在[终审页](/jobs/{job_id}/generate?resume_id={resume_id})逐条列出改动，等你确认。"
    )


def _do_tailor_multi(state: dict, idxs: list[int], resume_id: int, text: str = "") -> str:
    if not idxs:
        return _do_tailor(state, None, resume_id, text)
    return "\n\n".join(_do_tailor(state, idx, resume_id, text) for idx in idxs[:3])


def _do_apply(state: dict, index: int | None, text: str = "") -> str:
    cand = _resolve_candidate(state, index, text)
    if cand is None:
        return _clarify_candidate(state, index)
    ok = open_job(cand.get("url"))
    if not ok:
        return "启动浏览器失败，检查浏览器配置。"

    state["last_apply_cid"] = cand.get("cid")
    state.pop("pending", None)
    _mark_performed(state, "apply_open", cand.get("cid") or "")
    return (
        f"已在你的登录态浏览器里打开「{cand['title']}」（{cand.get('company') or ''}）的岗位页。\n"
        f"请在窗口里完成投递（验证码/提交由你自己操作）。\n"
        f"**投完回来说一句「投好了」，我直接帮你记上**，不用自己去投递页点。"
    )


def _do_record_application(state: dict, index: int | None = None, note: str = "") -> str:
    """把「已经投了」这件事记进投递表——不需要用户去别的页面点。"""
    from ..repo import applications as app_repo

    candidates = state.get("candidates") or []
    if index is not None:
        cand = _resolve_candidate(state, index, "")
    else:
        cid = state.get("last_apply_cid")
        cand = next((c for c in candidates if cid and c.get("cid") == cid), None)
        if cand is None:
            return ("还没打开过投递页，我不确定你投的是哪一个。\n"
                    "先说「投第 N 个」打开岗位页，投完回来告诉我「投好了」。")
    if cand is None:
        return _clarify_candidate(state, index)

    resume_id = int(state.get("resume_id") or 0) or 1

    job = None
    if cand.get("source_id"):
        job = job_repo.find_by_dedupe_key(f"51job:{cand['source_id']}")
    if job is None:
        job_id = job_repo.create_job(
            title=cand["title"], company=cand.get("company") or "",
            city=(cand.get("location") or "").split("·")[0] if cand.get("location") else "",
            url=cand.get("url") or "", platform="51job",
            job_type=infer_job_type(cand.get("experience") or ""),
            external_id=cand.get("source_id") or None,
            dedupe_key=f"51job:{cand['source_id']}" if cand.get("source_id") else None,
            description=cand.get("description") or "",
        )
    else:
        job_id = job["id"]

    existing = app_repo.find_by_job(job_id)
    if existing is not None:
        app_repo.record_submit(existing["id"], note)
        app_id = existing["id"]
    else:
        app_id = app_repo.create_application(
            job_id=job_id, resume_id=resume_id, channel="51job", notes=note)
        app_repo.record_submit(app_id, note)

    _mark_performed(state, "record", cand.get("cid") or "")
    position = _index_of(state, cand)
    return (f"已记录：第 {position} 个「{cand['title']}」（{cand.get('company') or ''}）"
            f"标记为已投递。\n看全部投递：[投递记录](/applications)")


def _job_data(state: dict, cand: dict) -> dict:
    """给模型看的岗位数据。JD 正文是网页抓取原文，键名带 _untrusted 标记。"""
    description = cand.get("description") or ""
    sections = []
    for title, body in _split_jd_sections(description):
        body = (body or "").strip()
        if body:
            sections.append({"title": title or "岗位描述", "text": body[:500]})
    return {
        "index": _index_of(state, cand),
        "cid": cand.get("cid"),
        "title": cand["title"],
        "company": cand.get("company") or "",
        "salary": cand.get("salary") or "",
        "location": cand.get("location") or "",
        "experience": cand.get("experience") or "",
        "education": cand.get("education") or "",
        "score": cand.get("score"),
        "url": cand.get("url") or "",
        "sections_untrusted": sections,
    }


def _ask_detail_of(state: dict, index: int | None, text: str) -> tuple[str, dict | None]:
    """返回（给用户看的文本, 给模型看的岗位数据）。"""
    import re as _re

    cand = _resolve_candidate(state, index, text)
    if cand is None:
        return _clarify_candidate(state, index), None

    data = _job_data(state, cand)
    asks_attr = _re.search(r"(公司|薪资|工资|地点|在哪|经验|学历|匹配)", text)
    asks_jd = _re.search(r"(要求|需求|职责|干什么|做什么|详情|介绍|内容|是什么|怎么样)", text)

    if asks_attr and not asks_jd:
        return (
            f"「{cand['title']}」\n"
            f"公司：{cand.get('company') or '未填'}\n"
            f"地点：{cand.get('location') or '未填'}\n"
            f"薪资：{cand.get('salary') or '未填'}\n"
            f"经验：{cand.get('experience') or '未填'}\n"
            f"学历：{cand.get('education') or '未填'}\n"
            f"匹配：{cand.get('score')} 分"
        ), data

    description = cand.get("description") or ""
    if not description.strip():
        return f"「{cand['title']}」没抓到 JD 正文，只能看到列表信息。", data

    lines = [f"「{cand['title']}」（{cand.get('company') or ''}·{cand.get('location') or ''}）"]
    for title, body in _split_jd_sections(description):
        body = (body or "").strip()
        if not body:
            continue
        lines.append(f"【{title or '岗位描述'}】")
        lines.append(body[:500])
    if len(lines) == 1:
        lines.append(description[:500])
    lines.append(f"来源：[岗位详情]({cand.get('url') or '#'})")
    return "\n".join(lines), data


def _do_ask_detail(state: dict, index: int | None, text: str) -> str:
    return _ask_detail_of(state, index, text)[0]



def _judge_many(state: dict, idxs: list[int], text: str,
                resume_id: int) -> tuple[str, list[dict]]:
    """一次问多个岗位（「第二个和第三个适合我吗」）。"""
    if not idxs:
        one_text, one_data = _judge_of(state, None, text, resume_id)
        return one_text, ([one_data] if one_data else [])
    parts: list[str] = []
    data: list[dict] = []
    for idx in idxs[:3]:
        one_text, one_data = _judge_of(state, idx, text, resume_id)
        parts.append(one_text)
        if one_data:
            data.append(one_data)
    return "\n\n".join(parts), data


def _do_judge_multi(state: dict, idxs: list[int], text: str, resume_id: int) -> str:
    return _judge_many(state, idxs, text, resume_id)[0]


def _ask_detail_many(state: dict, idxs: list[int], text: str) -> tuple[str, list[dict]]:
    if not idxs:
        one_text, one_data = _ask_detail_of(state, None, text)
        return one_text, ([one_data] if one_data else [])
    parts: list[str] = []
    data: list[dict] = []
    for idx in idxs[:3]:
        one_text, one_data = _ask_detail_of(state, idx, text)
        parts.append(one_text)
        if one_data:
            data.append(one_data)
    return "\n\n".join(parts), data


def _do_ask_detail_multi(state: dict, idxs: list[int], text: str) -> str:
    return _ask_detail_many(state, idxs, text)[0]


def _judge_of(state: dict, index: int | None, text: str,
              resume_id: int) -> tuple[str, dict | None]:
    """返回（给用户看的文本, 判断结果数据）。"""
    cand = _resolve_candidate(state, index, text)
    if cand is None:
        return _clarify_candidate(state, index), None
    doc = resume_repo.load_doc(resume_id)
    if doc is None:
        return "没找到简历。", None

    position = _index_of(state, cand)

    try:
        result = asyncio.run(judge_mod.judge(doc, cand.get("description") or ""))
    except Exception as exc:  # noqa: BLE001 - 判断失败要能回话给用户
        detail = str(exc)[:120]
        return (
            f"判断服务这次没成功（{detail}）。\n"
            f"先看客观数据：匹配 {cand.get('score')} 分。\n"
            f"可以过一会儿再说「第 {position} 个适合我吗」重试。"
        ), None

    lines = [f"第 {position} 个「{cand['title']}」——{result['verdict']}（匹配 {cand.get('score')} 分）"]
    if result["summary"]:
        lines.append(result["summary"])
    if result["reasons"]:
        lines.append("· 优势：" + "；".join(result["reasons"]))
    if result["risks"]:
        lines.append("· 差距：" + "；".join(result["risks"]))
    if result["suggestion"]:
        lines.append("· 建议：" + result["suggestion"])
    data = {
        "index": position,
        "cid": cand.get("cid"),
        "title": cand["title"],
        "verdict": result["verdict"],
        "summary": result["summary"],
        "reasons": result["reasons"],
        "risks": result["risks"],
        "suggestion": result["suggestion"],
        "score": cand.get("score"),
    }
    return "\n".join(lines), data


def _do_judge(state: dict, index: int | None, text: str, resume_id: int) -> str:
    return _judge_of(state, index, text, resume_id)[0]


def _advice_of(resume_id: int) -> tuple[str, list[dict]]:
    """返回（给用户看的文本, 建议列表）。"""
    doc = resume_repo.load_doc(resume_id)
    if doc is None:
        return "没找到简历，先去「简历」页导入一份。", []
    from ..generate import suggest as suggest_mod

    try:
        items = asyncio.run(suggest_mod.general_review(doc))
    except Exception as exc:  # noqa: BLE001 - 建议失败要能回话
        return f"没能生成建议（{str(exc)[:80]}），过会儿再试。", []
    if not items:
        return "这次没生成建议（模型调用失败），过会儿再说一次「我的简历需要补充什么」。", []

    lines = ["针对你简历本身，可以补这些（只在你确实做过时补）："]
    for i, s in enumerate(items, 1):
        lines.append(f"{i}. **{s['title']}**")
        if s.get("why"):
            lines.append(f"   为什么：{s['why']}")
        if s.get("how"):
            lines.append(f"   怎么写：{s['how']}")
    lines.append("想看针对某个岗位的建议，先「找 xxx 岗位」，再「定制第 N 个」。")
    return "\n".join(lines), [
        {"title": s["title"], "why": s.get("why") or "", "how": s.get("how") or ""}
        for s in items
    ]


def _do_advice(resume_id: int) -> str:
    return _advice_of(resume_id)[0]


def _do_status(state: dict) -> str:
    candidates = state.get("candidates") or []
    queue = state.get("queue") or []
    lines = [f"当前候选 {len(candidates)} 个，定制队列 {len(queue)} 个。"]
    if candidates:
        lines.extend(_list_candidates(candidates))
    if queue:
        lines.append("已定制：" + "、".join(q["title"] for q in queue))
    return "\n".join(lines)
