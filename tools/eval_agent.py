"""Agent 对话评测集：验证「模型能不能选对工具」。

    python tools/eval_agent.py          # 跑全部
    python tools/eval_agent.py -v       # 打印每一轮的工具调用

需要真实模型（会消耗少量 token），因此不进 CI。
用独立临时数据库，不会碰用户的库；搜索/判断/投递全部替换成假实现，
不联网、不开浏览器。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

_TMP = Path(tempfile.mkdtemp(prefix="jp-agent-eval-"))
os.environ["RA_DATA_DIR"] = str(_TMP)
os.environ.pop("RA_LLM_STUB", None)

from app.agent import chat  # noqa: E402
from app.agent import conversation as conv
from app.db import get_conn, migrate  # noqa: E402
from app.models import Bullet, Item, ResumeDoc, Section  # noqa: E402
from app.repo import resumes as resume_repo  # noqa: E402

CANDIDATES = [
    {"source_id": "1", "title": "AI 应用工程师", "company": "字节跳动", "location": "北京",
     "score": 69.8, "url": "u", "description": "负责大模型应用落地。要求 Python、RAG、后端经验。"},
    {"source_id": "2", "title": "视觉算法工程师", "company": "百度", "location": "北京",
     "score": 66.4, "url": "u", "description": "负责视觉算法研发。要求深度学习、目标检测。"},
    {"source_id": "3", "title": "后端开发工程师", "company": "美团", "location": "北京",
     "score": 55.2, "url": "u", "description": "负责服务端开发。要求 Python、MySQL、高并发。"},
]

# (用户说, 期望调用的工具, 说明)
CASES: list[tuple[str, set[str], str]] = [
    ("找 3 个和我简历匹配的岗位", {"search_jobs"}, "按简历推导关键词"),
    ("帮我找上海的算法岗位", {"search_jobs"}, "带城市"),
    ("再找几个", {"more_jobs"}, "翻页"),
    ("不要外包的", {"exclude_jobs"}, "排除"),
    ("帮我分析一下第二个和第三个岗位是否适合我", {"judge_fit"}, "多个岗位"),
    ("第三个呢", {"judge_fit", "ask_job_detail"}, "省略句"),
    ("第一个岗位具体要什么", {"ask_job_detail"}, "问详情"),
    ("我手头这几个机会靠谱吗", {"list_candidates", "judge_fit"}, "泛问"),
    ("我的简历还需要补什么", {"resume_advice"}, "简历建议"),
    ("帮我定制第二个", {"tailor_resume"}, "定制要提议"),
    ("帮我投一下第三个岗位", {"apply_job"}, "投递要提议"),
    ("第二个我已经投好了", {"record_application"}, "投递后由 agent 记录"),
    ("帮我定制第一个和第二个", {"tailor_resume"}, "多个定制只提一次"),
    ("这几个岗位里哪个最值得投", {"judge_fit"}, "比较"),
    ("第 2 个公司是做什么的", {"ask_job_detail"}, "公司信息"),
    ("现在有哪些", {"list_candidates"}, "列候选"),
    ("适合我的岗位还有吗", {"more_jobs", "search_jobs"}, "继续找"),
]


def setup() -> int:
    migrate()
    doc = ResumeDoc(name="张三", intents="AI 应用开发")
    doc.sections = [
        Section(type="education", items=[Item(org="西北工业大学", role="本科",
                                             meta={"专业": "软件工程"})]),
        Section(type="skill", items=[Item(org="Python"), Item(org="PyTorch")]),
        Section(type="project", items=[Item(org="手语翻译系统", bullets=[
            Bullet(text="基于 YOLOv8 实现手势识别"),
            Bullet(text="构建图卷积网络完成 CAD 特征识别"),
        ])]),
    ]
    return resume_repo.create_resume(doc, title="评测用")


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    resume_id = setup()
    conversation_id = conv.get_active()

    calls: list[str] = []

    def fake_search(state, text, rid, params=None):
        calls.append("search_jobs")
        state["candidates"] = list(CANDIDATES)
        return "找到 3 个岗位"

    def fake_more(state, rid, count):
        calls.append("more_jobs")
        return "又找到 2 个岗位"

    def fake_exclude(state, words):
        calls.append("exclude_jobs")
        return f"已排除「{words}」"

    def fake_status(state):
        calls.append("list_candidates")
        return "当前 3 个候选"

    def fake_detail(state, index, text=""):
        calls.append("ask_job_detail")
        cand = state["candidates"][(index or 1) - 1]
        return f"「{cand['title']}」：{cand['description']}"

    def fake_judge_multi(state, idxs, text, rid):
        calls.append("judge_fit")
        return "适配度：推荐"

    def fake_advice(rid):
        calls.append("resume_advice")
        return "建议补一段后端经历"

    def fake_propose_tailor(state, indices):
        calls.append("tailor_resume")
        return f"要定制这些吗：{indices}"

    def fake_propose_apply(state, index):
        calls.append("apply_job")
        return f"要投第 {index} 个吗？"

    def fake_record(state, index=None, note=""):
        calls.append("record_application")
        return f"已记录第 {index} 个"

    chat._do_search = fake_search
    chat._do_more = fake_more
    chat._do_exclude = fake_exclude
    chat._do_status = fake_status
    chat._do_ask_detail = fake_detail
    chat._do_judge_multi = fake_judge_multi
    chat._do_advice = fake_advice
    chat._propose_tailor = fake_propose_tailor
    chat._propose_apply = fake_propose_apply
    chat._do_record_application = fake_record

    passed = 0
    for text, expected, note in CASES:
        calls.clear()
        with get_conn() as conn:
            conn.execute("DELETE FROM agent_trace")
        conv.set_state(conversation_id,
                       {"candidates": list(CANDIDATES), "queue": [], "filters": {}, "goal": {}})
        try:
            out = chat.handle(conversation_id, text, resume_id)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL  {text}  [{note}]  异常 {type(exc).__name__}: {exc}")
            continue

        got = set(calls)
        ok = bool(expected & got)
        passed += ok
        mark = "PASS" if ok else "FAIL"
        if verbose or not ok:
            print(f"{mark}  {text}")
            print(f"      期望 {sorted(expected)} → 实际 {sorted(got)}（{note}）")
            if verbose:
                print(f"      回复：{(out.get('reply') or '')[:120]}")

    total = len(CASES)
    print(f"\n工具选择正确率：{passed}/{total} = {passed / total * 100:.0f}%")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
