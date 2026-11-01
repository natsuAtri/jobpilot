"""Agent 夹具回放：用录好的模型响应驱动整个循环，不需要真实模型。

    python tools/smoke_agent_replay.py          # 跑全部
    python tools/smoke_agent_replay.py -v       # 打印每个用例的细节

夹具在 tools/fixtures/agent/*.json，用 tools/record_agent.py 录（需要真实模型）。
这个脚本只测「循环 + 工具接线」是否还通，**不测**模型选工具的语义质量——
那由 tools/eval_agent.py 负责（需要真实模型，不进 CI）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

_TMP = Path(tempfile.mkdtemp(prefix="jp-agent-replay-"))
os.environ["RA_DATA_DIR"] = str(_TMP)
os.environ.pop("RA_LLM_STUB", None)

from app.agent import chat  # noqa: E402
from app.agent import conversation as conv
from app.db import get_conn, migrate  # noqa: E402
from app.llm import client  # noqa: E402
from app.models import Bullet, Item, ResumeDoc, Section  # noqa: E402
from app.repo import resumes as resume_repo  # noqa: E402

FIXTURE_DIR = BASE / "tools" / "fixtures" / "agent"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def _setup_resume() -> int:
    migrate()
    doc = ResumeDoc(name="张三", intents="AI 应用开发")
    doc.sections = [
        Section(type="education", items=[Item(org="某某大学", role="本科",
                                             meta={"专业": "软件工程"})]),
        Section(type="skill", items=[Item(org="Python"), Item(org="PyTorch")]),
        Section(type="project", items=[Item(org="手语翻译系统", bullets=[
            Bullet(text="基于 YOLOv8 实现手势识别"),
        ])]),
    ]
    path = Path(os.environ["RA_DATA_DIR"]) / "app.db"
    if path.exists():
        with get_conn() as conn:
            row = conn.execute("SELECT id FROM resume LIMIT 1").fetchone()
        if row:
            return int(row["id"])
    return resume_repo.create_resume(doc, title="回放用")


def _apply_stubs(calls: list[str], candidates: list[dict]) -> None:
    """有副作用的工具全部替换成桩：不联网、不开浏览器、不写业务表。

    注意各自的返回形状要和真实函数一致：
    叶子函数返回字符串；_*_many / _advice_of 返回 (文本, 数据) 二元组。
    """

    def text_stub(name: str):
        def inner(*args, **kwargs):
            calls.append(name)
            return f"[桩] {name}"
        return inner

    def pair_stub(name: str):
        def inner(*args, **kwargs):
            calls.append(name)
            return f"[桩] {name}", []
        return inner

    chat._do_search = text_stub("search_jobs")           # type: ignore[assignment]
    chat._do_more = text_stub("more_jobs")               # type: ignore[assignment]
    chat._do_exclude = text_stub("exclude_jobs")         # type: ignore[assignment]
    chat._do_status = text_stub("list_candidates")       # type: ignore[assignment]
    chat._propose_tailor = text_stub("tailor_resume")    # type: ignore[assignment]
    chat._propose_apply = text_stub("apply_job")         # type: ignore[assignment]
    chat._do_record_application = text_stub("record_application")  # type: ignore[assignment]
    chat._ask_detail_many = pair_stub("ask_job_detail")  # type: ignore[assignment]
    chat._judge_many = pair_stub("judge_fit")            # type: ignore[assignment]
    chat._advice_of = pair_stub("resume_advice")         # type: ignore[assignment]


def _run(fixture: dict, resume_id: int) -> tuple[list[str], str]:
    responses = list(fixture.get("responses") or [])
    calls: list[str] = []
    candidates = fixture.get("fake_candidates") or []
    for index, cand in enumerate(candidates, start=1):
        cand.setdefault("cid", f"c{index}")

    _apply_stubs(calls, candidates)

    async def replay_chat(task, messages, tools, **kw):
        if not responses:
            raise AssertionError("夹具响应用完了")
        item = responses.pop(0)
        return client.ToolReply(
            content=item.get("content") or "",
            tool_calls=item.get("tool_calls") or [],
            finish_reason=item.get("finish_reason") or "",
        )

    original = client.chat_with_tools
    client.chat_with_tools = replay_chat
    try:
        state = dict(fixture.get("state") or {})
        state["candidates"] = candidates
        state["resume_id"] = resume_id
        conv.set_state(conv.get_active(), state)
        conv.clear_messages(conv.get_active())
        out = chat.handle(conv.get_active(), fixture["utterance"], resume_id)
    finally:
        client.chat_with_tools = original
    return calls, out.get("reply") or ""


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    resume_id = _setup_resume()
    fixtures = sorted(FIXTURE_DIR.glob("*.json"))
    if not fixtures:
        print(f"没有找到夹具：{FIXTURE_DIR}")
        return 1

    for path in fixtures:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        expected = fixture.get("expect") or {}
        calls, reply = _run(fixture, resume_id)

        tools_ok = calls == (expected.get("tools") or [])
        needle = expected.get("reply_contains") or ""
        reply_ok = (not needle) or (needle in reply)
        with get_conn() as conn:
            verified = conn.execute(
                "SELECT COUNT(*) n FROM agent_trace WHERE kind='verify'").fetchone()["n"]
        ok = tools_ok and reply_ok and verified == 0
        detail = ""
        if not tools_ok:
            detail = f"工具 {calls} != {expected.get('tools')}"
        elif not reply_ok:
            detail = f"回复不含「{needle}」"
        elif verified:
            detail = f"出现 {verified} 条 verify 告警"

        check(fixture["id"], ok, detail)
        if verbose:
            print(f"      工具：{calls}")
            print(f"      回复：{reply[:100]}")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
