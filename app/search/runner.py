"""搜索编排：在后台线程跑，进度写进 progress。"""
from __future__ import annotations

import threading

from . import progress
from .fiftyone import search as search_51job
from .model import infer_job_type


def _score_job(job: dict, doc) -> float:
    """用完整 JD 打分，和导入后岗位详情的匹配用同一份 JD——分数一致。"""
    from ..match import evaluate, semantic

    jd = job.get("description") or " ".join(
        x for x in (job.get("title"), job.get("experience"), job.get("education")) if x
    )
    if not jd.strip():
        return 0.0
    direction = semantic.direction_similarity(doc, jd)
    project = semantic.project_relevance(doc, jd)
    result = evaluate(doc, jd, job_type=infer_job_type(job.get("experience") or ""),
                      direction=direction, project=project)
    return result.total


def _worker(run_id: str, keyword: str, city_code: str, limit: int, resume_id: int | None) -> None:
    try:
        jobs = search_51job(
            keyword,
            city_code=city_code,
            limit=limit,
            progress=lambda step: progress.update(run_id, step=step),
        )
        jobs = [j.as_dict() for j in jobs]

        if resume_id is not None:
            from ..repo import resumes

            doc = resumes.load_doc(resume_id)
            if doc is not None:
                progress.update(run_id, step="按简历匹配打分…")
                for job in jobs:
                    job["score"] = round(_score_job(job, doc), 1)
                jobs.sort(key=lambda j: -j.get("score", 0))

        progress.finish(run_id, jobs)
    except Exception as exc:  # noqa: BLE001 - 任何失败都要反映到状态里
        progress.fail(run_id, f"{type(exc).__name__}: {exc}")


def run_search(keyword: str, city_code: str = "000000", limit: int = 30,
               resume_id: int | None = None) -> str:
    """启动一次搜索，立即返回 run_id。"""
    run_id = progress.create()
    thread = threading.Thread(
        target=_worker, args=(run_id, keyword, city_code, limit, resume_id), daemon=True
    )
    thread.start()
    return run_id
