"""对话式 agent 的路由。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..agent import chat as chat_agent
from ..agent import conversation as conv
from ..repo import resumes as resume_repo
from ..templating import templates

router = APIRouter()


def _public_candidates(conversation_id: int) -> list[dict]:
    """给前端渲染候选卡片用的精简数据（含 JD，供「查看详情」展开）。"""
    state = conv.get_state(conversation_id)
    out = []
    for index, item in enumerate(state.get("candidates") or [], start=1):
        out.append({
            "index": index,
            "cid": item.get("cid"),
            "title": item.get("title") or "",
            "company": item.get("company") or "",
            "location": item.get("location") or "",
            "score": item.get("score"),
            "salary": item.get("salary") or "",
            "experience": item.get("experience") or "",
            "url": item.get("url") or "",
            "jd": item.get("description") or "",
        })
    return out


@router.get("/chat")
async def chat_page(request: Request):
    conversation_id = conv.get_active()
    resumes = resume_repo.list_resumes()
    return templates.TemplateResponse(
        request, "chat.html",
        {
            "active": "chat",
            "messages": conv.list_messages(conversation_id),
            "candidates": _public_candidates(conversation_id),
            "resume_id": resumes[0]["id"] if resumes else None,
            "has_resume": bool(resumes),
        },
    )


async def _run_turn(conversation_id: int, text: str, resume_id: int) -> dict:
    conv.add_message(conversation_id, "user", text)
    # agent 的处理里有同步 Playwright 调用，必须放到线程池里跑，
    # 不能在 asyncio 事件循环里直接执行。
    result = await asyncio.to_thread(chat_agent.handle, conversation_id, text, resume_id)
    conv.add_message(
        conversation_id, "agent", result["reply"],
        {"intent": result["intent"], "suggestions": result.get("suggestions", [])},
    )
    return {
        "reply": result["reply"],
        "intent": result["intent"],
        "suggestions": result.get("suggestions", []),
        "candidates": _public_candidates(conversation_id),
    }


@router.post("/chat/send")
async def chat_send_json(text: str = Form(...), resume_id: int = Form(...)):
    """前端 fetch 用：立即返回 JSON，由前端追加消息（不整页刷新）。"""
    text = text.strip()
    if not text:
        return JSONResponse({"reply": "", "suggestions": [], "candidates": []})
    conversation_id = conv.get_active()
    return JSONResponse(await _run_turn(conversation_id, text, resume_id))


@router.post("/chat")
async def chat_send(text: str = Form(...), resume_id: int = Form(...)):
    """无 JS 时的回退：整页提交。"""
    text = text.strip()
    conversation_id = conv.get_active()
    if text:
        await _run_turn(conversation_id, text, resume_id)
    return RedirectResponse("/chat", status_code=303)


@router.post("/chat/clear")
async def chat_clear():
    conversation_id = conv.get_active()
    conv.clear_messages(conversation_id)
    conv.set_state(conversation_id, {})
    return RedirectResponse("/chat", status_code=303)


@router.get("/agent")
async def legacy_agent():
    """旧的一键 Agent 页已并入对话，保留重定向避免老链接 404。"""
    return RedirectResponse("/chat", status_code=303)
