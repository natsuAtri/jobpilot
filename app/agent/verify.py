"""回复一致性校验：模型说的话，状态里得对得上。

为什么不用「让模型再评一遍自己的回答」：
- 多一次调用、多花钱，而且模型对自己的输出往往过度肯定
- 真正会出错的是**硬事实**：编造不存在的分数、引用不存在的序号

所以这里只做确定性检查，零成本、可复现：
1. 回复里出现的「xx.x 分」必须能在候选里找到
2. 回复里出现的「第 N 个」不能超出候选数量
3. 声称「已定制/已打开」时，状态里必须真有对应动作

发现问题不篡改回复，只记 trace —— 用来发现问题、积累数据，而不是掩盖。
"""
from __future__ import annotations

import re

SCORE_RE = re.compile(r"(\d{1,3}(?:\.\d)?)\s*分")
# 回复声称「已做过某事」时，这一轮必须真有对应动作
_CLAIMS = (
    (re.compile(r"已记录|已标记为已投递"), "record"),
    (re.compile(r"提交定制任务|开始为.*生成定制简历"), "tailor"),
    (re.compile(r"已在你的登录态浏览器里打开"), "apply_open"),
)
INDEX_RE = re.compile(r"第\s*([一二三四五六七八九十\d]{1,3})\s*个")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

ALLOWED_EXTRA_SCORES = {100.0, 0.0}   # 阈值/满分这类约定值


def _scores_of(state: dict) -> set[float]:
    out: set[float] = set()
    for cand in state.get("candidates") or []:
        try:
            out.add(round(float(cand.get("score") or 0), 1))
        except (TypeError, ValueError):
            continue
    return out


def check_reply(reply: str, state: dict) -> list[str]:
    """返回问题列表；空列表表示没问题。"""
    issues: list[str] = []
    text = reply or ""
    candidates = state.get("candidates") or []

    # 1) 分数必须是真实存在的候选分数（或 0/100 这类约定值）
    known = _scores_of(state) | ALLOWED_EXTRA_SCORES
    for raw in SCORE_RE.findall(text):
        try:
            value = round(float(raw), 1)
        except ValueError:
            continue
        if value not in known and not any(abs(value - k) < 0.05 for k in known):
            issues.append(f"回复里的「{raw} 分」不在候选分数里")

    # 2) 声称的动作必须真的发生过
    performed = {item.get("action") for item in (state.get("performed") or [])}
    for pattern, action in _CLAIMS:
        if pattern.search(text) and action not in performed:
            issues.append(f"回复声称「{action}」，但这一轮没有对应动作")

    # 3) 「第 N 个」不能越界
    for raw in INDEX_RE.findall(text):
        try:
            index = int(raw)
        except ValueError:
            index = CN_NUM.get(raw, 0)
        if index and candidates and index > len(candidates):
            issues.append(f"回复引用了第 {index} 个，但只有 {len(candidates)} 个候选")

    return issues


def describe(issues: list[str]) -> str:
    return "；".join(issues)
