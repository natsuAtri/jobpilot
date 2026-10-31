"""Agent 可用的工具定义（OpenAI function calling 格式）。

只放「只读 / 可重复」的工具。定制简历（启动后台任务）和投递（打开浏览器）
不交给模型自动触发——它们有副作用、会花钱，由规则精确命中，用户显式说才执行。
"""
from __future__ import annotations

from typing import Any

MAX_STEPS = 3

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_jobs",
            "description": "按关键词和城市搜索岗位，返回候选列表。用户说「找工作」「有没有 xx 岗位」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "岗位关键词，如 AI 应用、算法、后端"},
                    "city": {"type": "string", "description": "城市，如 北京、上海；不限就留空"},
                    "min_score": {"type": "integer", "description": "匹配分门槛，用户没说就留空"},
                    "count": {"type": "integer", "description": "用户想要的岗位数量，如「找 3 个」就传 3"},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "more_jobs",
            "description": "翻下一页，找更多同类岗位。用户说「再找几个」「还有吗」时调用。",
            "parameters": {
                "type": "object",
                "properties": {"count": {"type": "integer", "description": "想要几个，默认 5"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exclude_jobs",
            "description": "排除标题里包含指定词的岗位。用户说「不要外包」「排除销售」时调用。",
            "parameters": {
                "type": "object",
                "properties": {"words": {"type": "string", "description": "要排除的词"}},
                "required": ["words"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_job_detail",
            "description": "查看某一个候选岗位的要求、公司、薪资、地点。用户问「第 2 个是什么」时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "单个候选序号"},
                    "indices": {"type": "array", "items": {"type": "integer"},
                                "description": "多个候选序号，如「第二个和第三个」传 [2, 3]"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "judge_fit",
            "description": "判断候选岗位是否适合这份简历（每个岗位消耗一次模型调用）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "单个候选序号"},
                    "indices": {"type": "array", "items": {"type": "integer"},
                                "description": "多个候选序号，如「第二个和第三个」传 [2, 3]"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_candidates",
            "description": "列出当前的候选岗位清单。用户问「现在有什么」「有哪些」时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_advice",
            "description": "针对简历本身给出「该补什么」的建议，不绑定具体岗位（会消耗一次模型调用）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tailor_resume",
            "description": "针对岗位定制简历。注意：这只是向用户提议，不会立即执行；用户确认后才开始。"
                           "一次要定制多个岗位时，把序号放进 indices 一次调用（不要分开调用）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "单个候选序号"},
                    "indices": {"type": "array", "items": {"type": "integer"},
                                "description": "多个候选序号，如 [1, 2]"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_application",
            "description": "把「已经投递了」记录进投递表。用户说「投好了」「已投递」「刚投完」时调用。"
                           "不指定序号时默认记录最近一次打开的那个岗位。这个工具会真正写入数据。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "候选序号，不知道就留空"},
                    "indices": {"type": "array", "items": {"type": "integer"},
                                "description": "一次记录多个岗位"},
                    "note": {"type": "string", "description": "备注，比如渠道或投递方式"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_job",
            "description": "打开某个岗位的投递页。注意：这只是向用户提议，不会立即执行；"
                           "用户必须先确认。只在用户明确说要投递时调用。",
            "parameters": {
                "type": "object",
                "properties": {"index": {"type": "integer", "description": "候选序号，从 1 开始"}},
                "required": ["index"],
            },
        },
    },
]

# 工具名 → 对应意图名（用于提示与统计）
TOOL_INTENT = {
    "search_jobs": "search",
    "more_jobs": "more",
    "exclude_jobs": "exclude",
    "ask_job_detail": "ask_detail",
    "judge_fit": "judge",
    "list_candidates": "status",
    "resume_advice": "advice",
    "tailor_resume": "tailor",
    "apply_job": "apply",
    "record_application": "apply",
}

SYSTEM_PROMPT = """你是 JobPilot，一个本地求职 agent。你能调用工具帮用户找岗位、看岗位、判断适配度、改简历、投递。

用户会先收到一条「当前状态」消息，里面有候选清单、上次动作和简历摘要，请据此理解「这个」「第三个」这类指代。

规则：
1. 需要数据就先调用工具，不要凭空编造岗位或分数。
2. 一次可以调用多个工具（例如先搜索、再对第 1 个判断适配度）。
3. tailor_resume / apply_job 只是「提议」，执行前用户要确认；一次要处理多个岗位时，把序号放进 indices 一次调用，不要把确认拆成两次。
4. 用户说「投好了」「已投递」时，调用 record_application 把投递记下来，不要让用户自己去页面点。
5. 一次问多个岗位（如「第二个和第三个」）时，优先用 indices 参数一次调用。
6. 回答用中文，简洁，可用「·」列点；不要输出 JSON 或工具名。
7. 工具返回"还没有候选"时，引导用户先搜索岗位。
8. 如果用户的问题和找工作无关，直接回答，不要硬套工具。"""
