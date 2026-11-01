"""录制 agent 夹具（本地用，需要真实模型）。

    python tools/record_agent.py

这个脚本会**产生真实费用**：每个用例要调 1-3 次模型。
只在改了工具描述 / 系统提示之后跑一次，把新行为录成夹具，
之后 CI 用 tools/smoke_agent_replay.py 回放。

录出来的文件在 tools/fixtures/agent/*.json。
注意：夹具回放测的是「循环 + 工具接线」，不是模型选工具的语义质量。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))
sys.path.insert(0, str(BASE))

import smoke_agent_replay as harness  # noqa: E402

from app.agent import chat  # noqa: E402
from app.agent import conversation as conv
from app.llm import client  # noqa: E402

CANDIDATES = [
    {"source_id": "1", "title": "AI 应用工程师", "company": "字节跳动", "location": "北京",
     "score": 69.8, "url": "https://example.com/1",
     "description": "岗位职责：负责大模型应用落地。任职要求：Python、RAG、后端经验。"},
    {"source_id": "2", "title": "视觉算法工程师", "company": "百度", "location": "北京",
     "score": 66.4, "url": "https://example.com/2",
     "description": "岗位职责：视觉算法研发。任职要求：深度学习、目标检测。"},
    {"source_id": "3", "title": "后端开发工程师", "company": "美团", "location": "北京",
     "score": 55.2, "url": "https://example.com/3",
     "description": "岗位职责：服务端开发。任职要求：Python、MySQL、高并发。"},
]

CASES = [
    {"id": "search_basic", "utterance": "找 3 个和我简历匹配的岗位"},
    {"id": "search_then_judge", "utterance": "找几个岗位，然后告诉我哪个最值得投"},
    {"id": "ask_detail", "utterance": "第一个岗位具体要什么"},
    {"id": "tailor_proposal", "utterance": "帮我定制第二个"},
    {"id": "exclude_followup", "utterance": "我这边不想要外包的岗位"},
]


def main() -> int:
    resume_id = harness._setup_resume()
    fixture_dir = harness.FIXTURE_DIR
    fixture_dir.mkdir(parents=True, exist_ok=True)

    calls: list[str] = []
    harness._apply_stubs(calls, CANDIDATES)

    for case in CASES:
        recorded: list[dict] = []

        async def recording(task, messages, tools, **kw):
            reply = await real_chat(task, messages, tools, **kw)
            recorded.append({
                "finish_reason": reply.finish_reason,
                "content": reply.content or "",
                "tool_calls": reply.tool_calls or [],
            })
            return reply

        real_chat = client.chat_with_tools
        client.chat_with_tools = recording
        calls.clear()
        try:
            state = {"candidates": [dict(c) for c in CANDIDATES], "queue": [],
                     "filters": {}, "goal": {}, "resume_id": resume_id,
                     "last_apply_cid": "c2"}
            for index, cand in enumerate(state["candidates"], start=1):
                cand["cid"] = f"c{index}"
            conv.set_state(conv.get_active(), state)
            conv.clear_messages(conv.get_active())
            out = chat.handle(conv.get_active(), case["utterance"], resume_id)
        finally:
            client.chat_with_tools = real_chat

        reply = out.get("reply") or ""
        content = next((r["content"] for r in reversed(recorded) if r["content"]), "")
        expect = {
            "tools": calls,
            "reply_contains": (content or reply)[:12],
        }
        fixture = {
            "id": case["id"],
            "utterance": case["utterance"],
            "state": {"candidates": CANDIDATES, "queue": [], "filters": {}, "goal": {}},
            "fake_candidates": CANDIDATES,
            "responses": recorded,
            "expect": expect,
        }
        path = fixture_dir / f"{case['id']}.json"
        path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"写入 {path.name}：工具 {calls}，{len(recorded)} 次模型响应")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
