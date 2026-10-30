"""表单填充引擎冒烟测试（用本地 mock 表单，不依赖真实招聘站）。

    python tools/smoke_apply.py

验证：语义字段 → 控件映射 → 填值 → 停在提交按钮（不点击）。
真实站点的选择器是同一套引擎，只是映射表不同。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.apply.fill import fill_form, resolve_selector, submit_button_exists  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)


MOCK_HTML = """
<form id="applyForm">
  <label>姓名<input name="userName"></label>
  <label>手机<input name="phone"></label>
  <label>邮箱<input name="email"></label>
  <label>期望薪资<input name="salary"></label>
  <label>无法映射的字段<input name="customField"></label>
  <button type="submit" id="submitBtn">提交申请</button>
</form>
"""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.set_content(MOCK_HTML)

        values = {"name": "张三", "phone": "13800138000", "email": "z@example.com", "salary": "15k"}
        report = fill_form(page, values)

        check("姓名控件被识别并填入", report.get("name") == "filled", str(report))
        check("手机控件被识别并填入", report.get("phone") == "filled", str(report))
        check("邮箱控件被识别并填入", report.get("email") == "filled", str(report))
        check("薪资控件被识别并填入", report.get("salary") == "filled", str(report))
        check("填值正确", page.input_value("input[name=userName]") == "张三",
              page.input_value("input[name=userName]"))
        check("提交按钮存在", submit_button_exists(page))
        check("提交按钮未被点击（页面未跳转）", "set_content" not in page.url and page.query_selector("#submitBtn") is not None)

        browser.close()

    # 未知字段的映射返回 None，不报错
    from playwright.sync_api import sync_playwright as sp2

    with sp2() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content(MOCK_HTML)
        check("无法映射的字段返回 None", resolve_selector(pg, "customField") is None)
        b.close()

    # 个人数据库联动：缺失字段询问 + 补全回写
    from app.apply.fill import fill_with_database

    with sp2() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content(MOCK_HTML)

        personal = {"name": "张三", "phone": "13800138000"}  # 缺 email
        report, missing = fill_with_database(pg, personal)
        check("缺失字段被识别", "email" in missing, str(missing))
        check("有值的字段已填入", report.get("name") == "filled" and report.get("phone") == "filled",
              str(report))
        check("缺失字段未填", report.get("email") != "filled", str(report))

        # 模拟用户补填 → 回写个人库 → 再填
        personal["email"] = "z@example.com"
        _, missing2 = fill_with_database(pg, personal)
        check("补全后缺失减少", "email" not in missing2, str(missing2))
        check("补全后表单值正确", pg.input_value("input[name=email]") == "z@example.com")
        b.close()

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
