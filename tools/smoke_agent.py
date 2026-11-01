"""Agent 层冒烟测试：意图路由 / 复合请求 / 工具循环 / trace。

    python tools/smoke_agent.py

不依赖真实模型：工具循环用假的 chat_with_tools 驱动。
需要一份能解析的 PDF 或已有简历；没有就用临时合成简历。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

# 用独立数据目录，绝不碰用户的库
_TMP = Path(tempfile.mkdtemp(prefix="jp-agent-smoke-"))
os.environ["RA_DATA_DIR"] = str(_TMP)
os.environ["RA_LLM_STUB"] = "1"

from app.agent import chat, intent  # noqa: E402
from app.agent import conversation as conv
from app.db import get_conn, migrate  # noqa: E402
from app.llm import client  # noqa: E402
from app.models import Bullet, Item, ResumeDoc, Section  # noqa: E402
from app.repo import resumes as resume_repo  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def setup() -> int:
    migrate()
    doc = ResumeDoc(name="张三")
    doc.sections = [
        Section(type="project", items=[Item(org="项目A", bullets=[Bullet(text="做了点事")])]),
    ]
    return resume_repo.create_resume(doc, title="冒烟测试")


def test_intent() -> None:
    cases = [
        ("找北京的 AI 应用岗位", "search"),
        ("定制第 2 个", "tailor"),
        ("我打算投第三个 帮我把简历优化一下", "tailor"),
        ("投第 1 个", "apply"),
        ("我的简历需要补充什么", "advice"),
        ("现在有什么", "status"),
    ]
    for text, expected in cases:
        got = intent.classify(text).name
        check(f"意图：{text}", got == expected, f"期望 {expected} 得到 {got}")


def test_fast_path_rules() -> None:
    """只有短、单动作、无连词的指令才走快路径。"""
    cases = [
        ("投第 1 个", "apply"),
        ("投 3", "apply"),
        ("定制第 2 个", "tailor"),
        ("再找几个", "more"),
        ("排除外包", "exclude"),
        ("现在有什么", "status"),
        ("第 2 个的要求", "ask_detail"),
    ]
    for text, expected in cases:
        got = chat.fast_intent(text)
        check(f"快路径：{text}", got is not None and got.name == expected,
              str(got.name if got else None))

    complex_cases = [
        "找 3 个和我简历匹配的岗位",
        "帮我分析一下第二个和第三个岗位是否适合我",
        "第三个呢",
        "投第三个之前先帮我改下简历",
        "找几个岗位，然后告诉我哪个最值得投",
    ]
    for text in complex_cases:
        check(f"不进快路径：{text}", chat.fast_intent(text) is None, str(chat.fast_intent(text)))


def test_loop(resume_id: int) -> None:
    cid = conv.get_active()

    def fake_search(state, text, resume_id_, params=None):
        state["candidates"] = [
            {"index": 1, "source_id": "1", "title": "AI 应用工程师", "company": "字节跳动",
             "location": "北京", "score": 69.8, "url": "u", "description": "要求 RAG"},
        ]
        return "找到 1 个岗位"

    original_search, original_judge = chat._do_search, chat._do_judge
    chat._do_search = fake_search
    chat._do_judge = lambda state, idx, text, rid: f"第 {idx} 个：推荐"
    try:
        calls = {"n": 0}

        async def fake_chat(task, messages, tools, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return client.ToolReply(content="", tool_calls=[{
                    "id": "c1", "type": "function",
                    "function": {"name": "search_jobs", "arguments": '{"keyword":"AI"}'}}])
            if calls["n"] == 2:
                return client.ToolReply(content="", tool_calls=[{
                    "id": "c2", "type": "function",
                    "function": {"name": "judge_fit", "arguments": '{"index":1}'}}])
            return client.ToolReply(content="搜到 1 个，第 1 个更值得投。", tool_calls=[])

        original_chat = client.chat_with_tools
        client.chat_with_tools = fake_chat
        try:
            out = chat.handle(cid, "找几个岗位，然后告诉我哪个最值得投", resume_id)
        finally:
            client.chat_with_tools = original_chat

        check("工具循环返回最终文本", "更值得投" in out["reply"], out["reply"][:60])
        check("工具循环调用了两次模型以上", calls["n"] >= 3, str(calls["n"]))
        check("工具循环的意图归到首个工具", out["intent"] == "search", out["intent"])
    finally:
        chat._do_search, chat._do_judge = original_search, original_judge


def test_fast_path(resume_id: int) -> None:
    """规则命中时不应该调用模型。"""
    cid = conv.get_active()
    calls = {"n": 0}

    async def boom(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("规则命中时不应调用模型")

    original = client.chat_with_tools
    client.chat_with_tools = boom
    try:
        chat.handle(cid, "现在有什么", resume_id)
    finally:
        client.chat_with_tools = original
    check("规则命中走快路径（0 次模型调用）", calls["n"] == 0, str(calls["n"]))



def test_clarify(resume_id: int) -> None:
    """没有候选、序号越界、关键词为空时，要问清楚而不是瞎猜。"""
    state = {"candidates": [
        {"index": i, "source_id": str(i), "title": f"岗位{i}", "company": "X",
         "location": "北京", "score": 60, "url": "u", "description": "d"} for i in (1, 2, 3)
    ], "queue": [], "filters": {}, "goal": {}}

    out = chat._do_ask_detail(state, 9, "")
    check("序号越界会澄清", "只有 3 个候选" in out, out[:40])

    out = chat._do_ask_detail(state, 2, "")
    check("合法序号正常返回", "岗位2" in out, out[:40])

    empty = {"candidates": [], "queue": [], "filters": {}, "goal": {}}
    out = chat._do_search(empty, "", resume_id, {})
    check("空关键词会反问方向", "哪个方向" in out, out[:40])

    out = chat._do_apply(empty, 1, "")
    check("无候选投递会引导", "还没有候选岗位" in out, out[:40])



def test_relax(resume_id: int) -> None:
    """城市搜不到时自动放宽到全国，并且明确告诉用户放宽了什么。"""
    from app.agent import goal as goal_mod

    class FakeJob:
        def __init__(self, i: int, loc: str):
            self.source_id, self.title, self.company = str(i), f"AI 工程师{i}", "X"
            self.location, self.url, self.description = loc, "u", "要求 Python"
            self.experience, self.salary, self.education = "1-3年", "20-30K", "本科"

        def as_dict(self):
            return {"source_id": self.source_id, "title": self.title, "company": self.company,
                    "location": self.location, "url": self.url, "description": self.description,
                    "experience": self.experience, "education": self.education}

    calls: list[tuple] = []

    def fake_search(keyword, city_code=None, limit=200, pages=1):
        calls.append((keyword, city_code))
        return [] if city_code == "010000" else [FakeJob(1, "上海")]

    original = chat.search_51job
    chat.search_51job = fake_search
    try:
        goal = goal_mod.rule_parse("找北京的 AI 工程师")
        out = chat._search_and_store(
            {"candidates": [], "queue": [], "filters": {}, "goal": {}}, resume_id, goal)
    finally:
        chat.search_51job = original

    check("城市没结果会重试全国", len(calls) == 2, str(calls))
    check("放宽后告知用户", "放宽到全国" in out, out[:60])
    check("放宽后确实有结果", "找到 1 个岗位" in out, out[:60])



def test_checkpoint(resume_id: int) -> None:
    """生成中断后重跑，已完成的步骤应复用，不能重复烧 token。"""

    from app.generate import engine
    from app.llm.client import LLMError
    from app.repo import generations as gen_repo
    from app.repo import jobs as job_repo

    doc = resume_repo.load_doc(resume_id)
    job_id = job_repo.create_job(title="AI 工程师", description="要求 Python 与检索经验",
                                 dedupe_key="smoke-ckpt")
    run_id = gen_repo.get_or_create_run(resume_id, job_id)

    calls: list[str] = []

    def make_client(fail_at: int | None = None):
        async def fake(task, messages, **kw):
            calls.append(task)
            if fail_at and len(calls) == fail_at:
                raise LLMError("模拟中断")
            if task == "resume_strategy":
                return {"focus": [{"source_id": "project:0:0", "why": "贴 JD"}], "missing": []}
            if task == "resume_draft":
                # 必须有实质改写，评审才会被调用（第 3 次调用）
                return {"rewrites": [{"source_id": "project:0:0",
                                      "text": "基于 Python 实现检索服务"}]}
            if task == "resume_critique":
                return {"reviews": [], "missed": []}
            if task == "resume_gaps_suggest":
                return {"suggestions": []}
            return {}
        return fake

    original = client.complete_json
    # 第一次：第三步（评审）失败
    client.complete_json = make_client(fail_at=3)
    try:
        asyncio.run(engine.generate(doc, "要求 Python 与检索经验",
                                    on_step=lambda s, p: gen_repo.save_step(run_id, s, p)))
        crashed = False
    except Exception:
        crashed = True
    finally:
        client.complete_json = original

    check("中断确实抛出", crashed)
    saved = gen_repo.load_steps(run_id)
    check("中断前的步骤已落库", {"strategy", "draft"} <= set(saved), str(sorted(saved)))

    # 第二次：复用检查点
    calls.clear()
    client.complete_json = make_client()
    try:
        asyncio.run(engine.generate(doc, "要求 Python 与检索经验",
                                    cache=saved,
                                    on_step=lambda s, p: gen_repo.save_step(run_id, s, p)))
    finally:
        client.complete_json = original

    check("复用时不再调策略", "resume_strategy" not in calls, str(calls))
    check("复用时不再调起草", "resume_draft" not in calls, str(calls))



def test_verify(resume_id: int) -> None:
    """回复里的分数/序号必须和状态对得上。"""
    from app.agent import verify

    state = {"candidates": [{"score": 69.8}, {"score": 66.4}]}
    check("正确引用不报错", verify.check_reply("第 1 个 69.8 分，第 2 个 66.4 分", state) == [])
    check("编造分数会被抓", bool(verify.check_reply("第 1 个 88.8 分", state)))
    check("越界序号会被抓", bool(verify.check_reply("第 5 个也不错", state)))
    check("0 分是允许的", verify.check_reply("匹配 0 分说明不相关", state) == [])
    check("声称已记录但没动作会报",
          bool(verify.check_reply("已记录第 1 个", {"performed": []})))
    check("有对应动作就不报",
          verify.check_reply("已记录第 1 个", {"performed": [{"action": "record"}]}) == [])
    check("声称已打开但没动作会报",
          bool(verify.check_reply("已在你的登录态浏览器里打开岗位页", {"performed": []})))

    # 集成：工具返回了不一致的内容，trace 里应出现 verify 记录
    cid = conv.get_active()
    original = chat._do_status
    chat._do_status = lambda st: "第 1 个 99.9 分，非常合适"
    try:
        chat.handle(cid, "现在有什么", resume_id)
    finally:
        chat._do_status = original
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT kind FROM agent_trace")]
    check("不一致会写 verify 记录", "verify" in {r["kind"] for r in rows},
          str([r["kind"] for r in rows]))



def test_multi_index(resume_id: int) -> None:
    """「第二个和第三个适合我吗」要判断两个；「第三个呢」要继承上一步动作。"""
    cid = conv.get_active()
    cands = [{"index": i, "source_id": str(i), "title": f"岗位{i}", "company": "X",
              "location": "北京", "score": 60, "url": "u", "description": "d"} for i in (1, 2, 3)]
    conv.set_state(cid, {"candidates": cands, "queue": [], "filters": {}, "goal": {}})

    state = {"candidates": cands, "queue": [], "filters": {}, "goal": {}}

    calls: list[str] = []

    async def judge_stub(doc, jd):
        calls.append(jd)
        return {"verdict": "推荐", "summary": f"第{len(calls)}次", "reasons": [], "risks": [],
                "suggestion": ""}

    original = chat.judge_mod.judge
    chat.judge_mod.judge = judge_stub
    try:
        out = chat._do_judge_multi(state, [2, 3], "", resume_id)
        check("两个序号都会被判断", len(calls) == 2, str(len(calls)))
        check("两条结果都返回", out.count("第") >= 2, out[:60])

        calls.clear()
        out = chat._do_judge_multi(state, [3], "", resume_id)
        check("指定序号只判断一个", len(calls) == 1, str(len(calls)))
        check("用的是第 3 个", "第 3 个" in out, out[:40])
    finally:
        chat.judge_mod.judge = original


def test_resume_search(resume_id: int) -> None:
    """「和我简历匹配」要用简历推导的关键词，并且按用户说的数量展示。"""
    cid = conv.get_active()
    conv.set_state(cid, {"candidates": [], "queue": [], "filters": {}, "goal": {}})

    async def fake_keywords(doc, limit=3):
        return ["算法工程师"]

    class FakeJob:
        def __init__(self, i: int):
            self.source_id, self.title, self.company = str(i), f"算法工程师{i}", "X"
            self.location, self.url, self.description = "北京", "u", "要求 Python"
            self.experience, self.salary, self.education = "1-3年", "20-30K", "本科"

        def as_dict(self):
            return {"source_id": self.source_id, "title": self.title, "company": self.company,
                    "location": self.location, "url": self.url, "description": self.description,
                    "experience": self.experience, "education": self.education}

    original_plan, original_search = chat.plan_mod.search_keywords, chat.search_51job
    chat.plan_mod.search_keywords = fake_keywords
    chat.search_51job = lambda *a, **k: [FakeJob(i) for i in range(1, 6)]
    try:
        state = {"candidates": [], "queue": [], "filters": {}, "goal": {}}
        reply = chat._do_search(state, "找 3 个和我简历匹配的岗位", resume_id, {})
    finally:
        chat.plan_mod.search_keywords = original_plan
        chat.search_51job = original_search

    check("关键词来自简历而不是原句", "算法工程师" in reply and "我简历" not in reply, reply[:60])
    listed = [ln for ln in reply.splitlines() if ln[:1].isdigit()]
    check("只展示用户要的数量", len(listed) <= 3, str(len(listed)))
    check("按简历搜索会告知用户", "按你的简历" in reply, reply[:80])

    # 放宽条件时，简历提示和放宽提示必须同时出现（不能互相挤掉）
    def city_miss(keyword, city_code=None, limit=200, pages=1):
        return [] if city_code == "010000" else [FakeJob(9)]

    chat.search_51job = city_miss
    chat.plan_mod.search_keywords = fake_keywords
    try:
        relaxed = chat._do_search(
            {"candidates": [], "queue": [], "filters": {}, "goal": {}},
            "找北京的和我简历匹配的岗位", resume_id, {})
    finally:
        chat.search_51job = original_search
        chat.plan_mod.search_keywords = original_plan
    check("放宽与简历提示同时出现",
          "按你的简历" in relaxed and "放宽到全国" in relaxed, relaxed[:120])
    check("提示还有剩余", "还有" in reply, reply[-60:])



def test_multi_tailor(resume_id: int) -> None:
    """「定制1和2」是精确指令，且必须立刻返回（生成放后台）。"""
    it = chat.fast_intent("定制1和2")
    check("定制1和2 走快路径", it is not None and it.name == "tailor", str(it))
    check("两个序号都带上", bool(it) and it.params.get("indices") == [1, 2], str(it.params if it else None))

    from app.routes import generate as gen_route

    cands = [{"source_id": str(i), "title": f"岗位{i}", "company": "X", "location": "北京",
              "score": 60, "url": "u", "description": "要求 Python"} for i in (1, 2)]
    state = {"candidates": cands, "queue": [], "filters": {}, "goal": {}}

    original = gen_route._start
    gen_route._start = lambda job_id, rid, opinion="": "run-smoke"
    try:
        import time as _time

        started = _time.time()
        out = chat._do_tailor_multi(state, [1, 2], resume_id, "")
        elapsed = _time.time() - started
    finally:
        gen_route._start = original

    check("定制立刻返回（不等生成）", elapsed < 2, f"{elapsed:.2f}s")
    check("每个岗位一个进度链接", out.count("看进度") == 2, out[:80])
    check("进了定制队列", len(state["queue"]) == 2, str(len(state["queue"])))



def test_apply_record(resume_id: int) -> None:
    """投递由 agent 记录，不需要用户去别的页面点。"""
    from app.repo import applications as app_repo

    cands = [{"cid": f"c{i}", "source_id": str(i), "title": f"岗位{i}", "company": "X",
              "location": "北京", "score": 60, "url": "u", "description": "要求 Python"}
             for i in (1, 2)]
    state = {"candidates": cands, "queue": [], "filters": {}, "goal": {},
             "resume_id": resume_id, "last_apply_cid": "c2"}

    # 没有打开过投递页时不该记账
    gate = chat._do_record_application({"candidates": cands, "resume_id": resume_id}, None, "")
    check("没打开过投递页不记账", "还没打开过投递页" in gate, gate[:40])

    out = chat._do_record_application(state, None, "手动投递")
    check("投递记录成功", "已记录" in out, out[:40])

    rows = [a for a in app_repo.list_applications() if a.get("status") == "submitted"]
    check("投递表里有记录", len(rows) >= 1, str(len(rows)))

    before = len(app_repo.list_applications())
    chat._do_record_application(state, 2)
    check("重复记录不会产生重复行", len(app_repo.list_applications()) == before,
          f"{before} → {len(app_repo.list_applications())}")

    # 唯一约束真的落到了库上（并发时靠它兜底，而不是靠 find-then-insert）
    import sqlite3

    existing = app_repo.list_applications()[0]
    duplicated = False
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO application(job_id, resume_id) VALUES (?, ?)",
                (existing["job_id"], resume_id))
        except sqlite3.IntegrityError:
            duplicated = True
    check("同一岗位插第二条会被数据库拒绝", duplicated)


def test_merged_proposal(resume_id: int) -> None:
    """多个岗位定制只确认一次。"""
    cands = [{"source_id": str(i), "title": f"岗位{i}", "company": "X", "location": "北京",
              "score": 60, "url": "u", "description": ""} for i in (1, 2)]
    state = {"candidates": cands, "queue": [], "filters": {}, "goal": {}}

    single = chat._propose_tailor(state, [1])
    check("单个定制只提一个", single.count("定制简历吗") == 1, single[:40])

    merged = chat._propose_tailor(state, [1, 2])
    check("多个定制合并成一次确认", "定制1和2" in merged, merged[:80])
    check("合并时列出两个岗位", merged.count("第 ") == 2, merged[:80])



def test_candidate_identity(resume_id: int) -> None:
    """候选的身份必须是 cid，不能是列表位置；追加不能让旧位置漂移。"""
    class FakeJob:
        def __init__(self, i: int, score: float):
            self.source_id, self.title, self.company = str(i), f"岗位{i}", "X"
            self.location, self.url, self.description = "北京", "u", "要求 Python"
            self.experience, self.salary, self.education = "1-3年", "", "本科"

        def as_dict(self):
            return {"source_id": self.source_id, "title": self.title, "company": self.company,
                    "location": self.location, "url": self.url, "description": self.description,
                    "experience": self.experience, "education": self.education}

    calls = {"n": 0}

    def fake_search(keyword, city_code=None, limit=200, pages=1):
        # 第二批分数更高——旧实现会把它们排到前面，导致「第 3 个」换人
        calls["n"] += 1
        base = calls["n"] * 10
        return [FakeJob(base + i, 60 + base + i) for i in range(3)]

    original = chat.search_51job
    chat.search_51job = fake_search
    try:
        state = {"candidates": [], "queue": [], "filters": {}, "goal": {}, "resume_id": resume_id}
        chat._do_search(state, "找 AI 岗位", resume_id, {"keyword": "AI"})
        first = [(c.get("cid"), c["title"]) for c in state["candidates"]]
        check("搜索后每个候选都有 cid", all(c[0] for c in first), str(first))

        chat._do_more(state, resume_id, 3)
        after = [(c.get("cid"), c["title"]) for c in state["candidates"]][:len(first)]
        check("追加后旧候选位置不变", after == first, f"{after} != {first}")
        check("追加后候选变多", len(state["candidates"]) > len(first),
              str(len(state["candidates"])))

        # 「这个」要跟着 cid 走，而不是跟着位置走
        target = state["candidates"][2]
        state["last_cid"] = target.get("cid")
        other = state["candidates"][3]["title"].replace("岗位", "")
        chat._do_exclude(state, other)
        resolved = chat._resolve_candidate(state, None, "这个")
        check("排除后「这个」仍指原来那个岗位",
              resolved is not None and resolved["title"] == target["title"],
              str(resolved["title"] if resolved else None))

        # 满 30 个后追加：给明确提示，且列表不变
        state["candidates"] = [
            {"cid": f"c{i}", "title": f"岗位{i}", "company": "X", "source_id": str(i),
             "score": 50, "location": "北京", "url": "u", "description": "d"}
            for i in range(chat.MAX_CANDIDATES)
        ]
        state["cid_seq"] = chat.MAX_CANDIDATES
        out = chat._do_more(state, resume_id, 3)
        check("候选满 30 个时明确提示", "候选已经有" in out, out[:40])
        check("满了不再增长", len(state["candidates"]) == chat.MAX_CANDIDATES,
              str(len(state["candidates"])))
    finally:
        chat.search_51job = original



def test_agent_pronoun(resume_id: int) -> None:
    """agent 路径下「这个」必须按 last_cid 解析，不能静默取第 1 个。"""
    cid = conv.get_active()
    cands = [
        {"cid": "c1", "source_id": "1", "title": "岗位一", "company": "A", "location": "北京",
         "score": 60, "url": "u", "description": "岗位一的 JD"},
        {"cid": "c2", "source_id": "2", "title": "岗位二", "company": "B", "location": "上海",
         "score": 70, "url": "u", "description": "岗位二的 JD", "salary": "20-30K",
         "experience": "1-3年", "education": "本科"},
        {"cid": "c3", "source_id": "3", "title": "岗位三", "company": "C", "location": "深圳",
         "score": 65, "url": "u", "description": "岗位三的 JD"},
    ]
    state = {"candidates": cands, "queue": [], "filters": {}, "goal": {},
             "resume_id": resume_id, "last_cid": "c2"}
    conv.set_state(cid, state)

    seen: list[str] = []

    async def judge_stub(doc, jd):
        seen.append(jd)
        return {"verdict": "推荐", "summary": "", "reasons": [], "risks": [], "suggestion": ""}

    async def fake_tool_call(task, messages, tools, **kw):
        return client.ToolReply(content="", tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "judge_fit", "arguments": "{}"}}])

    original_judge, original_chat = chat.judge_mod.judge, client.chat_with_tools
    chat.judge_mod.judge = judge_stub
    client.chat_with_tools = fake_tool_call
    try:
        chat.handle(cid, "这个岗位适合我吗", resume_id)
    finally:
        chat.judge_mod.judge = original_judge
        client.chat_with_tools = original_chat
    check("「这个」判断的是第 2 个岗位", seen and "岗位二" in seen[0], str(seen)[:80])

    # 投递提议也要落到第 2 个
    out = chat._propose_apply(state, None, "投这个")
    check("投递提议指向第 2 个", "第 2 个" in out and "岗位二" in out, out[:60])

    # 问薪资时要给属性块，而不是整段 JD
    detail = chat._do_ask_detail(state, None, "这个岗位薪资多少")
    check("问薪资给属性块", "薪资：" in detail, detail[:60])
    check("问薪资不倒整段 JD", "岗位二的 JD" not in detail, detail[:60])



def test_tailor_idempotent(resume_id: int) -> None:
    """同一个岗位连点两次定制，只能有一个任务在跑。"""
    import time as _time

    from app.routes import generate as gen_route

    runs: list[str] = []

    def fake_worker(run_id, job_id, rid, opinion):
        runs.append(run_id)
        _time.sleep(0.4)

    original_worker = gen_route._worker
    gen_route._worker = fake_worker
    gen_route._inflight.clear()
    try:
        first = gen_route._start(901, resume_id)
        second = gen_route._start(901, resume_id)
        check("重复定制返回同一个 run", first == second, f"{first} vs {second}")
        _time.sleep(0.9)
        check("worker 只跑了一次", len(runs) == 1, str(len(runs)))

        _time.sleep(0.2)
        third = gen_route._start(901, resume_id)
        check("上一轮结束后可以重新跑", third != first, f"{third} vs {first}")
        _time.sleep(0.9)
    finally:
        gen_route._worker = original_worker
        gen_route._inflight.clear()



def test_tool_result_shape(resume_id: int) -> None:
    """工具必须返回「结构化观察 + 呈现文案」两套，观察里不能带 markdown 链接。"""
    import json as _json

    from app.agent.tools import ToolResult

    class FakeJob:
        def __init__(self, i: int):
            self.source_id, self.title, self.company = str(i), f"岗位{i}", "X"
            self.location, self.url, self.description = "北京", "u", "要求 Python"
            self.experience, self.salary, self.education = "1-3年", "20-30K", "本科"

        def as_dict(self):
            return {"source_id": self.source_id, "title": self.title, "company": self.company,
                    "location": self.location, "url": self.url, "description": self.description,
                    "experience": self.experience, "education": self.education}

    original = chat.search_51job
    chat.search_51job = lambda *a, **k: [FakeJob(i) for i in range(1, 4)]
    try:
        state = {"candidates": [], "queue": [], "filters": {}, "goal": {},
                 "resume_id": resume_id}
        result = chat._run_tool("search_jobs", {"keyword": "AI"}, state, resume_id, "")
    finally:
        chat.search_51job = original

    check("返回 ToolResult", isinstance(result, ToolResult), type(result).__name__)
    check("有呈现文案", isinstance(result.text, str) and bool(result.text), result.text[:40])
    cands = result.data.get("candidates") or []
    check("观察里有候选列表", bool(cands), str(result.data)[:60])
    if cands:
        first = cands[0]
        check("候选含 index/cid/title/score",
              {"index", "cid", "title", "score"} <= set(first), str(first))
    try:
        _json.dumps(result.data, ensure_ascii=False)
        serializable = True
    except TypeError:
        serializable = False
    check("观察可 JSON 序列化", serializable)

    detail_state = {"candidates": [
        {"cid": "c1", "title": "岗位一", "company": "A", "location": "北京", "score": 60,
         "url": "https://example.com/1", "description": "岗位职责：写代码。任职要求：Python。"}],
        "queue": [], "filters": {}, "goal": {}}
    detail = chat._run_tool("ask_job_detail", {"index": 1}, detail_state, resume_id, "")
    jobs = detail.data.get("jobs") or []
    check("详情带不可信标记", bool(jobs) and "sections_untrusted" in jobs[0], str(jobs)[:60])
    check("观察里没有 markdown 链接", "](/" not in detail.as_message(), detail.as_message()[:80])

    # JD 是外部原文：进 prompt 时要带标记，但不能泄漏到用户可见回复里
    from app.agent import judge as judge_mod

    doc = resume_repo.load_doc(resume_id)
    prompts = judge_mod._messages(doc, "岗位职责：写代码。")
    check("JD 在 prompt 里标为不可信", any("<<<" in m["content"] for m in prompts),
          str(prompts)[:80])

    cid = conv.get_active()
    conv.set_state(cid, {"candidates": detail_state["candidates"], "queue": [],
                         "filters": {}, "goal": {}})

    async def judge_stub(doc_, jd):
        return {"verdict": "推荐", "summary": "", "reasons": [], "risks": [], "suggestion": ""}

    original_judge = chat.judge_mod.judge
    chat.judge_mod.judge = judge_stub
    try:
        reply = chat._do_judge(detail_state, 1, "", resume_id)
    finally:
        chat.judge_mod.judge = original_judge
    check("用户可见回复不出现 <<<", "<<<" not in reply, reply[:60])



def test_budget(resume_id: int) -> None:
    """模型一直调工具要能收尾；LLMError 降级；其它异常必须穿透。"""
    import time as _time

    from app.llm.client import LLMError

    cid = conv.get_active()
    conv.set_state(cid, {"candidates": [], "queue": [], "filters": {}, "goal": {},
                         "resume_id": resume_id})
    text = "我最近在考虑换工作，你觉得我该从哪下手"
    original = client.chat_with_tools

    async def never_stops(task, messages, tools, **kw):
        return client.ToolReply(content="", tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "list_candidates", "arguments": "{}"}}])

    client.chat_with_tools = never_stops
    started = _time.time()
    try:
        out = chat.handle(cid, text, resume_id)
    finally:
        client.chat_with_tools = original
    elapsed = _time.time() - started
    check("一直调工具也能收尾", elapsed < 10, f"{elapsed:.1f}s")
    check("回复说明到了上限", "上限" in out["reply"], out["reply"][-60:])
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) n FROM agent_trace WHERE kind='tool'").fetchone()["n"]
    check("工具调用次数受控", n <= 8, str(n))

    async def llm_down(*a, **k):
        raise LLMError("模型不可用")

    client.chat_with_tools = llm_down
    try:
        out = chat.handle(cid, text, resume_id)
    finally:
        client.chat_with_tools = original
    check("模型不可用时降级到规则", bool(out["reply"]), out["reply"][:30])
    with get_conn() as conn:
        row = conn.execute(
            "SELECT result_json FROM agent_trace WHERE kind='error' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    check("失败原因标记为 llm_unavailable",
          bool(row) and "llm_unavailable" in row["result_json"],
          (row["result_json"] if row else "无记录")[:120])

    async def boom(*a, **k):
        raise RuntimeError("boom")

    client.chat_with_tools = boom
    raised = None
    try:
        try:
            chat.handle(cid, text, resume_id)
        except RuntimeError as exc:
            raised = exc
    finally:
        client.chat_with_tools = original
    check("代码缺陷不被吞成规则回复", isinstance(raised, RuntimeError), str(raised))



def test_record_gate(resume_id: int) -> None:
    """记录投递必须由用户显式发起，而且不能交给模型（写工具已下架）。"""
    from app.agent.tools import TOOLS

    names = {tool["function"]["name"] for tool in TOOLS}
    check("record_application 不在工具面", "record_application" not in names, str(sorted(names)))

    cands = [
        {"cid": "c1", "source_id": "1", "title": "岗位一", "company": "A", "location": "北京",
         "score": 60, "url": "u", "description": "d"},
        {"cid": "c2", "source_id": "2", "title": "岗位二", "company": "B", "location": "上海",
         "score": 70, "url": "u", "description": "d"},
    ]
    state = {"candidates": cands, "resume_id": resume_id, "performed": []}
    out = chat._do_record_application(state, None, "")
    check("没打开过投递页不记账", "还没打开过投递页" in out, out[:40])

    # 「第二个我已经投好了」必须走记录，而不是进 agent 循环
    cid = conv.get_active()
    conv.set_state(cid, {"candidates": cands, "queue": [], "filters": {}, "goal": {},
                         "resume_id": resume_id, "last_apply_cid": "c2"})

    async def boom(*a, **k):
        raise AssertionError("记录投递不应调用模型")

    original = client.chat_with_tools
    client.chat_with_tools = boom
    try:
        out = chat.handle(cid, "第二个我已经投好了", resume_id)
    finally:
        client.chat_with_tools = original

    check("投好了走规则不调模型", out["intent"] == "record", out["intent"])
    check("回复确认已记录", "已记录" in out["reply"], out["reply"][:40])
    performed = conv.get_state(cid).get("performed") or []
    check("performed 里有 record",
          any(item.get("action") == "record" for item in performed), str(performed))



def test_shared_client(resume_id: int) -> None:
    """一轮对话应该复用同一个 HTTP 连接池，而不是每步新建。"""
    cid = conv.get_active()
    conv.set_state(cid, {"candidates": [], "queue": [], "filters": {}, "goal": {}})
    seen: list[object] = []

    async def capture(task, messages, tools, **kw):
        seen.append(kw.get("client"))
        return client.ToolReply(content="好的", tool_calls=[])

    original = client.chat_with_tools
    client.chat_with_tools = capture
    try:
        chat.handle(cid, "我最近在考虑换工作，你觉得我该从哪下手", resume_id)
    finally:
        client.chat_with_tools = original
    check("循环把共享 client 传给了模型调用", bool(seen) and seen[0] is not None,
          str(seen[:1]))



def test_client_robustness(resume_id: int) -> None:
    """截断要提示；tool_calls 要清洗；不支持 response_format 的端点要自动降级。"""
    import json as _json

    import httpx

    from app.llm import client as llm

    cid = conv.get_active()
    conv.set_state(cid, {"candidates": [], "queue": [], "filters": {}, "goal": {}})

    async def truncated(task, messages, tools, **kw):
        return llm.ToolReply(content="半截话", tool_calls=[], finish_reason="length")

    original = llm.chat_with_tools
    llm.chat_with_tools = truncated
    try:
        out = chat.handle(cid, "我最近在考虑换工作，你觉得我该从哪下手", resume_id)
    finally:
        llm.chat_with_tools = original
    check("回答被截断会告知用户", "截断了" in out["reply"], out["reply"][-40:])

    # 后面的用例要真的走 HTTP，所以临时关掉替身
    stub_env = os.environ.pop("RA_LLM_STUB", None)
    try:
        sent: list[dict] = []

        def handler(request):
            sent.append(_json.loads(request.content))
            if len(sent) == 1:
                return httpx.Response(400, json={"error": "response_format not supported"})
            return httpx.Response(200, json={
                "choices": [{"message": {"content": '{"ok": 1}'}, "finish_reason": "stop"}],
            })

        async def run_json():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                return await llm.complete_json(
                    "resume_strategy", [{"role": "user", "content": "x"}], client=http)

        data = asyncio.run(run_json())
        check("json 模式不支持时自动降级", data == {"ok": 1}, str(data))
        check("第一次带 response_format", "response_format" in sent[0])
        check("降级后不再带 response_format", "response_format" not in sent[1])

        def tool_handler(request):
            return httpx.Response(200, json={
                "choices": [{
                    "message": {"content": "", "tool_calls": [{
                        "id": "x", "type": "function", "index": 0,
                        "function": {"name": "search_jobs", "arguments": "{}"},
                    }]},
                    "finish_reason": "tool_calls",
                }],
            })

        async def run_tools():
            async with httpx.AsyncClient(transport=httpx.MockTransport(tool_handler)) as http:
                return await llm.chat_with_tools(
                    "agent_tools", [{"role": "user", "content": "x"}], [], client=http)

        reply = asyncio.run(run_tools())
        check("tool_calls 只保留必要字段",
              set(reply.tool_calls[0]) == {"id", "type", "function"}, str(reply.tool_calls[0]))
        check("finish_reason 透传", reply.finish_reason == "tool_calls", reply.finish_reason)
    finally:
        if stub_env is not None:
            os.environ["RA_LLM_STUB"] = stub_env



def test_retry_policy(resume_id: int) -> None:
    """限流听 Retry-After；鉴权错误不重试。"""
    import time as _time

    import httpx

    from app.llm import client as llm

    stub_env = os.environ.pop("RA_LLM_STUB", None)
    try:
        hits = {"n": 0}

        def limited(request):
            hits["n"] += 1
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow"})

        async def run_limited():
            async with httpx.AsyncClient(transport=httpx.MockTransport(limited)) as http:
                return await llm.complete(
                    "resume_strategy", [{"role": "user", "content": "x"}], client=http)

        started = _time.time()
        try:
            asyncio.run(run_limited())
            raised = None
        except llm.LLMError as exc:
            raised = exc
        elapsed = _time.time() - started
        check("429 总共请求 3 次", hits["n"] == 3, str(hits["n"]))
        check("听 Retry-After，不傻等", elapsed < 3, f"{elapsed:.1f}s")
        check("限流分类正确", raised is not None and raised.kind == "rate_limit", str(raised))

        hits["n"] = 0

        def unauthorised(request):
            hits["n"] += 1
            return httpx.Response(401, json={"error": "bad key"})

        async def run_auth():
            async with httpx.AsyncClient(transport=httpx.MockTransport(unauthorised)) as http:
                return await llm.complete(
                    "resume_strategy", [{"role": "user", "content": "x"}], client=http)

        try:
            asyncio.run(run_auth())
            raised2 = None
        except llm.LLMError as exc:
            raised2 = exc
        check("401 不重试", hits["n"] == 1, str(hits["n"]))
        check("鉴权分类正确", raised2 is not None and raised2.kind == "auth", str(raised2))
    finally:
        if stub_env is not None:
            os.environ["RA_LLM_STUB"] = stub_env



def test_stub_text(resume_id: int) -> None:
    """替身模式下 agent 分支要回一句人话，不能把 JSON 甩给用户。"""
    cid = conv.get_active()
    conv.set_state(cid, {"candidates": [], "queue": [], "filters": {}, "goal": {}})
    original = os.environ.get("RA_LLM_STUB")
    os.environ["RA_LLM_STUB"] = "1"
    try:
        out = chat.handle(cid, "你好呀朋友", resume_id)
    finally:
        if original is None:
            os.environ.pop("RA_LLM_STUB", None)
        else:
            os.environ["RA_LLM_STUB"] = original
    check("替身模式不吐 JSON", "{" not in out["reply"], out["reply"][:60])
    check("替身模式有人话提示", "替身模型" in out["reply"], out["reply"][:60])



def test_tool_registry() -> None:
    """声明、意图、处理器三者必须一一对应，缺一个要在启动时炸掉。"""
    from app.agent import tools as tools_mod

    check("注册表自洽", tools_mod.validate() == [], str(tools_mod.validate()))
    check("每个工具都绑定了 handler",
          all(tool.handler is not None for tool in tools_mod.REGISTRY.values()))
    check("TOOLS 与注册表一一对应",
          {s["function"]["name"] for s in tools_mod.TOOLS} == set(tools_mod.REGISTRY),
          str(set(tools_mod.REGISTRY)))

    try:
        tools_mod.bind_all({"nonexistent_tool": lambda *a: None})
        rejected = False
    except KeyError:
        rejected = True
    check("绑定未声明的工具会被拒绝", rejected)



def test_state_lock(resume_id: int) -> None:
    """同一会话的两条消息不能并行处理——否则后写的会把先写的状态冲掉。"""
    import threading
    import time as _time

    cid = conv.get_active()
    events: list[str] = []

    def slow_locked(conversation_id, text, resume_id_):
        events.append("enter")
        _time.sleep(0.3)
        events.append("exit")
        return {"reply": "ok", "intent": "chat", "suggestions": []}

    original = chat._handle_locked
    chat._handle_locked = slow_locked
    try:
        threads = [
            threading.Thread(target=chat.handle, args=(cid, f"消息{i}", resume_id))
            for i in range(2)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=5)
    finally:
        chat._handle_locked = original

    overlapped = any(
        events[i] == "enter" and events[i + 1] == "enter"
        for i in range(len(events) - 1)
    )
    check("同一会话串行执行", not overlapped, str(events))
    check("两条消息都跑完了", events.count("exit") == 2, str(events))
    check("同一会话拿到的是同一把锁",
          chat._lock_for(cid) is chat._lock_for(cid))



def test_tool_timeout(resume_id: int) -> None:
    """工具有执行上限；失败要能区分超时/异常/未知工具。"""
    import time as _time

    from app.agent import tools as tools_mod
    from app.agent.tools import ToolResult

    # 1) 超时
    def slow_tool(name, args, state, rid, utterance=""):
        _time.sleep(1.2)
        return ToolResult(True, "status", "慢工具返回了", {})

    original_run = chat._run_tool
    original_timeout = tools_mod.TOOL_TIMEOUT
    chat._run_tool = slow_tool
    tools_mod.TOOL_TIMEOUT = 0.3

    cid = conv.get_active()
    conv.set_state(cid, {"candidates": [], "queue": [], "filters": {}, "goal": {}})

    async def always_call(task, messages, tools, **kw):
        return client.ToolReply(content="", tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": "list_candidates", "arguments": "{}"}}])

    original_call = client.chat_with_tools
    client.chat_with_tools = always_call
    started = _time.time()
    try:
        out = chat.handle(cid, "我最近在考虑换工作，你觉得我该从哪下手", resume_id)
    finally:
        client.chat_with_tools = original_call
        chat._run_tool = original_run
        tools_mod.TOOL_TIMEOUT = original_timeout
    elapsed = _time.time() - started
    check("工具超时不会拖住整轮", elapsed < 3, f"{elapsed:.1f}s")
    check("超时会告知用户", "超过" in out["reply"], out["reply"][:60])

    # 2) 错误分类
    class FailingTool:
        intent = "status"

        @staticmethod
        def handler(*args):
            raise RuntimeError("boom")

    original_get = tools_mod.get
    tools_mod.get = lambda name: FailingTool()
    try:
        failed = chat._run_tool("list_candidates", {}, {"candidates": []}, resume_id, "")
    finally:
        tools_mod.get = original_get
    check("工具异常标 tool_error", failed.ok is False and failed.error_kind == "tool_error",
          failed.error_kind)

    unknown = chat._run_tool("no_such_tool", {}, {}, resume_id, "")
    check("未知工具标 unknown", unknown.error_kind == "unknown", unknown.error_kind)



def test_input_caps(resume_id: int) -> None:
    """超长输入不该整段进 prompt；候选里的 JD 也要截断。"""
    import json as _json
    cid = conv.get_active()
    conv.set_state(cid, {"candidates": [], "queue": [], "filters": {}, "goal": {}})

    async def boom(*a, **k):
        raise AssertionError("超长消息不应进入 agent")

    original = client.chat_with_tools
    client.chat_with_tools = boom
    try:
        out = chat.handle(cid, "找岗位" * 2000, resume_id)
    finally:
        client.chat_with_tools = original
    check("超长消息被挡下", "太长" in out["reply"], out["reply"][:40])
    check("提示给出上限", str(chat.MAX_USER_CHARS) in out["reply"], out["reply"][:60])

    # 候选里的 JD 截断
    class LongJob:
        source_id, title, company = "9", "超长 JD 岗位", "X"
        location, url = "北京", "u"
        description = "要求：" + "Python " * 1000
        experience, salary, education = "1-3年", "", "本科"

        def as_dict(self):
            return {"source_id": self.source_id, "title": self.title, "company": self.company,
                    "location": self.location, "url": self.url,
                    "description": self.description, "experience": self.experience,
                    "education": self.education}

    original_search = chat.search_51job
    chat.search_51job = lambda *a, **k: [LongJob()]
    try:
        state = {"candidates": [], "queue": [], "filters": {}, "goal": {}}
        chat._do_search(state, "找 AI 岗位", resume_id, {"keyword": "AI"})
    finally:
        chat.search_51job = original_search
    stored = state["candidates"][0]["description"]
    check("候选 JD 被截断", len(stored) <= chat.MAX_JD_CHARS, str(len(stored)))
    check("状态不会因为长 JD 膨胀", len(_json.dumps(state)) < 10000, str(len(_json.dumps(state))))


def test_trace() -> None:
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT kind, name FROM agent_trace")]
    kinds = {r["kind"] for r in rows}
    check("trace 有记录", bool(rows), "空")
    check("trace 覆盖 intent/tool/result", {"intent", "tool", "result"} <= kinds, str(kinds))



def test_prune() -> None:
    """观测/日志表要有保留策略，不能无限长（放最后跑，会清表）。"""
    from app.repo import maintenance

    with get_conn() as conn:
        conn.execute("DELETE FROM agent_trace")
        for i in range(8):
            conn.execute(
                "INSERT INTO agent_trace(turn_id, step, kind, name, created_at) "
                "VALUES (?, 0, 'tool', 'x', datetime('now','localtime','-10 days'))",
                (f"old{i}",),
            )
        for i in range(8):
            conn.execute(
                "INSERT INTO agent_trace(turn_id, step, kind, name) VALUES (?, 0, 'tool', 'x')",
                (f"new{i}",),
            )

    removed = maintenance.prune_agent_trace(keep_days=7, keep_rows=5)
    with get_conn() as conn:
        left = conn.execute("SELECT COUNT(*) n FROM agent_trace").fetchone()["n"]
    check("过期 trace 被清掉", removed >= 8, str(removed))
    check("按行数兜底到上限以内", left <= 5, str(left))

    cid = conv.get_active()
    for i in range(30):
        conv.add_message(cid, "user", f"m{i}")
    removed_msgs = maintenance.prune_messages(keep=10)
    check("消息按会话裁剪", removed_msgs >= 20, str(removed_msgs))


def main() -> int:
    resume_id = setup()
    test_intent()
    test_fast_path_rules()
    test_loop(resume_id)
    test_fast_path(resume_id)
    test_clarify(resume_id)
    test_relax(resume_id)
    test_checkpoint(resume_id)
    test_verify(resume_id)
    test_multi_index(resume_id)
    test_resume_search(resume_id)
    test_multi_tailor(resume_id)
    test_apply_record(resume_id)
    test_merged_proposal(resume_id)
    test_candidate_identity(resume_id)
    test_agent_pronoun(resume_id)
    test_tailor_idempotent(resume_id)
    test_tool_result_shape(resume_id)
    test_budget(resume_id)
    test_record_gate(resume_id)
    test_shared_client(resume_id)
    test_client_robustness(resume_id)
    test_retry_policy(resume_id)
    test_stub_text(resume_id)
    test_tool_registry()
    test_state_lock(resume_id)
    test_tool_timeout(resume_id)
    test_input_caps(resume_id)
    test_trace()
    test_prune()

    if failures:
        print(f"\n失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
