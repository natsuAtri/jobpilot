"""M2 冒烟测试：渲染 → 打印 PDF → 回读校验。

    python tools/smoke_m2.py 1          # 用数据库里 id=1 的简历
    python tools/smoke_m2.py samples/简历.pdf   # 或直接从 PDF 解析

产物写到 data/export/ 下，便于人工翻看。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.repo import resumes as repo  # noqa: E402
from app.render import build_context, expected_fragments, render_html, verify_pdf  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


def load(arg: str):
    if arg.isdigit():
        return repo.load_doc(int(arg))
    from app.parse import extract_pages, extract_rules

    return extract_rules(extract_pages(arg))


def main(arg: str) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from app.render.browser import print_to_pdf

    doc = load(arg)
    if doc is None:
        print("找不到简历")
        return 2

    # 测试产物写到 tmp，别污染 data/export（用户会在下载目录里看到）
    out_dir = BASE / "data" / "tmp" / "m2"
    out_dir.mkdir(parents=True, exist_ok=True)
    expected = expected_fragments(doc)

    for variant in ("design", "ats"):
        for theme in ("ink", "graphite") if variant == "design" else ("ink",):
            html = render_html(doc, theme=theme, variant=variant)
            check(f"{variant}/{theme} 生成 HTML", "<div class=\"page" in html, f"{len(html)} 字符")

            pdf_path = out_dir / f"resume-{variant}-{theme}.pdf"
            print_to_pdf(html, pdf_path)
            check(f"{variant}/{theme} 生成 PDF", pdf_path.exists() and pdf_path.stat().st_size > 5000,
                  f"{pdf_path.stat().st_size if pdf_path.exists() else 0} 字节")

            result = verify_pdf(pdf_path, expected)
            check(f"{variant}/{theme} 回读校验", result.ok, result.message)
            if not result.ok:
                print("      缺失:", result.missing[:5])

    # 上下文构建的健壮性
    ctx = build_context(doc)
    check("期望片段数量 > 12", len(expected) > 12, str(len(expected)))
    check("至少渲染 4 个章节", len(ctx["sections"]) >= 4, str([s["type"] for s in ctx["sections"]]))
    check("空章节被过滤", all(s["items"] for s in ctx["sections"]))
    check("日期已规范化",
          all("." in i["period"].split(" — ")[0] for s in ctx["sections"]
              if s["kind"] == "items" for i in s["items"] if i["period"]),
          str([i["period"] for s in ctx["sections"] if s["kind"] == "items" for i in s["items"]]))

    # 负向测试：校验门禁必须真的会拦人
    bad = verify_pdf(out_dir / "resume-design-ink.pdf", expected + ["这一段内容不存在于任何简历中"])
    check("多出不存在的内容时校验失败", not bad.ok, bad.message)
    check("失败时能报出缺失片段", any("不存在" in m for m in bad.missing), str(bad.missing[:2]))

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "1"))
