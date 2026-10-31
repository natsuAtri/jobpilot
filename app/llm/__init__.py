"""LLM 层：厂商注册、任务绑定、统一调用。"""
from .tasks import TASK_KEYS, TASKS, get_task

__all__ = ["TASKS", "TASK_KEYS", "get_task"]
