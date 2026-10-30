"""对话式 agent：一条消息 → 意图 → 动作 → 自然语言回复。

控制流保持确定性（规则意图），LLM 只用于简历改写本身——
这样每一步都能复现、能 debug。
"""
from __future__ import annotations

import asyncio

from ..apply import open_job
from ..generate import engine
from ..rag.chunk import _split_jd_sections
from ..repo import generations as gen_repo
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

MAX_CANDIDATES = 30

# 每个意图回答后给出的「下一步」建议（点击即发送）
SUGGESTIONS: dict[str, list[str]] = {
    "search": ["定制第 1 个", "排除外包", "看第 2 个的要求"],
    "more": ["定制第 1 个", "看第 2 个的要求"],
    "exclude": ["定制第 1 个", "看第 2 个的要求"],
    "tailor": ["投第 1 个", "看第 2 个的要求"],
    "apply": ["看第 2 个的要求", "现在有什么"],
    "ask_detail": ["定制第 1 个", "投第 1 个"],
    "judge": ["定制第 1 个", "投第 1 个"],
    "status": ["定制第 1 个", "排除外包"],
    "chat": ["找北京的 AI 应用岗位", "定制是什么意思", "现在有什么"],
}


# ---------------------------------------------------------------- 入口
def _list_candidates(candidates: list[dict], limit: int = 20) -> list[str]:
    """把候选列表格式化成编号行。说「剩下 N 个」时一定要把 N 个列出来。"""
    lines = []
    for index, item in enumerate(candidates[:limit], start=1):
        lines.append(
            f"{index}. {item['title']}（{item.get('company') or '公司未填'}）"
            f"{item.get('location') or ''} {item.get('score')} 分"
        )
    if len(candidates) > limit:
        lines.append(f"…还有 {len(candidates) - limit} 个")
    return lines


def handle(conversation_id: int, text: str, resume_id: int) -> dict:
    state = conv.get_state(conversation_id)
    state.setdefault("candidates", [])
    state.setdefault("queue", [])
    state.setdefault("filters", {})
    state.setdefault("goal", {})

    intent = intent_mod.classify(text)
    if intent.name == "chat":
        fallback = asyncio.run(intent_mod.classify_llm(text))
        if fallback is not None:
            intent = fallback
    if intent.name == "search":
        reply = _do_search(state, text, resume_id, intent.params)
    elif intent.name == "more":
        reply = _do_more(state, resume_id, intent.params.get("count") or 5)
    elif intent.name == "exclude":
        reply = _do_exclude(state, intent.params.get("words") or "")
    elif intent.name == "tailor":
        reply = _do_tailor(state, intent.params.get("index"), resume_id, intent.params.get("text", ""))
    elif intent.name == "apply":
        reply = _do_apply(state, intent.params.get("index"), intent.params.get("text", ""))
    elif intent.name == "ask_detail":
        reply = _do_ask_detail(state, intent.params.get("index"), text)
    elif intent.name == "judge":
        reply = _do_judge(state, intent.params.get("index"), text, resume_id)
    elif intent.name == "advice":
        reply = _do_advice(resume_id)
    elif intent.name == "status":
        reply = _do_status(state)
    else:
        reply = (
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

    conv.set_state(conversation_id, state)
    return {"reply": reply, "intent": intent.name,
            "suggestions": SUGGESTIONS.get(intent.name, [])}


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
    state["goal"] = {
        "keyword": goal.keyword, "city": goal.city,
        "min_score": goal.min_score, "count": goal.count, "page": 1,
    }
    return _search_and_store(state, resume_id, goal)


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


def _search_and_store(state: dict, resume_id: int, goal, append: bool = False) -> str:
    doc = resume_repo.load_doc(resume_id)
    if doc is None:
        return "没找到简历，先去「简历」页导入一份。"

    jobs = search_51job(goal.keyword, city_code=code_for(goal.city), limit=200, pages=getattr(goal, "page", 1))
    seen = {c.get("source_id") for c in state.get("candidates", [])}
    excluded = state.get("filters", {}).get("exclude") or []

    fresh = []
    for job in jobs:
        if job.source_id and job.source_id in seen:
            continue
        if goal.city and goal.city not in (job.location or ""):
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

    if not fresh:
        if append:
            return "又翻了一页，没有新的岗位了。"
        state["candidates"] = []
        return f"没找到「{goal.keyword}」岗位（城市：{goal.city or '不限'}）。换个关键词试试。"

    combined = (state.get("candidates") or []) + fresh if append else fresh
    combined.sort(key=lambda c: -c["score"])
    state["candidates"] = combined[:MAX_CANDIDATES]

    threshold_note = f"≥{goal.min_score} 分" if goal.min_score else "按匹配分排序"
    lines = [
        f"{('又' if append else '')}找到 {len(fresh)} 个{'新的' if append else ''}岗位"
        f"（关键词「{goal.keyword}」· 城市「{goal.city or '不限'}」· {threshold_note}）："
    ]
    lines.extend(_list_candidates(state["candidates"]))
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


def _resolve_candidate(state: dict, index: int | None, text: str = ""):
    """解析「第 N 个 / 这个 / 它」。显式序号会记下来，供下次「这个」指代。"""
    import re as _re

    candidates = state.get("candidates") or []
    if not candidates:
        return None
    if index and 1 <= index <= len(candidates):
        state["last_index"] = index
        return candidates[index - 1]
    if _re.search(r"(这个|那个|它|刚才)", text or ""):
        last = state.get("last_index")
        if last and 1 <= last <= len(candidates):
            return candidates[last - 1]
    return candidates[0]


def _do_tailor(state: dict, index: int | None, resume_id: int, text: str = "") -> str:
    cand = _resolve_candidate(state, index, text)
    if cand is None:
        return "还没有候选岗位，先说「找 xxx 岗位」。"
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
    result = asyncio.run(engine.generate(doc, cand.get("description") or ""))
    db_run = gen_repo.get_or_create_run(resume_id, job_id)
    gen_repo.replace_items(db_run, result.proposals)

    queue = state.setdefault("queue", [])
    if not any(q.get("job_id") == job_id for q in queue):
        queue.append({
            "job_id": job_id, "title": cand["title"],
            "company": cand.get("company") or "", "score": cand.get("score"),
            "candidates": len(result.proposals), "passed": result.accepted_count,
        })
    return (
        f"已为「{cand['title']}」生成定制简历：{len(result.proposals)} 条候选，"
        f"{result.accepted_count} 条有实质改动。\n"
        f"去终审：[终审页](/jobs/{job_id}/generate?resume_id={resume_id})"
    )


def _do_apply(state: dict, index: int | None, text: str = "") -> str:
    cand = _resolve_candidate(state, index, text)
    if cand is None:
        return "还没有候选岗位，先说「找 xxx 岗位」。"
    ok = open_job(cand.get("url"))
    if not ok:
        return "启动浏览器失败，检查浏览器配置。"

    job = None
    if cand.get("source_id"):
        job = job_repo.find_by_dedupe_key(f"51job:{cand['source_id']}")
    apply_link = f"/jobs/{job['id']}/apply" if job else "/applications"
    return (
        f"已在你的登录态浏览器里打开「{cand['title']}」的岗位页。\n"
        f"请在窗口里完成投递（验证码/提交由你操作），完成后到"
        f"[投递页]({apply_link})点「已投递」记录。"
    )


def _do_ask_detail(state: dict, index: int | None, text: str) -> str:
    """回答「第 N 个的具体要求/公司/薪资」这类问题。"""
    import re as _re

    cand = _resolve_candidate(state, index, text)
    if cand is None:
        return "还没有候选岗位，先说「找 xxx 岗位」。"

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
        )

    description = cand.get("description") or ""
    if not description.strip():
        return f"「{cand['title']}」没抓到 JD 正文，只能看到列表信息。"

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
    return "\n".join(lines)


