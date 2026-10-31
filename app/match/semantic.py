"""语义匹配：用 RAG 检索把 JD 要求和简历片段对齐，产出可解释证据。

和关键词匹配互补：关键词命中告诉你「写没写」，语义检索告诉你
「这条 JD 要求和简历里哪段经历对应」。二者结合起来，匹配才有解释力。

关键约束：证据必须相关。embedding 会把「都是技术文本」也算得近，
所以用 rerank（交叉编码）做精排，再套一个相关性阈值——低于阈值的
说明简历里没有对应经历，宁可不给证据也不硬凑。
"""
from __future__ import annotations

from ..models import ResumeDoc
from ..rag.chunk import chunk_jd, chunk_resume
from ..rag.retrieval import search

MAX_JD_CHUNKS = 10

# 相关性阈值（rerank 原始 logit；dense 回退用余弦）。
# bge-reranker-base 的 logit 未经校准：实测无关配对 ~0.00-0.02，相关 ~0.07+。
RERANK_THRESHOLD = 0.05
DENSE_THRESHOLD = 0.35

_warned = False
_degraded = False


def degraded() -> bool:
    """语义能力是否因模型不可用而降级过（供页面提示用）。"""
    return _degraded


def _warn_degraded(exc: BaseException) -> None:
    """模型不可用时只提示一次——否则每次打分都刷屏。

    降级本身不可怕，可怕的是用户不知道为什么分数变了。
    """
    global _warned, _degraded
    _degraded = True
    if _warned:
        return
    _warned = True
    print(
        "[semantic] 语义模型不可用，匹配已降级为关键词打分"
        f"（{type(exc).__name__}: {exc}）。\n"
        "           常见原因：当前 Python 环境的 transformers 版本与"
        " sentence-transformers 不匹配。\n"
        "           解决：在启动应用的同一个环境里执行 "
        "pip install -r requirements.txt -r requirements-rag.txt"
    )


def semantic_evidence(doc: ResumeDoc, jd_text: str, top_k: int = 1) -> list[dict]:
    """对 JD 每个「要求」片段，找简历里语义最相近、且超过阈值的 bullet。"""
    try:
        return _semantic_evidence(doc, jd_text, top_k)
    except Exception as exc:  # noqa: BLE001 - 模型挂了也不能让打分 500
        _warn_degraded(exc)
        return []


def _semantic_evidence(doc: ResumeDoc, jd_text: str, top_k: int = 1) -> list[dict]:
    resume_chunks = chunk_resume(doc)
    jd_chunks = chunk_jd(jd_text)  # 已剔除「介绍/职责」类段落
    if not resume_chunks or not jd_chunks:
        return []

    from ..rag.rerank import available as rerank_available

    evidence: list[dict] = []
    for jc in jd_chunks[:MAX_JD_CHUNKS]:
        hits = search(
            resume_chunks, jc.text, top_k=top_k, recall=10,
            use_rerank=rerank_available(),
        )
        if not hits:
            continue
        hit = hits[0]
        # 相关性：有 rerank 用原始 logit，否则用 dense 余弦
        if hit.rerank is not None:
            score = float(hit.rerank)
            if score < RERANK_THRESHOLD:
                continue
        else:
            score = float(hit.dense)
            if score < DENSE_THRESHOLD:
                continue
        evidence.append({
            "jd": jc.text,
            "resume": hit.chunk.text,
            "label": hit.chunk.label,
            "score": round(score, 3),
        })
    return evidence


def evidence_available() -> bool:
    """bge 模型是否就位（未下载时语义匹配直接跳过，不报错）。"""
    from ..rag.embed import model_path

    return model_path().exists()


# ---------------------------------------------------------------- 项目对口度
_PROJECT_CACHE: dict[str, list[list[float]]] = {}


def project_relevance(doc: ResumeDoc, jd_text: str) -> float | None:
    """JD 的每条要求，简历里有没有对应的项目经历——这才是「对口度」。

    比关键词覆盖更有意义：会几门语言不重要，做过的项目和岗位要求
    对不对得上才重要。
    """
    from ..rag.chunk import chunk_jd, chunk_resume
    from ..rag.embed import embed

    if not evidence_available():
        return None
    resume_chunks = chunk_resume(doc)
    jd_chunks = chunk_jd(jd_text)
    if not resume_chunks or not jd_chunks:
        return None

    key = "|".join(c.text for c in resume_chunks)[:400]
    try:
        if key not in _PROJECT_CACHE:
            if len(_PROJECT_CACHE) > 2:
                _PROJECT_CACHE.clear()
            _PROJECT_CACHE[key] = embed([c.text for c in resume_chunks])
        resume_vecs = _PROJECT_CACHE[key]
        jd_vecs = embed([c.text for c in jd_chunks])
    except Exception as exc:  # noqa: BLE001 - 模型异常不影响主流程
        _warn_degraded(exc)
        return None

    total = 0.0
    for jd_vec in jd_vecs:
        best = max(
            sum(a * b for a, b in zip(jd_vec, resume_vec))
            for resume_vec in resume_vecs
        )
        # bge 余弦：对口 ~0.6-0.85，无关 ~0.3-0.5，映射到 0..1
        total += max(0.0, min(1.0, (best - 0.45) / 0.35))
    return total / len(jd_vecs)


# ---------------------------------------------------------------- 方向一致性
_DIRECTION_CACHE: dict[str, list[float]] = {}


def _resume_direction_text(doc: ResumeDoc) -> str:
    parts: list[str] = []
    for section in doc.sections:
        if section.type == "skill":
            parts.extend(it.org for it in section.items if it.org)
        parts.extend(it.org for it in section.items if it.org and section.type in ("project", "experience"))
    parts.extend(b.text for section in doc.sections for item in section.items
                 for b in item.bullets if b.text.strip())
    return " ".join(parts)[:1000]


def direction_similarity(doc: ResumeDoc, jd_text: str) -> float | None:
    """简历方向 vs 岗位方向的语义相似度，归一化到 0..1。

    用来识别「标题党岗位」：设备工程师岗就算 JD 里出现 Python，
    方向也和算法简历对不上。
    """
    from ..rag.chunk import requirement_text
    from ..rag.embed import embed

    if not evidence_available():
        return None
    resume_text = _resume_direction_text(doc)
    jd_req = requirement_text(jd_text)[:800] or jd_text[:800]
    if not resume_text.strip() or not jd_req.strip():
        return None

    key = resume_text[:200]
    try:
        if key not in _DIRECTION_CACHE:
            if len(_DIRECTION_CACHE) > 3:
                _DIRECTION_CACHE.clear()
            _DIRECTION_CACHE[key] = embed([resume_text])[0]
        resume_vec = _DIRECTION_CACHE[key]
        jd_vec = embed([jd_req])[0]
    except Exception as exc:  # noqa: BLE001 - 模型异常不影响主流程
        _warn_degraded(exc)
        return None

    cosine = float(sum(a * b for a, b in zip(resume_vec, jd_vec)))
    # bge 的余弦通常落在 0.3~0.85，映射到 0..1 才有区分度
    return max(0.0, min(1.0, (cosine - 0.30) / 0.45))
