"""简历：导入、校对、保存。"""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response

from ..config import EXPORT_DIR, TMP_DIR, UPLOADS_DIR
from ..forms import SECTION_TYPES, apply_form
from ..models import ResumeDoc
from ..parse import extract_pages, extract_rules, render_png
from ..parse.pdf import extract_photo
from ..render import expected_fragments, render_html, verify_pdf
from ..render.browser import BrowserError, print_to_pdf, screenshot_png
from ..repo import personal
from ..repo import resumes as repo
from ..templating import templates

router = APIRouter(prefix="/resumes")

FIELD_RE = re.compile(r"^([simbl]):(\d+)(?::(\d+))?(?::(\d+))?$")



# --------------------------------------------------------------------- 路由
@router.get("")
async def list_page(request: Request, msg: str | None = None):
    return templates.TemplateResponse(
        request, "resumes_list.html",
        {"active": "resume", "items": repo.list_resumes(), "msg": msg},
    )


@router.post("/blank")
async def create_blank():
    """从零创建：建一份空简历，直接进对话补录。"""
    doc = ResumeDoc()
    resume_id = repo.create_resume(doc, kind="generated", title="新简历")
    return RedirectResponse(f"/resumes/{resume_id}/dialogue?mode=scratch", status_code=303)


@router.post("/import")
async def import_pdf(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "只支持 PDF")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    raw = await file.read()
    # 用内容指纹判断「是不是同一份文件」：不同文件 = 不同简历，可以并存
    source_hash = hashlib.sha256(raw).hexdigest()
    stored = UPLOADS_DIR / f"{uuid.uuid4().hex}.pdf"
    stored.write_bytes(raw)

    pages = extract_pages(stored)
    if not pages or not any(p.lines for p in pages):
        return RedirectResponse(
            "/resumes?err=这个 PDF 没有文本层，可能是扫描件（M1 暂不支持）", status_code=303
        )

    doc = extract_rules(pages)
    # 规则只搭骨架；配了模型就让它按视觉行重排（失败自动退回规则结果）
    from ..llm import registry
    from ..parse.refine import refine

    if registry.configured():
        doc = await refine(pages, doc)
    stem = Path(file.filename).stem
    title = f"{doc.name} 的简历" if doc.name else stem
    # 同一份文件重复导入 → 覆盖原来那条（解析器升级后能刷新）；
    # 不同文件 → 新建一份，多份简历并存。
    reuse = repo.find_by_source_hash(source_hash)
    resume_id = repo.create_resume(
        doc, source_file=stored.name, title=title,
        source_hash=source_hash, reuse=reuse,
    )
    if reuse:
        # 覆盖时旧简历派生的对话补充 / 定制结果 / 匹配分全部失效
        repo.reset_derived(resume_id)
    from ..agent import conversation as conv

    chat_state = conv.get_state(conv.get_active())
    if chat_state:
        # 候选的匹配分是按旧简历算的，留着会误导
        chat_state["candidates"] = []
        chat_state["queue"] = []
        conv.set_state(conv.get_active(), chat_state)
    # 同步填充个人数据库，并尝试从 PDF 里提取证件照
    personal.build_from_resume(doc)
    if personal.photo_path() is None:
        found = extract_photo(stored)
        if found:
            personal.set_photo(found[0], found[1], source="resume")
    # 先进入解析复核，而不是直接去编辑——让用户先回答 Agent 的疑问
    msg = ("这份简历之前导入过，已按最新解析结果刷新。"
           if reuse else "简历已导入。先回答下面几个疑点，也可以直接去 Agent 让它帮你找岗位")
    return RedirectResponse(f"/resumes/{resume_id}/dialogue?msg={msg}", status_code=303)


@router.get("/{resume_id}")
async def edit_page(request: Request, resume_id: int, msg: str | None = None):
    doc = repo.load_doc(resume_id)
    if doc is None:
        raise HTTPException(404, "简历不存在")
    with_file = repo.list_resumes()
    row = next((r for r in with_file if r["id"] == resume_id), None)
    return templates.TemplateResponse(
        request,
        "resume_edit.html",
        {
            "active": "resume",
            "resume": row,
            "doc": doc,
            "section_types": list(SECTION_TYPES),
            "page_count": _page_count(row["source_file"]) if row and row["source_file"] else 0,
            "msg": msg,
        },
    )


@router.get("/{resume_id}/page/{index}.png")
async def page_image(resume_id: int, index: int):
    row = next((r for r in repo.list_resumes() if r["id"] == resume_id), None)
    if row is None or not row["source_file"]:
        raise HTTPException(404, "没有原始文件")
    path = UPLOADS_DIR / row["source_file"]
    if not path.exists():
        raise HTTPException(404, "原始文件已丢失")
    try:
        return Response(render_png(path, index, scale=1.6), media_type="image/png")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"渲染失败：{exc}") from exc


