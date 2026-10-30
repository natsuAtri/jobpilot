"""BM25 关键词检索（内存实现）。

自己写而不是调 FTS5，是为了把「idf、tf、长度归一」这几个概念讲清楚——
这也是作品集里值得展示的一小段。数据量小，O(n) 扫一遍完全够。
"""
from __future__ import annotations

import math
from collections import Counter

from .tokenize import tokenize


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_tokens = [tokenize(d) for d in docs]
        self.doc_len = [len(t) for t in self.doc_tokens]
        self.avgdl = sum(self.doc_len) / len(self.doc_len) if self.doc_len else 0.0
        self.n = len(self.doc_tokens)

        df: Counter[str] = Counter()
        for tokens in self.doc_tokens:
            df.update(set(tokens))
        self.idf = {
            term: math.log(1 + (self.n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def score(self, query: str) -> list[float]:
        query_terms = tokenize(query)
        scores: list[float] = []
        for tokens in self.doc_tokens:
            tf = Counter(tokens)
            doc_len = len(tokens)
            total = 0.0
            for term in query_terms:
                if term not in self.idf:
                    continue
                f = tf.get(term, 0)
                total += self.idf[term] * (f * (self.k1 + 1)) / (
                    f + self.k1 * (1 - self.b + self.b * doc_len / self.avgdl)
                )
            scores.append(total)
        return scores
