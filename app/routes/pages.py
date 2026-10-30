"""页面路由与健康检查。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..config import BASE_DIR
from ..db import get_conn

router = APIRouter()


@router.get("/healthz")
async def healthz() -> JSONResponse:
    with get_conn() as conn:
        conn.execute("SELECT 1").fetchone()
    return JSONResponse({"status": "ok", "base_dir": str(BASE_DIR)})


@router.get("/")
async def index(request: Request):
    return RedirectResponse("/chat", status_code=303)
