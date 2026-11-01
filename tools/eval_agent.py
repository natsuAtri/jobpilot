"""Agent 对话评测集：验证「模型能不能选对工具」。

    python tools/eval_agent.py          # 跑全部
    python tools/eval_agent.py -v       # 打印每一轮的工具调用与回复

需要真实模型（会消耗少量 token），因此不进 CI；CI 用 tools/smoke_agent_replay.py
回放录好的夹具。用独立临时数据库，不碰用户的库；搜索/判断/投递全部替换成假实现，
不联网、不开浏览器。

判定规则：
- mode = "exact"（默认）：实际调用的工具集合必须**完全等于** tools
- mode = "subset"：tools 是实际调用的子集，且不能碰 forbid 里的工具
- reply_must：最终回复必须能匹配这条正则
"""
from __future__ import annotations

import os
import re
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
from app.db import migrate  # noqa: E402
from app.models import Bullet, Item, ResumeDoc, Section  # noqa: E402
from app.repo import resumes as resume_repo  # noqa: E402

CANDIDATES = [
    {"cid": "c1", "source_id": "1", "title": "AI 应用工程师", "company": "字节跳动",
     "location": "北京", "score": 69.8, "url": "u",
     "description": "负责大模型应用落地。要求 Python、RAG、后端经验。"},
    {"cid": "c2", "source_id": "2", "title": "视觉算法工程师", "company": "百度",
     "location": "北京", "score": 66.4, "url": "u",
     "description": "负责视觉算法研发。要求深度学习、目标检测。"},
    {"cid": "c3", "source_id": "3", "title": "后端开发工程师", "company": "美团",
     "location": "北京", "score": 55.2, "url": "u",
     "description": "负责服务端开发。要求 Python、MySQL、高并发。"},
]

BASE_STATE = {
    "candidates": CANDIDATES,
    "queue": [],
    "filters": {},
    "goal": {},
    "last_apply_cid": "c2",
}

CASES: list[dict] = [
    {"text": "找 3 个和我简历匹配的岗位", "tools": {"search_jobs"}, "mode": "subset",
     "forbid": {"judge_fit", "tailor_resume", "apply_job"}},
    {"text": "帮我找上海的算法岗位", "tools": {"search_jobs"}, "mode": "subset",
     "forbid": {"judge_fit", "tailor_resume", "apply_job"}},
    {"text": "还有别的吗，再给我找几个", "tools": {"more_jobs"}, "mode": "subset",
     "forbid": {"tailor_resume", "apply_job"}},
    {"text": "我这边不想要外包的岗位", "tools": {"exclude_jobs"}, "mode": "subset",
     "forbid": {"tailor_resume", "apply_job"}},
    {"text": "帮我分析一下第二个和第三个岗位是否适合我", "tools": {"judge_fit"},
     "mode": "subset"},
    {"text": "第三个呢", "tools": {"judge_fit"}, "mode": "subset",
     "state": {"last_action": "judge",
               "last_user_text": "帮我分析一下第二个和第三个岗位是否适合我"}},
    {"text": "第一个岗位具体要什么", "tools": {"ask_job_detail"}},
    {"text": "我手头这几个机会靠谱吗", "tools": {"judge_fit"}, "mode": "subset"},
    {"text": "我的简历还需要补什么", "tools": {"resume_advice"}},
    {"text": "帮我定制第二个", "tools": {"tailor_resume"}},
    {"text": "帮我投一下第三个岗位", "tools": {"apply_job"}},
    {"text": "这几个岗位里哪个最值得投", "tools": {"judge_fit"}, "mode": "subset"},
    {"text": "第二个岗位是做什么的", "tools": {"ask_job_detail"}},
    {"text": "我看看现在都有哪些候选机会", "tools": {"list_candidates"}, "mode": "subset"},
    {"text": "适合我的岗位还有吗", "tools": {"more_jobs"}, "mode": "subset"},
    {"text": "帮我定制第一个和第二个", "tools": {"tailor_resume"}},
    # 写操作已下架：记录投递只能靠规则，不允许任何工具调用
    {"text": "第二个我已经投好了", "tools": set(), "reply_must": "已记录"},
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


def build_stubs() -> list[str]:
    """桩在 _run_tool 这一层：这里只关心「模型选了哪个工具」。

    工具内部实现由 tools/smoke_agent.py 覆盖，不在这里重复测。
    """
    from app.agent.tools import TOOL_INTENT, ToolResult

    calls: list[str] = []

    def fake_run_tool(name, args, state, resume_id, utterance=""):
        calls.append(name)
        return ToolResult(True, TOOL_INTENT.get(name, "chat"),
                          f"[桩] 已执行 {name} {args}", {"tool": name, "args": args})

    chat._run_tool = fake_run_tool
    return calls


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    resume_id = setup()
    conversation_id = conv.get_active()
    calls = build_stubs()

    if not os.environ.get("RA_LLM_BASE_URL") and not os.environ.get("RA_LLM_MODEL"):
        print("没有配置模型（RA_LLM_BASE_URL / RA_LLM_MODEL），这个评测需要真实模型。")
        print("规则层回归请跑 tools/smoke_agent.py 或 tools/smoke_agent_replay.py。")
        return 2

    passed = 0
    for case in CASES:
        text = case["text"]
        expected = set(case["tools"])
        mode = case.get("mode", "exact")
        forbid = set(case.get("forbid") or ())

        state = dict(BASE_STATE)
        state.update(case.get("state") or {})
        state["resume_id"] = resume_id
        conv.set_state(conversation_id, state)
        conv.clear_messages(conversation_id)
        calls.clear()

        try:
            out = chat.handle(conversation_id, text, resume_id)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL  {text}  异常 {type(exc).__name__}: {exc}")
            continue

        got = set(calls)
        if mode == "subset":
            ok = expected <= got and not (got & forbid)
        else:
            ok = got == expected
        if ok and case.get("reply_must"):
            ok = bool(re.search(case["reply_must"], out.get("reply") or ""))
        passed += ok

        if verbose or not ok:
            print(f"{'PASS' if ok else 'FAIL'}  {text}")
            print(f"      期望 {mode} {sorted(expected)} → 实际 {sorted(got)}")
            if verbose:
                print(f"      回复：{(out.get('reply') or '')[:110]}")

    total = len(CASES)
    print(f"\n工具选择正确率：{passed}/{total} = {passed / total * 100:.0f}%")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
