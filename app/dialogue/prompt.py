"""对话提示词的构造。

上下文预算按设计文档 §13 控制：只放「简历摘要 + 最近几轮 + 缺口清单」，
不塞原始聊天记录，也不塞 PDF 原文。
"""
from __future__ import annotations

from ..models import SECTION_LABELS, ResumeDoc
from .gaps import GapReport

# 简历摘要的压缩参数
MAX_BULLET_CHARS = 60
MAX_ITEMS_PER_SECTION = 6
MAX_SECTIONS = 8
HISTORY_TURNS = 5

SYSTEM = """你是中文求职简历助理。用户会零散地告诉你经历，你要把它变成简历内容。

规则：
1. 每次只问一个问题，问最关键的那一个，不要连环追问。
2. 只能写用户明确说过的内容。用户没说的，不要补，不要猜，尤其不要编造数字。
3. 数字、公司名、学校名、时间必须原样使用用户给的表述。
4. 用户一句话里包含多个事实时，可以拆成多个 patch。
5. 如果用户这句话还不足以形成任何简历内容，patches 返回空数组，只提一个更具体的问题。
6. 问题要具体，能引导出信息。不要问「还有什么要补充的吗」这种。

只输出 JSON，格式：
{
  "ack": "一句话复述你理解到的内容（用户看得懂的话）",
  "patches": [
    {"op": "set_profile", "field": "location", "value": "西安"},
    {"op": "add_item", "section": "project", "org": "项目名", "role": "角色", "start": "2024.03", "end": "2024.06"},
    {"op": "add_bullet", "section": "project", "item": 0, "text": "一条要点"},
    {"op": "set_meta", "section": "project", "item": 0, "key": "技术栈", "value": "Python · Redis"}
  ],
  "question": "下一个问题"
}

可用 section：education / experience / project / skill / award / cert / other
可用 field：name / phone / email / location / intents
item 是被操作条目在当前章节列表中的下标（从 0 开始），省略时默认最后一条。"""


def describe(doc: ResumeDoc) -> str:
    """把简历压成给模型看的紧凑文本。"""
    lines: list[str] = []
    lines.append(f"姓名：{doc.name or '（空）'}")
    lines.append(f"电话：{doc.phone or '（空）'}｜邮箱：{doc.email or '（空）'}｜城市：{doc.location or '（空）'}")
    if doc.intents:
        lines.append(f"求职意向：{doc.intents}")

    if not doc.sections:
        lines.append("（简历目前是空的）")
        return "\n".join(lines)

    for section in doc.sections[:MAX_SECTIONS]:
        label = SECTION_LABELS.get(section.type, "其他")
        lines.append(f"\n【{label}】section={section.type}")
        if not section.items:
            lines.append("  （空）")
            continue
        for index, item in enumerate(section.items[:MAX_ITEMS_PER_SECTION]):
            head = " / ".join(
                x for x in (item.org, item.role, item.start, item.end) if x
            ) or "（未命名）"
            lines.append(f"  [{index}] {head}")
            for key, value in item.meta.items():
                lines.append(f"      {key}：{value}")
            for bullet in item.bullets:
                text = bullet.text.strip()
                if len(text) > MAX_BULLET_CHARS:
                    text = text[:MAX_BULLET_CHARS] + "…"
                lines.append(f"      · {text}")
    return "\n".join(lines)


def build_messages(
    doc: ResumeDoc,
    gaps: GapReport,
    history: list[dict[str, str]],
    user_text: str,
) -> list[dict[str, str]]:
    context = (
        f"# 当前简历\n{describe(doc)}\n\n"
        f"# 还没补全的地方（按重要程度排序）\n{gaps.as_text()}\n\n"
        f"# 用户刚说\n{user_text}"
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM}]
    messages.extend(history[-HISTORY_TURNS * 2:])
    messages.append({"role": "user", "content": context})
    return messages