@router.post("/{resume_id}")
async def save(request: Request, resume_id: int):
    doc = repo.load_doc(resume_id)
    if doc is None:
        raise HTTPException(404, "简历不存在")

    form = await request.form()
    doc = apply_form(doc, form)
    action_raw = form.get("action")
    action = (action_raw if isinstance(action_raw, str) else "save").strip()

    repo.replace_doc(resume_id, doc)
    repo.update_profile(resume_id, doc)

    if action == "verify":
        repo.mark_verified(resume_id)
        return RedirectResponse(f"/resumes/{resume_id}?msg=已标记为校对完成", status_code=303)
    return RedirectResponse(f"/resumes/{resume_id}?msg=已保存", status_code=303)


@router.get("/{resume_id}/preview")
async def preview_resume(resume_id: int, theme: str = "ink"):
    """简历编辑页左侧的实时预览（HTML 渲染，不转 PDF）。"""
    from fastapi.responses import HTMLResponse

    from ..render.html import render_html

    doc = repo.load_doc(resume_id)
    if doc is None:
        raise HTTPException(404, "简历不存在")
    return HTMLResponse(render_html(doc, theme=theme, photo=personal.photo_data_uri()))


@router.post("/{resume_id}/fill-personal")
async def fill_personal(resume_id: int):
    from ..repo import personal

    doc = repo.load_doc(resume_id)
    if doc is None:
        raise HTTPException(404, "简历不存在")
    n = personal.apply_to_doc(doc)
    if n:
        repo.replace_doc(resume_id, doc)
    return RedirectResponse(f"/resumes/{resume_id}?msg=从个人库补全 {n} 处", status_code=303)


@router.post("/{resume_id}/delete")
async def delete(resume_id: int):
    removed = repo.delete_resume(resume_id)
    msg = "已删除简历"
    if removed:
        msg += f"（连同 {removed} 条投递记录）"
    return RedirectResponse(f"/resumes?msg={msg}", status_code=303)


# --------------------------------------------------------------- 导出与预览
VALID_THEMES = ("ink", "graphite")


def _load_or_404(resume_id: int) -> tuple[ResumeDoc, dict]:
    doc = repo.load_doc(resume_id)
    if doc is None:
        raise HTTPException(404, "简历不存在")
    row = next((r for r in repo.list_resumes() if r["id"] == resume_id), None)
    return doc, row or {}


@router.get("/{resume_id}/preview.png")
async def preview_png(resume_id: int, theme: str = "ink"):
    doc, _ = _load_or_404(resume_id)
    theme = theme if theme in VALID_THEMES else "ink"
    html = render_html(doc, theme=theme, photo=personal.photo_data_uri())
    out = TMP_DIR / f"preview-{resume_id}-{theme}.png"
    try:
        screenshot_png(html, out)
    except BrowserError as exc:
        raise HTTPException(500, str(exc)) from exc
    return FileResponse(out, media_type="image/png")


@router.get("/{resume_id}/export")
async def export_pdf(request: Request, resume_id: int, theme: str = "ink"):
    doc, _ = _load_or_404(resume_id)
    theme = theme if theme in VALID_THEMES else "ink"

    html = render_html(doc, theme=theme, photo=personal.photo_data_uri())
    out = EXPORT_DIR / f"resume-{resume_id}-{theme}.pdf"
    try:
        print_to_pdf(html, out)
    except BrowserError as exc:
        raise HTTPException(500, str(exc)) from exc

    # 硬性门禁：回读校验不通过就不交付，避免导出一份文字被写坏的简历
    result = verify_pdf(out, expected_fragments(doc))
    if not result.ok:
        return templates.TemplateResponse(
            request,
            "export_failed.html",
            {
                "active": "resume",
                "resume": {"id": resume_id},
                "result": result,
                "theme": theme,
            },
            status_code=500,
        )

    filename = f"{doc.name or '简历'}-简历.pdf"
    return FileResponse(
        out,
        media_type="application/pdf",
        filename=filename,
        # 响应头只能是 latin-1，中文说明要编码后再放进去
        headers={"X-Verify": "ok", "X-Verify-Detail": quote(result.message)},
    )


_PAGE_CACHE: dict[str, int] = {}


def _page_count(source_file: str) -> int:
    if source_file in _PAGE_CACHE:
        return _PAGE_CACHE[source_file]
    path = Path(UPLOADS_DIR) / source_file
    if not path.exists():
        return 0
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        count = len(pdf)
    finally:
        pdf.close()
    _PAGE_CACHE[source_file] = count
    return count
