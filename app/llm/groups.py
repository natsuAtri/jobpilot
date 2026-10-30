# 内部任务 → 分组
# 现在统一走 .env 单模型，分组只保留结构，方便以后恢复「强弱两档」。
TASK_GROUP: dict[str, str] = {
    "resume_parse": "strong",
    "resume_strategy": "strong",
    "resume_draft": "strong",
    "resume_critique": "strong",
    "fit_judge": "strong",
    "resume_gaps_suggest": "cheap",
    "resume_review": "cheap",
    "intent_route": "cheap",
    "dialogue": "cheap",
}


def group_for(task: str) -> str:
    return TASK_GROUP.get(task, "cheap")
