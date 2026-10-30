"""对话引擎：把一句话变成简历增量。

流程固定为：读缺口 → 问一个问题 → 用户回答 → 抽 patch → 用户确认 → 写入。
patch 不自动落库，是为了让「模型理解错」的代价停在一次点击上。
"""
from __future__ import annotations

import json
from typing import Any

from ..llm import client
from ..repo import resumes as repo
from . import store
from .gaps import GapReport, analyze
from .patch import Patch, apply_patches, parse_patches
from .prompt import build_messages


def opening_question(doc) -> str:
    """开场问题不花 token：直接取缺口里最重要的那个。"""
    report = analyze(doc)
    top = report.top
    if top:
        return top.question
    return "简历看起来已经比较完整了。还有什么想补充的经历吗？"


def ensure_session(resume_id: int, mode: str = "supplement") -> dict[str, Any]:
    existing = store.latest_session(resume_id)
    if existing:
        return existing

    doc = repo.load_doc(resume_id)
    if doc is None:
        raise ValueError("简历不存在")

    profile_id = _profile_id(resume_id)
    session_id = store.create_session(profile_id, resume_id, mode)
    store.add_turn(session_id, "assistant", opening_question(doc))
    session = store.get_session(session_id)
    assert session is not None
    return session


def _profile_id(resume_id: int) -> int:
    for row in repo.list_resumes():
        if row["id"] == resume_id:
            return int(row["profile_id"])
    raise ValueError("简历不存在")


def pending_turn(session_id: int) -> dict[str, Any] | None:
    """等待用户确认的那一轮。"""
    for turn in reversed(store.list_turns(session_id)):
        if turn["role"] == "assistant" and turn["accepted"] is None and turn["patches"]:
            return turn
    return None


async def say(session_id: int, user_text: str) -> dict[str, Any]:
    """用户说了一句话，交给模型抽取增量。"""
    session = store.get_session(session_id)
    if session is None:
        raise ValueError("会话不存在")

    doc = repo.load_doc(int(session["resume_id"]))
    if doc is None:
        raise ValueError("简历不存在")

    gaps = analyze(doc)
    history = [
        {"role": t["role"], "content": t["content"]}
        for t in store.list_turns(session_id)
        if t["role"] in ("user", "assistant") and t["content"]
    ]

    messages = build_messages(doc, gaps, history, user_text)
    data = await client.complete_json("dialogue", messages)

    ack = str(data.get("ack") or "").strip()
    question = str(data.get("question") or "").strip()
    patches = parse_patches(data.get("patches"))

    store.add_turn(session_id, "user", user_text)
    content = "\n\n".join(x for x in (ack, question) if x) or "（模型没有返回内容）"
    turn_id = store.add_turn(
        session_id,
        "assistant",
        content,
        patch_json=json.dumps([p.model_dump() for p in patches], ensure_ascii=False),
    )
    return {"turn_id": turn_id, "content": content, "patches": [p.describe() for p in patches]}


def accept(session_id: int, turn_id: int) -> tuple[list[str], list[str]]:
    """确认并写入文档。"""
    session = store.get_session(session_id)
    turn = store.get_turn(turn_id)
    if session is None or turn is None or not turn["patch_json"]:
        return [], ["没有可应用的改动"]

    doc = repo.load_doc(int(session["resume_id"]))
    if doc is None:
        return [], ["简历不存在"]

    patches: list[Patch] = parse_patches(json.loads(turn["patch_json"]))
    applied, rejected = apply_patches(doc, patches)
    if applied:
        repo.replace_doc(int(session["resume_id"]), doc)
        repo.update_profile(int(session["resume_id"]), doc)
    store.set_accepted(turn_id, 1)
    return applied, rejected


def reject(turn_id: int) -> None:
    store.set_accepted(turn_id, 0)


def remaining(resume_id: int) -> GapReport:
    doc = repo.load_doc(resume_id)
    return analyze(doc) if doc else GapReport()
