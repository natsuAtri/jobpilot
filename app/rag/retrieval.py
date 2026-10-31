"""混合检索：dense embedding + BM25 → RRF 融合 → 可选 rerank 精排。

为什么混合：纯 embedding 会把「Java」和「Kotlin」算得近但不该命中，
纯关键词又漏同义表述（JD 写「高并发」，简历写「QPS 提升」）。
两者各取所长，用 RRF 融合。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bm25 import BM25
from .chunk import Chunk
from .embed import embed


@dataclass
class Hit:
    chunk: Chunk
    dense: float
    bm25: float
    fused: float
    rerank: float | None = None

    def as_dict(self) -> dict:
        return {
            **self.chunk.as_dict(),
            "dense": round(self.dense, 4),
            "bm25": round(self.bm25, 4),
            "fused": round(self.fused, 4),
            "rerank": round(self.rerank, 4) if self.rerank is not None else None,
        }


class Retriever:
    """对一个 chunk 集合建立检索索引。"""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.vecs = np.array(
            embed([c.text for c in chunks]), dtype=np.float32
        ) if chunks else np.empty((0, 512), dtype=np.float32)
        self.bm25 = BM25([c.text for c in chunks])

    def dense(self, query: str) -> np.ndarray:
        if self.vecs.shape[0] == 0:
            return np.empty(0, dtype=np.float32)
        q = np.array(embed([query])[0], dtype=np.float32)
        return self.vecs @ q

    def hybrid(self, query: str, top_k: int = 5, rrf_k: int = 60) -> list[Hit]:
        dense = self.dense(query)
        bm = np.array(self.bm25.score(query), dtype=np.float64)
        if len(self.chunks) == 0:
            return []

        fused = _rrf(dense, bm, k=rrf_k)
        order = np.argsort(-fused)[:top_k]
        hits = []
        for i in order:
            hits.append(
                Hit(chunk=self.chunks[int(i)], dense=float(dense[int(i)]),
                    bm25=float(bm[int(i)]), fused=float(fused[int(i)]))
            )
        return hits


def _rrf(dense: np.ndarray, bm: np.ndarray, k: int = 60) -> np.ndarray:
    """Reciprocal Rank Fusion：按各自排名取倒数求和，避免量纲不一致。"""
    dense_rank = _rank(dense)
    bm_rank = _rank(bm)
    return 1.0 / (k + dense_rank) + 1.0 / (k + bm_rank)


def _rank(scores: np.ndarray) -> np.ndarray:
    order = np.argsort(-scores)
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(1, len(order) + 1, dtype=np.float64)
    return ranks


def search(chunks: list[Chunk], query: str, top_k: int = 5, recall: int = 20,
           use_rerank: bool = True) -> list[Hit]:
    """完整检索：混合召回 → rerank 精排。"""
    retriever = Retriever(chunks)
    candidates = retriever.hybrid(query, top_k=min(recall, len(chunks)))

    if not use_rerank or len(candidates) <= top_k:
        return candidates[:top_k]

    from .rerank import rerank

    scores = rerank(query, [h.chunk.text for h in candidates])
    for hit, score in zip(candidates, scores):
        hit.rerank = float(score)
    candidates.sort(key=lambda h: -(h.rerank if h.rerank is not None else h.fused))
    return candidates[:top_k]
