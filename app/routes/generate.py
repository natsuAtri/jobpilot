"""双 agent 生成：触发、过程可视化、终审、落地。"""
from __future__ import annotations

import asyncio
import threading

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..generate import engine
from ..generate import progress as gen_progress
from ..repo import generations as gen_repo
from ..repo import jobs as job_repo
from ..repo import resumes as resume_repo
from ..templating import templates

router = APIRouter(prefix="/jobs")


def _job_or_404(job_id: int) -> dict:
    job = job_repo.get_job(job_id)
    if job is None:
        raise HTTPException(404, "岗位不存在")
    return job


def _back(job_id: int, resume_id: int) -> RedirectResponse:
    return RedirectResponse(f"/jobs/{job_id}/generate?resume_id={resume_id}", status_code=303)


# ---------------------------------------------------------------- 后台生成
def _worker(run_id: str, job_id: int, resume_id: int, opinion: str) -> None:
    try:
        job = job_repo.get_job(job_id)
        doc = resume_repo.load_doc(resume_id)
        if job is None or doc is None:
            gen_progress.fail(run_id, "岗位或简历不存在")
            return
        jd = job["description_raw"] or ""

        # 语义证据 + 匹配差距（增强项，失败不影响生成）
        evidence: list[dict] = []
        gaps: list[dict] = []
        try:
            from ..match import evaluate, semantic

            if semantic.evidence_available():
                evidence = semantic.semantic_evidence(doc, jd)
            gaps = evaluate(doc, jd, job_type=job.get("job_type") or "unknown").gaps
        except Exception:  # noqa: BLE001
            evidence, gaps = [], []

        db_run_id = gen_repo.get_or_create_run(resume_id, job_id)
        # 断点续跑：上次中断时已经完成的步骤直接复用，不重复烧 token
        steps = gen_repo.load_steps(db_run_id)
        if steps:
            gen_progress.add_step(run_id, "resume", f"发现上次的 {len(steps)} 个检查点，继续生成")

        def on_step(step: str, payload) -> None:
            gen_repo.save_step(db_run_id, step, payload)

        result = asyncio.run(
            engine.generate(
                doc, jd,
                opinion=opinion,
                trace=gen_progress.make_trace(run_id),
                evidence=evidence,
                gaps=gaps,
                cache=steps,
                on_step=on_step,
            )
        )
        gen_repo.replace_items(db_run_id, result.proposals)
        gen_repo.save_suggestions(db_run_id, result.suggestions)
        gen_repo.clear_steps(db_run_id)
        gen_progress.finish(run_id, f"已保存 {len(result.proposals)} 条候选，去终审页处理")
    except Exception as exc:  # noqa: BLE001 - 任何失败都要反映到页面
        gen_progress.fail(run_id, f"{type(exc).__name__}: {exc}")


def _start(job_id: int, resume_id: int, opinion: str = "") -> str:
    run_id = gen_progress.create()
    threading.Thread(
        target=_worker, args=(run_id, job_id, resume_id, opinion), daemon=True
    ).start()
    return run_id


def _wants_json(request: Request) -> bool:
    """页面里的按钮用 fetch 提交，返回 JSON 就地更新，避免整页刷新。"""
    return request.headers.get("x-requested-with") == "fetch"


def _state_payload(job_id: int, resume_id: int) -> dict:
    run = gen_repo.find_run(resume_id, job_id)
    items = gen_repo.list_items(int(run["id"])) if run else []
    accepted = sum(1 for i in items if i.get("accepted") == 1 or i.get("manual"))
    return {
        "ok": True,
        "accepted": accepted,
        "items": [
            {
                "id": i["id"],
                "accepted": i["accepted"],
                "manual": i["manual"],
                "blocked": i["blocked"],
            }
            for i in items
        ],
    }


@router.get("/{job_id}/generate")
async def review_page(request: Request, job_id: int, resume_id: int | None = None,
                      err: str | None = None, run: str | None = None):
    job = _job_or_404(job_id)
    resumes = resume_repo.list_resumes()

    # 生成中：展示双 agent 的实时过程
    if run:
        state = gen_progress.get(run)
        if state is not None:
            return templates.TemplateResponse(
                request, "generate_progress.html",
                {"active": "job", "job": job, "resume_id": resume_id,
                 "run_id": run, "state": state},
            )

    if not resumes:
        return templates.TemplateResponse(
            request, "generate_review.html",
            {"active": "job", "job": job, "resumes": [], "resume_id": None,
             "items": [], "run": None, "err": err},
        )
    if resume_id is None:
        resume_id = resumes[0]["id"]
    db_run = gen_repo.find_run(resume_id, job_id)
    items = gen_repo.list_items(int(db_run["id"])) if db_run else []
    # 高价值在前，仅措辞在后；被拦截的仍保留位置，由模板置灰
    priority_order = {"high": 0, "normal": 1, "low": 2}
    items.sort(key=lambda i: priority_order.get(i.get("priority") or "normal", 1))
    accepted = sum(1 for i in items if i.get("accepted") == 1 or i.get("manual"))
    suggestions = gen_repo.get_suggestions(int(db_run["id"])) if db_run else []
    # 上次生成中断留下的检查点：提示用户重新生成会从断点继续
    pending_steps = len(gen_repo.load_steps(int(db_run["id"]))) if db_run else 0
    return templates.TemplateResponse(
        request, "generate_review.html",
        {
            "active": "job", "job": job, "resumes": resumes, "resume_id": resume_id,
            "run": db_run, "items": items, "accepted": accepted,
            "suggestions": suggestions, "err": err,
            "pending_steps": pending_steps,
        },
    )


