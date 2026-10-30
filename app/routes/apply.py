"""投递：打开岗位页、记录投递、看板。"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..apply import open_job
from ..repo import applications as app_repo
from ..repo import jobs as job_repo
from ..repo import personal
from ..repo import resumes as resume_repo
from ..templating import templates

router = APIRouter()

# BOSS直聘适配后续再做（风控更严）；当前投递目标为前程无忧
JOB51_HOME = "https://we.51job.com"
JOB51_LOGIN = "https://login.51job.com/"


def _job_or_404(job_id: int) -> dict:
    job = job_repo.get_job(job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")
    return job


@router.get("/applications")
async def applications_page(request: Request):
    return templates.TemplateResponse(
        request, "applications.html",
        {"active": "apply", "applications": app_repo.list_applications()},
    )


@router.post("/apply/site-login")
async def site_login():
    """打开前程无忧让用户登录，登录态存进持久化 profile。"""
    ok = open_job(JOB51_LOGIN)
    msg = "已打开前程无忧登录页，请登录" if ok else "启动浏览器失败"
    return RedirectResponse(f"/applications?msg={msg}", status_code=303)


@router.get("/jobs/{job_id}/apply")
async def apply_page(request: Request, job_id: int, resume_id: int | None = None, msg: str | None = None):
    job = _job_or_404(job_id)
    resumes = resume_repo.list_resumes()
    if resume_id is None and resumes:
        resume_id = resumes[0]["id"]
    missing = [
        {"key": k, "label": personal.FIELD_LABELS[k]}
        for k in personal.missing_keys()
    ]
    return templates.TemplateResponse(
        request, "apply.html",
        {"active": "job", "job": job, "resumes": resumes, "resume_id": resume_id,
         "missing": missing, "msg": msg},
    )


@router.post("/jobs/{job_id}/apply/open")
async def open_browser(job_id: int):
    job = _job_or_404(job_id)
    ok = open_job(job.get("url"))
    msg = "已打开浏览器，请完成投递后回来点「已投递」" if ok else "启动浏览器失败，检查 RA_BROWSER 配置"
    return RedirectResponse(f"/jobs/{job_id}/apply?msg={msg}", status_code=303)


@router.post("/jobs/{job_id}/apply/submit")
async def submit(job_id: int, resume_id: int = Form(...), channel: str = Form(""), notes: str = Form("")):
    app_id = app_repo.create_application(job_id, resume_id, channel=channel, notes=notes)
    app_repo.record_submit(app_id, notes)
    return RedirectResponse("/applications?msg=已记录投递", status_code=303)


@router.post("/applications/{app_id}/status")
async def update_status(app_id: int, status: str = Form(...)):
    if status in ("submitted", "viewed", "replied", "interview", "offer", "rejected", "withdrawn"):
        app_repo.set_status(app_id, status)
    return RedirectResponse("/applications", status_code=303)


@router.post("/applications/{app_id}/delete")
async def delete(app_id: int):
    app_repo.delete_application(app_id)
    return RedirectResponse("/applications", status_code=303)
