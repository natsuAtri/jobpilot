"""对话式补录：页面与动作。"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..dialogue import doubts as doubts_mod
from ..dialogue import engine, store
from ..llm import client, registry
from ..repo import resumes as repo
from ..templating import templates

router = APIRouter(prefix="/resumes")


def _resume_or_404(resume_id: int) -> dict:
    row = next((r for r in repo.list_resumes() if r["id"] == resume_id), None)
    if row is None:
        raise HTTPException(404, "简历不存在")
    return row


@router.get("/{resume_id}/dialogue")
async def dialogue_page(
    request: Request, resume_id: int, err: str | None = None, mode: str = "supplement",
    msg: str | None = None,
):
    row = _resume_or_404(resume_id)
    session = engine.ensure_session(resume_id, mode if mode in ("supplement", "scratch") else "supplement")
    turns = store.list_turns(int(session["id"]))
    pending = engine.pending_turn(int(session["id"]))
    doc = repo.load_doc(resume_id)
    doubts = doubts_mod.generate(doc) if doc else doubts_mod.DoubtReport()
    has_model = registry.configured()

    return templates.TemplateResponse(
        request,
        "dialogue.html",
        {
            "active": "resume",
            "resume": row,
            "session": session,
            "turns": turns,
            "pending": pending,
            "doubts": doubts,
            "has_model": has_model,
            "err": err,
            # 用 notice 而不是 msg：base.html 会用 msg 渲染一条全局横幅，
            # 这里自带「去 Agent」链接，避免同一句话出现两次
            "notice": msg,
        },
    )


@router.post("/{resume_id}/dialogue/say")
async def dialogue_say(resume_id: int, text: str = Form(...)):
    _resume_or_404(resume_id)
    text = text.strip()
    if not text:
        return RedirectResponse(f"/resumes/{resume_id}/dialogue", status_code=303)

    session = engine.ensure_session(resume_id)
    try:
        await engine.say(int(session["id"]), text)
    except client.LLMError as exc:
        return RedirectResponse(
            f"/resumes/{resume_id}/dialogue?err={exc}", status_code=303
        )
    return RedirectResponse(f"/resumes/{resume_id}/dialogue", status_code=303)


@router.post("/{resume_id}/dialogue/accept/{turn_id}")
async def dialogue_accept(resume_id: int, turn_id: int):
    session = engine.ensure_session(resume_id)
    applied, rejected = engine.accept(int(session["id"]), turn_id)
    if applied:
        return RedirectResponse(
            f"/resumes/{resume_id}?msg=已写入 {len(applied)} 处改动", status_code=303
        )
    reason = rejected[0] if rejected else "没有可写入的改动"
    return RedirectResponse(f"/resumes/{resume_id}/dialogue?err={reason}", status_code=303)


@router.post("/{resume_id}/dialogue/reject/{turn_id}")
async def dialogue_reject(resume_id: int, turn_id: int):
    engine.reject(turn_id)
    return RedirectResponse(f"/resumes/{resume_id}/dialogue", status_code=303)
