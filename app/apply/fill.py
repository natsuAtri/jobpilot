"""表单填充引擎。

半自动投递的核心动作：把「语义字段」映射到真实表单的控件，填值，
然后**停在提交按钮**——提交永远由用户点。

字段映射是平台相关的：真实站点（前程无忧等）的选择器需要登录后对着
实际表单抓取。这里提供通用映射 + 停提交的骨架，用 mock 表单验证管线。
"""
from __future__ import annotations

# 语义字段 → 常见选择器（按优先级试）
FIELD_SELECTORS: dict[str, tuple[str, ...]] = {
    "name": ("input[name*=name]", "input[name*=userName]", "input[placeholder*=姓名]"),
    "phone": ("input[name*=phone]", "input[name*=mobile]", "input[placeholder*=手机]"),
    "email": ("input[name*=email]", "input[placeholder*=邮箱]"),
    "salary": ("input[name*=salary]", "input[placeholder*=薪资]"),
}


def resolve_selector(page, field: str) -> str | None:
    for selector in FIELD_SELECTORS.get(field, ()):
        if page.query_selector(selector):
            return selector
    return None


def fill_form(page, values: dict[str, str]) -> dict[str, str]:
    """按语义字段填值，返回 {字段: 结果}。不点击任何提交按钮。"""
    report: dict[str, str] = {}
    for field, value in values.items():
        selector = resolve_selector(page, field)
        if selector:
            page.fill(selector, value)
            report[field] = "filled"
        else:
            report[field] = "未找到控件"
    return report


# 个人数据库语义字段 → 表单语义字段
DATABASE_FORM_MAP: dict[str, str] = {
    "name": "name",
    "phone": "phone",
    "email": "email",
}


def fill_with_database(page, personal: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """用个人数据库的值填表，返回 (填充报告, 数据库缺失的表单字段)。

    这是「个人数据库逐步完善」的关键一环：有值的直接填；没值的（或控件
    没找到的）放进 missing 列表，调用方据此询问用户，用户补完回写数据库后
    再填一次即可。
    """
    values = {form_key: personal[db_key] for db_key, form_key in DATABASE_FORM_MAP.items()
              if personal.get(db_key)}
    missing = [db_key for db_key, form_key in DATABASE_FORM_MAP.items()
               if not personal.get(db_key)]

    report = fill_form(page, values)
    # 控件没找到的（即使有值），也视为待补
    for form_key, result in report.items():
        if result != "filled":
            db_key = next((k for k, v in DATABASE_FORM_MAP.items() if v == form_key), form_key)
            if db_key not in missing:
                missing.append(db_key)
    return report, missing


def submit_button_exists(page) -> bool:
    """找到提交按钮但绝不点击——只报告它是否在，让用户自己点。"""
    for selector in ("button[type=submit]", "input[type=submit]", "button:has-text('投递')", "button:has-text('提交')"):
        if page.query_selector(selector):
            return True
    return False
