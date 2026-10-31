"""意图识别评估集：改规则/改 prompt 之后，先跑这个再看效果。

    python tools/eval_intent.py            # 只看汇总
    python tools/eval_intent.py -v         # 打印每一条

语料来自真实对话里出现过的说法（含踩过坑的），不是编出来的。
新增一条规则时，务必同时在 GOLDEN 里补上对应说法，防止改 A 坏 B。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.agent.intent import classify  # noqa: E402

# (说法, 期望意图, 期望参数片段)
GOLDEN: list[tuple[str, str, dict]] = [
    # ---- 找岗位 ----
    ("找北京的 AI 应用岗位", "search", {}),
    ("帮我找上海的算法工程师", "search", {}),
    ("有没有深圳的测试岗", "search", {}),
    ("我想找杭州的 Java 开发", "search", {}),
    # ---- 再找 / 排除 ----
    ("再找几个", "more", {}),
    ("再来 5 个", "more", {"count": 5}),
    ("排除外包", "exclude", {}),
    ("不要培训机构的", "exclude", {}),
    # ---- 定制 / 优化 ----
    ("定制第 1 个", "tailor", {"index": 1}),
    ("定制 3", "tailor", {"index": 3}),
    ("优化第 2 个", "tailor", {"index": 2}),
    ("我打算投第三个 帮我把简历优化一下", "tailor", {"index": 3}),
    ("帮我把简历优化一下", "advice", {}),
    ("我的简历需要补充什么", "advice", {}),
    ("我的简历还需要改进吗", "advice", {}),
    # ---- 判断 / 投递 ----
    ("第 2 个适合我吗", "judge", {"index": 2}),
    ("这个岗位我有戏吗", "judge", {}),
    ("投第 1 个", "apply", {"index": 1}),
    ("投递第二个", "apply", {"index": 2}),
    # ---- 问详情 ----
    ("第 2 个的要求是什么", "ask_detail", {"index": 2}),
    ("这个岗位薪资多少", "ask_detail", {}),
    ("第一个公司叫什么", "ask_detail", {"index": 1}),
    # ---- 状态 / 闲聊 ----
    ("现在有什么", "status", {}),
    ("看看候选", "status", {}),
    ("定制是什么意思", "chat", {}),
    ("这个怎么用", "chat", {}),
    ("你好", "chat", {}),
    # ---- 易混 ----
    ("我不想去外包了，再找找", "more", {}),
    ("第 3 个公司是做什么的", "ask_detail", {"index": 3}),
    ("投第三个之前先帮我改下简历", "tailor", {"index": 3}),
]


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    failures: list[tuple[str, str, str, str]] = []
    for text, expected, params in GOLDEN:
        intent = classify(text)
        ok = intent.name == expected
        if ok and params:
            for key, value in params.items():
                if intent.params.get(key) != value:
                    ok = False
        if not ok:
            got = f"{intent.name} {intent.params}"
            failures.append((text, expected, got, str(params)))
        if verbose:
            mark = "PASS" if ok else "FAIL"
            print(f"{mark}  {text:32s} → {intent.name:10s} {intent.params}")

    total = len(GOLDEN)
    passed = total - len(failures)
    rate = passed / total * 100 if total else 0
    print(f"\n意图准确率：{passed}/{total} = {rate:.1f}%")
    if failures:
        print("\n未通过：")
        for text, expected, got, params in failures:
            print(f"  {text}")
            print(f"    期望 {expected} {params} → 实际 {got}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
