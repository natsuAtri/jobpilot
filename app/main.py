"""应用入口。

开发：python -m uvicorn app.main:app --reload
"""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# 工具注册表必须自洽：少绑一个 handler，模型调用时会静默变成「未知工具」。
# 宁可起不来，也不要带着半张表上线。
from .agent import tools as _agent_tools  # noqa: E402
from .config import STATIC_DIR, ensure_dirs
from .db import migrate
from .routes import apply, chat, dialogue, generate, jobs, pages, personal, resumes
from .templating import ASSET_VERSION

_registry_problems = _agent_tools.validate()
if _registry_problems:
    raise RuntimeError("Agent 工具注册表不完整：" + "；".join(_registry_problems))


def _warmup_models() -> None:
    """后台预加载语义模型，避免第一次搜索/生成时卡 30 秒。

    失败不影响主流程（模型没下载时直接跳过）。
    """
    try:
        from .match.semantic import evidence_available

        if evidence_available():
            from .rag.embed import embed

            embed(["预热"])
        from .rag.rerank import available as rerank_available
        from .rag.rerank import get_model

        if rerank_available():
            get_model()
    except Exception:  # noqa: BLE001 - 预加载失败只是慢一点
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    applied = migrate()
    if applied:
        print(f"[db] 已应用迁移：{', '.join(applied)}")
    from .repo import task_runs

    interrupted = task_runs.mark_interrupted()
    if interrupted:
        print(f"[db] {interrupted} 个未完成的后台任务已标记中断")
    threading.Thread(target=_warmup_models, daemon=True).start()

    # 观测/日志表不能无限长：启动时清一次老数据（后台，不拖慢启动）
    from .repo import maintenance

    threading.Thread(target=maintenance.prune_all, daemon=True).start()
    yield


app = FastAPI(title="JobPilot", version="0.1.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> HTMLResponse:
    """兜底：任何未捕获异常都渲染成可读页面，而不是裸 500。"""
    detail = f"{type(exc).__name__}: {exc}"
    return HTMLResponse(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>出错了 · JobPilot</title><link rel="stylesheet" href="/static/app.css?v={ASSET_VERSION}"><link rel="stylesheet" href="/static/theme.css?v={ASSET_VERSION}"></head>
<body><header class="topbar"><a class="brand" href="/chat">Job<em>Pilot</em></a></header>
<main class="wrap"><h1 class="page-title">操作没有完成</h1>
<div class="banner banner-bad">{detail}</div>
<p class="muted">出错了，但没有丢数据。可以返回上一页重试；如果反复出现，把这段报错发我。</p>
<p><a class="btn" href="/chat">回 Agent</a></p></main></body></html>""",
        status_code=500,
    )


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(pages.router)
app.include_router(resumes.router)
app.include_router(dialogue.router)
app.include_router(jobs.router)
app.include_router(generate.router)
app.include_router(personal.router)
app.include_router(chat.router)
app.include_router(apply.router)