@router.get("/{job_id}/generate/status/{run_id}")
async def generate_status(run_id: str):
    state = gen_progress.get(run_id)
    return JSONResponse(state or {"status": "unknown", "steps": []})


@router.post("/{job_id}/generate")
async def trigger(job_id: int, resume_id: int = Form(...)):
    _job_or_404(job_id)
    run_id = _start(job_id, resume_id)
    return RedirectResponse(
        f"/jobs/{job_id}/generate?resume_id={resume_id}&run={run_id}", status_code=303
    )


@router.post("/{job_id}/generate/opinion")
async def opinion(job_id: int, resume_id: int = Form(...), opinion: str = Form(...)):
    _job_or_404(job_id)
    run_id = _start(job_id, resume_id, opinion=opinion)
    return RedirectResponse(
        f"/jobs/{job_id}/generate?resume_id={resume_id}&run={run_id}", status_code=303
    )


@router.post("/{job_id}/generate/accept-all")
async def accept_all(request: Request, job_id: int, resume_id: int = Form(...)):
    run = gen_repo.find_run(resume_id, job_id)
    if run is not None:
        gen_repo.accept_all_passed(int(run["id"]))
    if _wants_json(request):
        return JSONResponse(_state_payload(job_id, resume_id))
    return _back(job_id, resume_id)


@router.post("/{job_id}/generate/item/{item_id}/accept")
async def accept_item(request: Request, job_id: int, item_id: int, resume_id: int = Form(...)):
    gen_repo.set_accepted(item_id, 1)
    if _wants_json(request):
        return JSONResponse(_state_payload(job_id, resume_id))
    return _back(job_id, resume_id)


@router.post("/{job_id}/generate/item/{item_id}/reject")
async def reject_item(request: Request, job_id: int, item_id: int, resume_id: int = Form(...)):
    gen_repo.set_accepted(item_id, 0)
    if _wants_json(request):
        return JSONResponse(_state_payload(job_id, resume_id))
    return _back(job_id, resume_id)


@router.post("/{job_id}/generate/item/{item_id}/edit")
async def edit_item(request: Request, job_id: int, item_id: int, resume_id: int = Form(...),
                      text: str = Form(...)):
    gen_repo.set_manual(item_id, text)
    if _wants_json(request):
        return JSONResponse(_state_payload(job_id, resume_id))
    return _back(job_id, resume_id)


@router.post("/{job_id}/generate/suggest/regen")
async def regen_suggestions(job_id: int, resume_id: int = Form(...)):
    """单独重跑「优化建议」——生成时模型偶尔会失败，给个补跑入口。"""
    job = _job_or_404(job_id)
    run = gen_repo.find_run(resume_id, job_id)
    doc = resume_repo.load_doc(resume_id)
    if run is None or doc is None:
        return RedirectResponse(f"/jobs/{job_id}/generate?resume_id={resume_id}", status_code=303)

    from ..generate import suggest as suggest_mod

    suggestions = await suggest_mod.suggest(doc, job.get("description_raw") or "")
    gen_repo.save_suggestions(int(run["id"]), suggestions)
    return RedirectResponse(f"/jobs/{job_id}/generate?resume_id={resume_id}", status_code=303)


@router.post("/{job_id}/generate/suggest/add")
async def add_suggestion(job_id: int, resume_id: int = Form(...), title: str = Form(...),
                         how: str = Form(""), section: str = Form("project")):
    _job_or_404(job_id)
    resume_repo.add_suggested_item(resume_id, section, title, how)
    return RedirectResponse(f"/resumes/{resume_id}?msg=已添加建议条目，记得填实内容", status_code=303)


@router.post("/{job_id}/generate/save")
async def save_tailored(job_id: int, resume_id: int = Form(...)):
    job = _job_or_404(job_id)
    run = gen_repo.find_run(resume_id, job_id)
    if run is None:
        return _back(job_id, resume_id)

    items = gen_repo.list_items(int(run["id"]))
    doc = resume_repo.load_doc(resume_id)
    if doc is None:
        raise HTTPException(404, "简历不存在")

    changed = engine.apply_items_to_doc(doc, items)
    if changed == 0:
        return RedirectResponse(
            f"/jobs/{job_id}/generate?resume_id={resume_id}&err=没有接受任何改动", status_code=303
        )

    new_id = resume_repo.create_resume(
        doc, kind="tailored", title=f"{doc.name} · {job['title']}",
        base_resume_id=resume_id,
    )
    gen_repo.mark_saved(int(run["id"]))
    return RedirectResponse(f"/resumes/{new_id}?msg=已生成定制版，共改动 {changed} 条", status_code=303)
