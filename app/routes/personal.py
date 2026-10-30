"""个人数据库：查看、补全。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ..repo import personal
from ..templating import templates

router = APIRouter()


@router.get("/personal")
async def personal_page(request: Request, msg: str | None = None):
    rows = personal.catalog_rows()
    missing = personal.missing_keys()
    return templates.TemplateResponse(
        request, "personal.html",
        {"active": "personal", "rows": rows, "missing_count": len(missing), "msg": msg},
    )


@router.post("/personal")
async def personal_save(key: str = Form(...), value: str = Form("")):
    if key in personal.FIELD_LABELS and value.strip():
        personal.set_value(key, value, source="manual")
        return RedirectResponse("/personal?msg=已保存", status_code=303)
    return RedirectResponse("/personal", status_code=303)
