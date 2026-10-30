"""RAG 检索冒烟测试。

    python tools/smoke_rag.py 14

验证：语义检索能把「JD 要求」对到「简历里语义相近的 bullet」，
即使两者用词完全不同（这是关键词匹配做不到的）。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.rag.chunk import chunk_resume  # noqa: E402
from app.rag.retrieval import search  # noqa: E402
from app.repo import resumes as repo  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def main(resume_id: int) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    doc = repo.load_doc(resume_id)
    if doc is None:
        print("找不到简历")
        return 2

    chunks = chunk_resume(doc)
    check("简历分块非空", len(chunks) >= 8, f"{len(chunks)} 块")
    check("每块都带上下文标签", all(c.label for c in chunks))

    # 语义检索：用完全不同的措辞去查，检索对象是真实简历里的内容
    cases = [
        ("熟悉深度学习框架，有模型训练经验", ["卷积", "训练", "TensorFlow"]),
        ("有目标检测或图像识别项目经验", ["识别", "YOLO", "手语"]),
        ("有软件测试与文档编写经验", ["单元测试", "文档", "测试"]),
    ]
    for query, keywords in cases:
        hits = search(chunks, query, top_k=3, use_rerank=False)
        top_texts = [h.chunk.text for h in hits]
        matched = any(any(k in t for k in keywords) for t in top_texts)
        check(f"语义检索命中「{query[:16]}…」", matched,
              f"top1={top_texts[0][:30] if top_texts else '无'}")

    # 融合分数合理性：dense + bm25 都在
    hits = search(chunks, "高并发系统设计", top_k=3, use_rerank=False)
    check("混合分数非空", all(h.dense >= 0 and h.bm25 >= 0 for h in hits))

    # rerank（若模型已下载）
    from app.rag import rerank as rerank_mod

    if rerank_mod.available():
        hits = search(chunks, "高并发系统设计", top_k=3, use_rerank=True)
        check("rerank 模型可用并参与排序",
              all(h.rerank is not None for h in hits),
              str([round(h.rerank or 0, 2) for h in hits]))
    else:
        print("SKIP  rerank（模型未下载，稍后可补测）")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 14))
