"""rerank 精排（CrossEncoder）。

embedding 是「双塔」：query 和文档各自独立编码，召回快但排序精度一般；
rerank 是「交叉编码」，把 query 和文档拼在一起过一遍，精度高但慢。
所以标准做法是：embedding 召回一批候选，rerank 只在候选上精排。

优先用更强的 bge-reranker-v2-m3；没有就退回 bge-reranker-base。
"""
from __future__ import annotations

import threading
from pathlib import Path

from ..config import BASE_DIR

_MODEL = None
_MODEL_PATH: Path | None = None
_LOCK = threading.Lock()

RERANK_CANDIDATES: tuple[Path, ...] = (
    BASE_DIR / "models" / "bge-reranker-v2-m3",
    BASE_DIR / "models" / "bge-reranker-base",
)


def _has_weights(path: Path) -> bool:
    return (path / "config.json").exists() and (
        (path / "model.safetensors").exists() or (path / "pytorch_model.bin").exists()
    )


def active_path() -> Path | None:
    """当前可用的 rerank 模型目录（优先 v2-m3）。"""
    for path in RERANK_CANDIDATES:
        if _has_weights(path):
            return path
    return None


def available() -> bool:
    return active_path() is not None


def get_model():
    global _MODEL, _MODEL_PATH
    if _MODEL is None:
        with _LOCK:
            if _MODEL is None:
                path = active_path()
                if path is None:
                    raise RuntimeError("rerank 模型未下载")
                from sentence_transformers import CrossEncoder

                _MODEL = CrossEncoder(str(path))
                _MODEL_PATH = path
    return _MODEL


def active_name() -> str:
    path = _MODEL_PATH or active_path()
    return path.name if path else ""


def rerank(query: str, texts: list[str]) -> list[float]:
    """返回每个候选与 query 的相关度分数（未归一化，只需相对大小）。"""
    if not texts:
        return []
    if not available():
        raise RuntimeError("rerank 模型未下载，请先下载 bge-reranker-v2-m3 或 bge-reranker-base")
    pairs = [[query, text] for text in texts]
    scores = get_model().predict(pairs)
    return [float(s) for s in scores]
