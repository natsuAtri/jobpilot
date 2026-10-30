"""M1 冒烟测试：解析 → 断言结构。

    python tools/smoke_m1.py samples/简历.pdf

不依赖测试框架，退出码非零即失败，方便直接跑或挂到 CI。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parse import extract_pages, extract_rules  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def main(path: str) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    pages = extract_pages(path)
    check("PDF 有内容", bool(pages and pages[0].lines), f"{len(pages)} 页")
    check("单页", len(pages) == 1, f"{len(pages)} 页")

    doc = extract_rules(pages)

    check("识别姓名", bool(doc.name), doc.name)
    check("识别手机号", bool(doc.phone), str(doc.phone))
    check("识别邮箱", bool(doc.email), str(doc.email))

    types = [s.type for s in doc.sections]
    check("识别章节数 >= 4", len(doc.sections) >= 4, str(types))
    for expected in ("education", "project", "skill", "award"):
        check(f"包含 {expected} 章节", expected in types)

    project = doc.section("project")
    check("项目章节有 2 个条目", project is not None and len(project.items) == 2,
          str(len(project.items)) if project else "none")

    if project:
        first = project.items[0]
        check("项目标题不含日期残留", first.start not in (first.org or ""),
              f"{first.org}")
        check("开始日期完整", first.start == "2023.9.18", str(first.start))
        check("结束日期未被截断", first.end == "2023.10.8", str(first.end))
        check("项目有要点", len(first.bullets) >= 4, str(len(first.bullets)))
        check("技术栈进入 meta", "技术栈" in first.meta, str(list(first.meta)))

    skill = doc.section("skill")
    check("技能章节有 3 组", skill is not None and len(skill.items) == 3,
          str(len(skill.items)) if skill else "none")

    edu = doc.section("education")
    if edu and edu.items:
        check("教育条目有起止时间", bool(edu.items[0].start and edu.items[0].end))
        check("核心课程进入 meta", "核心课程" in edu.items[0].meta)

    total = doc.bullet_count()
    check("要点总数 > 10", total > 10, str(total))

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
