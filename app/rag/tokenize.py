"""中文/混合文本分词。

不引入 jieba 之类分词库：对检索而言，CJK 用字符二元组（bigram）已经是
可靠的近似，ASCII 直接用词。好处是零依赖、行为可解释，适合放进作品集。
"""
from __future__ import annotations

import re

ASCII_RE = re.compile(r"[A-Za-z0-9+#./_\-]+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []

    # ASCII 词原样保留（Python、TensorFlow、QPS…）
    tokens.extend(t.lower() for t in ASCII_RE.findall(text))

    # CJK 字符二元组
    cjk = "".join(CJK_RE.findall(text))
    tokens.extend(cjk[i:i + 2] for i in range(len(cjk) - 1))

    return tokens
