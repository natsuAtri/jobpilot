"""任务定义。

核心设计：不绑厂商，而是「任务 → 模型」。现在统一走 .env 单模型，
task 主要用来带 temperature 等参数。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskSpec:
    key: str
    label: str
    hint: str
    needs_vision: bool = False
    temperature: float = 0.2


TASKS: tuple[TaskSpec, ...] = (
    TaskSpec("resume_parse", "简历结构化精修", "强推理 + 强 JSON", temperature=0.1),
    TaskSpec("resume_strategy", "改写策略", "强推理 + 强 JSON", temperature=0.2),
    TaskSpec("resume_draft", "定制简历起草", "最强推理 + 强 JSON", temperature=0.2),
    TaskSpec("resume_critique", "定制简历评审", "最强推理 + 强 JSON", temperature=0.1),
    TaskSpec("resume_gaps_suggest", "补写建议", "便宜、结构化输出", temperature=0.3),
    TaskSpec("resume_review", "简历整体建议", "便宜、结构化输出", temperature=0.3),
    TaskSpec("fit_judge", "岗位适配判断", "强推理 + 强 JSON", temperature=0.2),
    TaskSpec("intent_route", "意图兜底分类", "便宜、结构化输出", temperature=0.0),
    TaskSpec("dialogue", "对话补录", "中等即可", temperature=0.4),
)

TASK_KEYS = tuple(t.key for t in TASKS)
_BY_KEY = {t.key: t for t in TASKS}


def get_task(key: str) -> TaskSpec:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(f"未知任务：{key}") from None