def _do_judge(state: dict, index: int | None, text: str, resume_id: int) -> str:
    """「这个岗位适合我吗」——LLM 判断适配度。"""
    cand = _resolve_candidate(state, index, text)
    if cand is None:
        return "还没有候选岗位，先说「找 xxx 岗位」。"
    doc = resume_repo.load_doc(resume_id)
    if doc is None:
        return "没找到简历。"

    position = 1
    for i, item in enumerate(state.get("candidates") or [], start=1):
        if item is cand or item.get("source_id") == cand.get("source_id"):
            position = i
            break

    try:
        result = asyncio.run(judge_mod.judge(doc, cand.get("description") or ""))
    except Exception as exc:  # noqa: BLE001 - 判断失败要能回话给用户
        detail = str(exc)[:120]
        return (
            f"判断服务这次没成功（{detail}）。\n"
            f"先看客观数据：匹配 {cand.get('score')} 分。\n"
            f"可以过一会儿再说「第 {position} 个适合我吗」重试。"
        )

    lines = [f"第 {position} 个「{cand['title']}」——{result['verdict']}（匹配 {cand.get('score')} 分）"]
    if result["summary"]:
        lines.append(result["summary"])
    if result["reasons"]:
        lines.append("· 优势：" + "；".join(result["reasons"]))
    if result["risks"]:
        lines.append("· 差距：" + "；".join(result["risks"]))
    if result["suggestion"]:
        lines.append("· 建议：" + result["suggestion"])
    return "\n".join(lines)


def _do_advice(resume_id: int) -> str:
    """「我的简历该补什么」——针对简历本身，不绑定岗位。"""
    doc = resume_repo.load_doc(resume_id)
    if doc is None:
        return "没找到简历，先去「简历」页导入一份。"
    from ..generate import suggest as suggest_mod

    try:
        items = asyncio.run(suggest_mod.general_review(doc))
    except Exception as exc:  # noqa: BLE001 - 建议失败要能回话
        return f"没能生成建议（{str(exc)[:80]}），过会儿再试。"
    if not items:
        return "这次没生成建议（模型调用失败），过会儿再说一次「我的简历需要补充什么」。"

    lines = ["针对你简历本身，可以补这些（只在你确实做过时补）："]
    for i, s in enumerate(items, 1):
        lines.append(f"{i}. **{s['title']}**")
        if s.get("why"):
            lines.append(f"   为什么：{s['why']}")
        if s.get("how"):
            lines.append(f"   怎么写：{s['how']}")
    lines.append("想看针对某个岗位的建议，先「找 xxx 岗位」，再「定制第 N 个」。")
    return "\n".join(lines)


def _do_status(state: dict) -> str:
    candidates = state.get("candidates") or []
    queue = state.get("queue") or []
    lines = [f"当前候选 {len(candidates)} 个，定制队列 {len(queue)} 个。"]
    if candidates:
        lines.extend(_list_candidates(candidates))
    if queue:
        lines.append("已定制：" + "、".join(q["title"] for q in queue))
    return "\n".join(lines)
