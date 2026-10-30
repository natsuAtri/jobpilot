"""bge embedding，进程内单例。

模型只加载一次；encode 时归一化，这样余弦相似度就等于向量点积。
"""
from __future__ import annotations

import threading
from pathlib import Path

from ..config import BASE_DIR

_MODEL = None
_LOCK = threading.Lock()
MODEL_PATH = BASE_DIR / "models" / "bge-small-zh-v1.5"


def model_path() -> Path:
    return MODEL_PATH


def get_model():
    global _MODEL
    if _MODEL is None:
        with _LOCK:
            if _MODEL is None:
                from sentence_transformers import SentenceTransformer

                _MODEL = SentenceTransformer(str(MODEL_PATH))
    return _MODEL


def embed(texts: list[str]) -> list[list[float]]:
    """把一串文本编码成归一化的 512 维向量。"""
    if not texts:
        return []
    vectors = get_model().encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]
