"""投递助手：登录态浏览器 + 表单填充。"""
from .browser import open_job, profile_dir
from .fill import FIELD_SELECTORS, fill_form, resolve_selector, submit_button_exists

__all__ = [
    "open_job", "profile_dir",
    "FIELD_SELECTORS", "fill_form", "resolve_selector", "submit_button_exists",
]
