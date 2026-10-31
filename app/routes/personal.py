"""个人数据库：查看、补全、照片。"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from ..repo import personal
from ..templating import templates

router = APIRouter()

ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp"}


@router.get("/personal")
async def personal_page(request: Request, msg: str | None = None):
    rows = personal.catalog_rows()
    missing = personal.missing_keys()
    return templates.TemplateResponse(
        request, "personal.html",
        {
            "active": "personal",
            "rows": rows,
            "missing_count": len(missing),
            "photo_uri": personal.photo_data_uri(),
            "msg": msg,
        },
    )


@router.post("/personal")
async def personal_save(key: str = Form(...), value: str = Form("")):
    if key in personal.FIELD_LABELS and value.strip():
        personal.set_value(key, value, source="manual")
        return RedirectResponse("/personal?msg=已保存", status_code=303)
    return RedirectResponse("/personal", status_code=303)


@router.post("/personal/photo")
async def upload_photo(photo: UploadFile = File(...)):
    if photo.content_type not in ALLOWED_PHOTO_TYPES:
        return RedirectResponse("/personal?msg=照片只支持 JPG / PNG / WebP", status_code=303)

    data = await photo.read()
    if len(data) > 8 * 1024 * 1024:
        return RedirectResponse("/personal?msg=照片不能超过 8MB", status_code=303)

    ext = "png" if photo.content_type == "image/png" else "jpg"
    personal.set_photo(data, ext, source="manual")
    return RedirectResponse("/personal?msg=照片已更新，导出简历时会自动带上", status_code=303)


@router.post("/personal/photo/delete")
async def remove_photo():
    personal.delete_photo()
    return RedirectResponse("/personal?msg=照片已删除", status_code=303)
