"""技能匹配。

难点不在「字符串相等」，在「说法不同但指同一件事」：
JD 写「深度学习框架」，简历写 TensorFlow；JD 写「关系型数据库」，
简历写 MySQL。所以除了同义词归并，还要有一层「类别」兜底。
"""
from __future__ import annotations

import re

from ..models import ResumeDoc

# 规范名 → 同义写法（全部小写比较）
ALIASES: dict[str, tuple[str, ...]] = {
    "python": ("python",),
    "java": ("java",),
    "c++": ("c++", "cpp", "c＋＋"),
    "c": ("c语言",),
    "go": ("golang", "go语言"),
    "javascript": ("javascript", "js"),
    "typescript": ("typescript", "ts"),
    "sql": ("sql",),
    "mysql": ("mysql",),
    "postgresql": ("postgresql", "postgres", "pg"),
    "redis": ("redis",),
    "mongodb": ("mongodb", "mongo"),
    "linux": ("linux", "unix"),
    "git": ("git", "github", "gitlab"),
    "docker": ("docker", "容器化"),
    "kubernetes": ("kubernetes", "k8s"),
    "tensorflow": ("tensorflow", "tf"),
    "pytorch": ("pytorch", "torch"),
    "yolo": ("yolov8", "yolov5", "yolo"),
    "opencv": ("opencv",),
    "numpy": ("numpy",),
    "pandas": ("pandas",),
    "spring": ("spring", "springboot", "spring boot"),
    "django": ("django",),
    "flask": ("flask",),
    "fastapi": ("fastapi",),
    "react": ("react",),
    "vue": ("vue",),
    "nodejs": ("node.js", "nodejs", "node"),
    "gradio": ("gradio",),
    "spark": ("spark", "pyspark"),
    "hadoop": ("hadoop", "hdfs"),
    "kafka": ("kafka",),
    "elasticsearch": ("elasticsearch", "es"),
    "机器学习": ("机器学习", "machine learning", "ml"),
    "深度学习": ("深度学习", "deep learning", "神经网络", "图神经网络", "cnn", "gcn"),
    "计算机视觉": ("计算机视觉", "cv", "图像识别", "目标检测"),
    "自然语言处理": ("自然语言处理", "nlp"),
    "数据结构": ("数据结构", "算法"),
    "分布式": ("分布式", "微服务", "高并发"),
}

# 同类别之间算「部分匹配」
# 同类别之间算「部分匹配」的类别。
# 只对可替代性强的类别放行：会 TensorFlow 大概率能上手 PyTorch，
# 但会 Docker 不等于会 Kubernetes——工具类必须精确匹配，否则会给出
# 「建议补充」的错误行动项。
PARTIAL_CATEGORIES = {"语言", "框架", "算法"}

CATEGORY: dict[str, str] = {
    "python": "语言", "java": "语言", "c++": "语言", "c": "语言", "go": "语言",
    "javascript": "语言", "typescript": "语言", "sql": "语言",
    "mysql": "数据库", "postgresql": "数据库", "redis": "数据库", "mongodb": "数据库",
    "linux": "工具", "git": "工具", "docker": "工具", "kubernetes": "工具",
    "tensorflow": "框架", "pytorch": "框架", "spring": "框架", "django": "框架",
    "flask": "框架", "fastapi": "框架", "react": "框架", "vue": "框架",
    "nodejs": "框架", "gradio": "框架",
    "yolo": "视觉", "opencv": "视觉", "计算机视觉": "视觉",
    "numpy": "数据", "pandas": "数据", "spark": "数据", "hadoop": "数据",
    "kafka": "数据", "elasticsearch": "数据",
    "机器学习": "算法", "深度学习": "算法", "自然语言处理": "算法",
    "数据结构": "算法", "分布式": "架构",
}


def _alias_pattern(alias: str) -> str:
    """ASCII 别名要加词边界，否则 "es" 会匹配进 "Kubernetes"。"""
    escaped = re.escape(alias)
    if re.fullmatch(r"[A-Za-z0-9+#._/-]+", alias):
        return rf"(?<![A-Za-z0-9+#]){escaped}(?![A-Za-z0-9+#])"
    return escaped


# 匹配时按长度倒序，避免 "c" 抢先吃掉 "c++"
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (canon, re.compile("|".join(_alias_pattern(a) for a in aliases), re.IGNORECASE))
    for canon, aliases in sorted(ALIASES.items(), key=lambda kv: -len(kv[0]))
]


def extract(text: str) -> set[str]:
    """从任意文本里抽出规范化的技能名。"""
    if not text:
        return set()
    found: set[str] = set()
    for canon, pattern in _PATTERNS:
        if pattern.search(text):
            found.add(canon)
    return found


def resume_pool(doc: ResumeDoc) -> set[str]:
    """简历里出现过的全部技能：要点正文、技术栈、技能章节、标签。"""
    chunks: list[str] = []
    for section in doc.sections:
        for item in section.items:
            if item.org:
                chunks.append(item.org)
            if item.role:
                chunks.append(item.role)
            chunks.extend(item.meta.values())
            for bullet in item.bullets:
                chunks.append(bullet.text)
                chunks.extend(bullet.tags)
    return extract(" \n ".join(chunks))


def coverage(required: set[str], pool: set[str]) -> dict[str, list[str]]:
    """把要求拆成 命中 / 部分命中 / 缺失。"""
    hit: list[str] = []
    partial: list[str] = []
    miss: list[str] = []

    pool_categories = {
        CATEGORY.get(s) for s in pool
        if CATEGORY.get(s) in PARTIAL_CATEGORIES
    }
    for skill in sorted(required):
        if skill in pool:
            hit.append(skill)
        elif CATEGORY.get(skill) in pool_categories:
            partial.append(skill)
        else:
            miss.append(skill)
    return {"hit": hit, "partial": partial, "miss": miss}


def ratio(cov: dict[str, list[str]]) -> float:
    """命中算 1，部分命中算 0.5。全缺失返回 0。"""
    total = len(cov["hit"]) + len(cov["partial"]) + len(cov["miss"])
    if total == 0:
        return 0.0
    return (len(cov["hit"]) + 0.5 * len(cov["partial"])) / total
