"""开发用的模型替身。

仅当环境变量 `RA_LLM_STUB=1` 时启用，用于在没有 API Key 的环境里
验证「对话 → patch → 确认 → 落库」整条链路是否通畅。
它不产生任何智能，只按关键词回固定的 JSON。

生产路径永远不会走到这里：不加这个环境变量时，调用直接打到真实厂商。
"""
from __future__ import annotations

import json
import re

PROFILE_RULES = (
    ("西安", "location", "西安"),
    ("北京", "location", "北京"),
    ("上海", "location", "上海"),
    ("深圳", "location", "深圳"),
)

BULLET_HINTS = ("负责", "做了", "实现", "搭建", "完成")


def _last_user_text(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content", "")
            marker = "# 用户刚说"
            if marker in content:
                return content.split(marker, 1)[1].strip()
            return content
    return ""


def dialogue_stub(messages: list[dict[str, str]]) -> dict:
    text = _last_user_text(messages)
    patches: list[dict] = []

    for needle, field, value in PROFILE_RULES:
        if needle in text:
            patches.append({"op": "set_profile", "field": field, "value": value})
            break

    if any(hint in text for hint in BULLET_HINTS):
        bullet = re.sub(r"^(我|在|当时)?", "", text).strip("。；; ")
        if len(bullet) > 4:
            patches.append(
                {"op": "add_bullet", "section": "project", "item": 0, "text": bullet}
            )

    if patches:
        ack = "记下了：" + "；".join(p.get("text") or p.get("value") or "" for p in patches)
        question = "这段经历有具体数字吗？比如数据量、耗时或提升幅度。"
    else:
        ack = "我理解到了，但还不足以写进简历。"
        question = "能具体说一个你做过的项目吗？说清楚做了什么、用了什么技术。"

    return {"ack": ack, "patches": patches, "question": question}


def _original_from(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        content = message.get("content", "")
        if "<<<" in content and ">>>" in content:
            return content.split("<<<", 1)[1].split(">>>", 1)[0].strip()
    return ""


# 同义替换：不改变数字、实体与责任强度，能过四道闸门
SYNONYMS = (
    ("构建", "搭建"),
    ("实现", "完成"),
    ("开发", "研发"),
    ("制作", "构建"),
    ("进行", "开展"),
)


def rewrite_stub(messages: list[dict[str, str]]) -> dict:
    """替身改写：故意留出越界场景，用来验证闸门真的会拦。"""
    original = _original_from(messages)
    if not original:
        return {"rewritten": ""}

    # 越界场景一：凭空加数字
    if "神经网络" in original:
        return {"rewritten": original + "，覆盖 3 万条训练数据"}

    # 越界场景二：把「负责」抬成「主导」
    if "负责" in original:
        return {"rewritten": original.replace("负责", "主导", 1)}

    # 正常场景：同义替换，不该被拦
    for old, new in SYNONYMS:
        if old in original:
            return {"rewritten": original.replace(old, new, 1)}

    return {"rewritten": original}


def _whitelist_from(messages: list[dict[str, str]]) -> list[tuple[str, str]]:
    """从最后一条含「||」白名单行的用户消息里解析 (source_id, text)。"""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        lines = message.get("content", "").splitlines()
        facts = []
        for line in lines:
            if "||" not in line:
                continue
            parts = [p.strip() for p in line.split("||")]
            if len(parts) >= 6:
                facts.append((parts[0], parts[5]))
        if facts:
            return facts
    return []


def draft_stub(messages: list[dict[str, str]]) -> dict:
    """替身起草：故意把「负责」抬成「主导」验证闸门，其余做同义替换。"""
    facts = _whitelist_from(messages)
    rewrites: list[dict] = []
    for source_id, text in facts:
        if "负责" in text:
            rewrites.append({"source_id": source_id, "text": text.replace("负责", "主导", 1)})
            continue
        for old, new in SYNONYMS:
            if old in text:
                rewrites.append({"source_id": source_id, "text": text.replace(old, new, 1)})
                break
        else:
            rewrites.append({"source_id": source_id, "text": text})
    return {"rewrites": rewrites}


def critique_stub(messages: list[dict[str, str]]) -> dict:
    """替身评审：对「主导」抬强的改写给 edit，其余 ok。"""
    reviews: list[dict] = []
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        for line in message.get("content", "").splitlines():
            if "→" not in line:
                continue
            source_id, text = [p.strip() for p in line.split("→", 1)]
            if "主导" in text:
                reviews.append({"source_id": source_id, "verdict": "edit",
                                "reason": "责任强度被抬高", "suggestion": "改回「负责」"})
        break
    return {"reviews": reviews}


def generic_stub(task: str, messages: list[dict]) -> dict:
    if task == "dialogue":
        return dialogue_stub(messages)
    if task == "resume_rewrite":
        return rewrite_stub(messages)
    if task == "resume_draft":
        return draft_stub(messages)
    if task == "resume_critique":
        return critique_stub(messages)
    if task == "agent_tools":
        # 替身不参与工具决策：给出可读的一句话，别把 JSON 甩给用户
        return {"reply": "（替身模型：只能处理精确指令，请不要用它验证 agent 分支。）"}
    return {"ack": "", "patches": [], "question": "（替身模型：该任务未实现）"}


def as_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)
