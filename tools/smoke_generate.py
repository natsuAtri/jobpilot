"""双 agent 生成冒烟测试（用替身模型）。

    RA_LLM_STUB=1 python tools/smoke_generate.py

验证：事实白名单抽取 → 起草 → 三守恒拦下「负责→主导」→ 评审意见 →
有限轮停止 → 只应用通过校验的改写。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("RA_LLM_STUB", "1")

from app.generate import engine  # noqa: E402
from app.generate.facts import extract_facts, whitelist_text  # noqa: E402
from app.models import Bullet, Item, ResumeDoc, Section  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def make_doc() -> ResumeDoc:
    return ResumeDoc(
        name="测试",
        sections=[
            Section(
                type="project",
                items=[
                    Item(
                        org="手语翻译系统",
                        role=None,
                        bullets=[
                            Bullet(text="负责手语数据集的标注"),
                            Bullet(text="构建图卷积神经网络"),
                            Bullet(text="在开源数据集 MFCAD Dataset 进行训练"),
                        ],
                    )
                ],
            )
        ],
    )


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    # 只改标点/空白不算「改写」，不该占用终审列表
    from app.generate.engine import Proposal

    p1 = Proposal(source_id="x", label="x", original="美化前端操作界面。",
                  rewritten="美化前端操作界面")
    check("只去句号不算改动", not p1.changed)
    p2 = Proposal(source_id="x", label="x", original="美化前端操作界面。",
                  rewritten="优化前端操作界面交互")
    check("实质改写算改动", p2.changed)

    doc = make_doc()

    facts = extract_facts(doc)
    check("事实白名单抽取", len(facts) == 3, f"{len(facts)} 条")
    check("白名单文本可读", "负责" in whitelist_text(facts) and "||" in whitelist_text(facts))

    result = await engine.generate(doc, "岗位要求：熟悉深度学习框架，有模型训练经验")

    check("生成了提案", len(result.proposals) == 3, f"{len(result.proposals)} 条")
    check("有限轮停止", result.rounds >= 1, f"{result.rounds} 轮")

    blocked = [p for p in result.proposals if p.blocked]
    check("越界改写被拦下", len(blocked) == 1, f"{len(blocked)} 条")
    if blocked:
        names = [c.name for c in blocked[0].checks if not c.ok]
        check("拦截原因是强度守恒", "强度守恒" in names, str(names))
        check("评审给了意见", bool(blocked[0].critique), blocked[0].critique)

    passed_changed = [p for p in result.proposals if p.ok and p.changed]
    check("合法改写通过", len(passed_changed) >= 1, f"{len(passed_changed)} 条")

    # 应用：只写通过校验的
    applied = engine.apply_to_doc(doc, result.proposals)
    check("只应用了通过校验的改写", applied == len(passed_changed), f"{applied} 处")
    new_texts = [b.text for s in doc.sections for i in s.items for b in i.bullets]
    check("被拦下的原样保留", "负责手语数据集的标注" in new_texts, str(new_texts))
    check("合法改写已写入", any("搭建" in t or "开展" in t for t in new_texts), str(new_texts))

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
