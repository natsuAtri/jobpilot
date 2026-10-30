"""起草 / 评审 agent 的提示词。"""
from __future__ import annotations

from .facts import whitelist_text


def _strategy_block(strategy: dict | None) -> str:
    if not strategy or not (strategy.get("key_requirements") or strategy.get("focus")):
        return "（无策略，逐条判断是否需要改写）"
    lines = []
    if strategy.get("key_requirements"):
        lines.append("JD 最看重：" + "、".join(strategy["key_requirements"]))
    if strategy.get("strengths"):
        lines.append("你的强项：" + "、".join(strategy["strengths"]))
    if strategy.get("missing"):
        lines.append("你的短板（不要编造）：" + "、".join(strategy["missing"]))
    if strategy.get("focus"):
        lines.append("需要改写的条目：")
        for item in strategy["focus"]:
            lines.append(f"  - {item.get('source_id')}：{item.get('why', '')}")
    return "\n".join(lines)


def _evidence_block(evidence: list[dict] | None) -> str:
    if not evidence:
        return ""
    lines = ["", "# 语义证据（JD 要求 → 简历里对应的经历）"]
    for e in evidence[:8]:
        lines.append(f"JD「{e.get('jd', '')[:60]}」 → 简历「{e.get('resume', '')[:60]}」")
    return "\n".join(lines)


def draft_messages(facts, jd_text: str, opinion: str = "",
                   strategy: dict | None = None,
                   evidence: list[dict] | None = None) -> list[dict[str, str]]:
    whitelist = whitelist_text(facts, with_length=True)
    system = """你是简历改写「起草 agent」。把需要改写的经历要点写得更贴 JD，但**只换说法、不换内容**。

只允许三类改写：
1. 同义替换：「构建」→「搭建」，「制作」→「构建」，「进行训练」→「开展训练」
2. 调整语序：「在开源数据集 MFCAD Dataset 进行训练」→「基于开源数据集 MFCAD Dataset 开展训练」
3. 补齐助词：「在 X 进行训练」→「在 X 上开展训练」

**禁止**（违反任意一条都会被丢弃）：
- 增加任何从句、修饰、成果描述。反例：
  ✗「构建图卷积神经网络」→「设计并构建图卷积神经网络模型，完成模型结构设计与算法选型」
  ✓「构建图卷积神经网络」→「搭建图卷积神经网络」
- 用「并」「以及」「从而」「最终」「同时」引出原文没有的内容
- 新增技术名、工具名、数字、成果
- 抬高责任强度：「参与」不能变「负责」，「负责」不能变「主导」
- 改写后长度超过白名单里标注的「≤ N 字」上限

每一行白名单后面都标了字数上限，必须遵守。**长度超了就等于这条作废。**

只输出 JSON：{"rewrites": [{"source_id": "…", "text": "改写后的句子"}]}
只改写「需要改写的条目」；不写或原样返回表示不改。

# 示例（同一句话）
原文：在开源数据集 MFCAD Dataset 进行训练（18 字，上限 21 字）
✓ 基于开源数据集 MFCAD Dataset 开展训练（19 字）
✗ 在开源数据集 MFCAD Dataset 上进行大规模训练，并通过多轮实验优化模型效果（34 字，超长）"""
    content = (
        f"# 目标岗位 JD\n{jd_text[:800]}\n\n"
        f"# 事实白名单（只能改写这些）\n{whitelist}\n\n"
        f"# 改写策略\n{_strategy_block(strategy)}"
        f"{_evidence_block(evidence)}\n\n"
    )
    if opinion.strip():
        content += f"# 用户的修改意见（最高优先级，但同样不得引入白名单外的事实）\n{opinion.strip()}\n\n"
    content += "请逐条改写。"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


def critique_messages(facts, jd_text: str, rewrites: list[dict],
                      strategy: dict | None = None) -> list[dict[str, str]]:
    whitelist = whitelist_text(facts)
    draft_preview = "\n".join(f"{r.get('source_id')} → {r.get('text')}" for r in rewrites)
    system = """你是简历改写「评审 agent」。对照事实白名单审阅起草结果，做三件事：

1. 查错：改写是否偏离白名单事实（数字变了、名词多出来了、强度抬高了）。
2. 查收益：这条改动是「真的更贴 JD」还是「只换了说法」？只换说法判 noop。
3. 查漏：JD 的某条要求，简历里有对应经历但没体现（放进 missed）。

你绝对不能提议白名单里不存在的事实、技术、数字。

只输出 JSON：
{"reviews": [{"source_id": "…", "verdict": "ok|edit|noop",
              "value": "high|low", "reason": "…", "suggestion": "…"}],
 "missed": [{"requirement": "JD 的哪条要求", "suggestion": "简历哪条经历可以补上"}]}

verdict 含义：ok=可以保留；edit=需要按建议修订；noop=没有收益，应还原原文。"""

    content = (
        f"# 目标岗位 JD\n{jd_text[:800]}\n\n"
        f"# 事实白名单\n{whitelist}\n\n"
    )
    if strategy and strategy.get("key_requirements"):
        content += f"# JD 最看重\n{'、'.join(strategy['key_requirements'])}\n\n"
    content += f"# 起草结果\n{draft_preview}\n\n请逐条评审。"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
