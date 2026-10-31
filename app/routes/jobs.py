"""岗位：导入、列表、匹配、搜索。"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..match import evaluate, semantic
from ..repo import jobs as job_repo
from ..repo import resumes as resume_repo
from ..search import progress as search_progress
from ..search import run_search
from ..search.model import infer_job_type
from ..templating import templates

router = APIRouter(prefix="/jobs")

SALARY_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[-–~至]\s*(\d+(?:\.\d+)?)\s*万")
MONTHS = re.compile(r"·\s*(\d{1,2})\s*薪")


def _parse_salary(text: str) -> tuple[int | None, int | None, int | None]:
    """'1.2-1.8万·13薪' → (12000, 18000, 13)。"""
    months = None
    m = MONTHS.search(text)
    if m:
        months = int(m.group(1))
    r = SALARY_RANGE.search(text)
    if not r:
        return None, None, months
    return int(float(r.group(1)) * 10000), int(float(r.group(2)) * 10000), months


def _job_or_404(job_id: int) -> dict:
    job = job_repo.get_job(job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")
    return job


@router.get("")
async def list_page(request: Request, msg: str | None = None):
    """岗位列表页已下线：找岗位、看分数都走 Agent。"""
    return RedirectResponse("/chat", status_code=303)


# --------------------------------------------------------------- 搜索（须在 /{job_id} 之前注册）
@router.get("/search")
async def search_page(
    request: Request, run: str | None = None, keyword: str = "", city: str = "",
    resume_id: str = "",
):
    resumes = resume_repo.list_resumes()
    return templates.TemplateResponse(
        request, "search.html",
        {
            "active": "job", "resumes": resumes, "run": run,
            "keyword": keyword, "city": city, "resume_id": resume_id,
        },
    )


@router.post("/search/start")
async def search_start(
    keyword: str = Form(...), city: str = Form(""), limit: int = Form(20),
    resume_id: str = Form(""),
):
    from ..search.cities import code_for

    city_code = code_for(city)
    rid = int(resume_id) if resume_id.isdigit() else None
    run_id = run_search(keyword.strip(), city_code=city_code, limit=limit, resume_id=rid)
    return RedirectResponse(
        f"/jobs/search?run={run_id}&keyword={keyword}&city={city}&resume_id={resume_id}",
        status_code=303,
    )


@router.get("/search/status/{run_id}")
async def search_status(run_id: str):
    state = search_progress.get(run_id)
    if state is None:
        return JSONResponse({"status": "error", "error": "未知的运行 ID"})
    return JSONResponse(state)


@router.post("/search/import")
async def search_import(request: Request):
    form = await request.form()
    raw = form.get("jobs")
    raw = raw if isinstance(raw, str) else "[]"
    try:
        jobs = json.loads(raw)
    except json.JSONDecodeError:
        return RedirectResponse("/jobs?msg=导入数据格式错误", status_code=303)

    imported = 0
    skipped = 0
    for j in jobs:
        if not isinstance(j, dict) or not j.get("title"):
            continue
        salary_min, salary_max, months = _parse_salary(j.get("salary") or "")
        city = (j.get("location") or "").split("·")[0] if j.get("location") else ""
        dedupe_key = f"51job:{j.get('source_id')}" if j.get("source_id") else None
        if dedupe_key and job_repo.find_by_dedupe_key(dedupe_key):
            skipped += 1
            continue
        job_repo.create_job(
            title=j["title"],
            company=j.get("company") or "",
            city=city,
            url=j.get("url") or "",
            platform="51job",
            job_type=infer_job_type(j.get("experience") or ""),
            salary_min=salary_min, salary_max=salary_max, salary_months=months,
            external_id=j.get("source_id") or None,
            dedupe_key=dedupe_key,
            description=j.get("description") or "",
        )
        imported += 1
    msg = f"已导入 {imported} 个岗位"
    if skipped:
        msg += f"，{skipped} 个已存在自动跳过"
    return RedirectResponse(f"/jobs?msg={msg}", status_code=303)


@router.post("")
async def create(
    title: str = Form(...),
    company: str = Form(""),
    city: str = Form(""),
    url: str = Form(""),
    job_type: str = Form("unknown"),
    description: str = Form(...),
):
    if not title.strip() or not description.strip():
        return RedirectResponse("/jobs?msg=标题和 JD 正文都要填", status_code=303)
    job_type = job_type if job_type in ("campus", "social", "intern") else "unknown"
    job_id = job_repo.create_job(
        title=title, company=company, city=city, url=url,
        description=description, job_type=job_type,
    )
    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@router.get("/{job_id}")
async def detail(request: Request, job_id: int, resume_id: int | None = None):
    job = _job_or_404(job_id)
    resumes = resume_repo.list_resumes()

    if resume_id is None and resumes:
        resume_id = resumes[0]["id"]

    match = None
    doc = None
    if resume_id:
        doc = resume_repo.load_doc(resume_id)
        match = job_repo.get_match(job_id, resume_id)

    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "active": "job",
            "job": job,
            "resumes": resumes,
            "resume_id": resume_id,
            "doc": doc,
            "match": match,
        },
    )


@router.post("/{job_id}/match")
async def run_match(job_id: int, resume_id: int = Form(...)):
    job = _job_or_404(job_id)
    doc = resume_repo.load_doc(resume_id)
    if doc is None:
        raise HTTPException(404, "简历不存在")

    # 语义证据 + 方向一致性：有 JD 正文且 bge 就位时才做（否则降级，不阻塞）
    evidence: list = []
    direction = None
    project = None
    if job.get("description_raw") and semantic.evidence_available():
        try:
            direction = semantic.direction_similarity(doc, job["description_raw"])
            project = semantic.project_relevance(doc, job["description_raw"])
            evidence = semantic.semantic_evidence(doc, job["description_raw"])
        except Exception as exc:  # noqa: BLE001 - 模型环境坏了也要出分，不能 500
            print(f"[match] 语义匹配不可用，降级为关键词打分：{type(exc).__name__}: {exc}")

    result = evaluate(doc, job["description_raw"] or "",
                      job_type=job.get("job_type") or "unknown",
                      direction=direction, project=project)

    # 语义维度没跑出来时，分数只剩关键词/年限/语言三项，会明显偏高——必须说清楚
    if job.get("description_raw") and semantic.degraded():
        result.notes.append(
            "语义模型未就绪，本次只用「关键词 / 年限 / 语言」三项打分，分数会偏高。"
            "修复环境后重新匹配即可（运行 python tools/check_env.py 自查）。"
        )

    job_repo.save_match(job_id, resume_id, result, evidence=evidence)
    return RedirectResponse(f"/jobs/{job_id}?resume_id={resume_id}", status_code=303)


@router.post("/{job_id}/delete")
async def delete(job_id: int):
    job_repo.delete_job(job_id)
    return RedirectResponse("/jobs", status_code=303)
