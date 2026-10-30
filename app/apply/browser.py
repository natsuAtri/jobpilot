"""投递助手：打开可见浏览器 + 持久化登录态。

用系统 Chrome 带一个专用 profile 打开岗位页：用户扫码/密码登录一次，
登录态存进 data/browser-profile，之后每次投递复用。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import DATA_DIR, ensure_dirs
from ..render.browser import find_browser


def profile_dir() -> Path:
    ensure_dirs()
    return DATA_DIR / "browser-profile"


def open_job(url: str | None) -> bool:
    """用持久化 profile 打开岗位页（可见窗口）。返回是否成功启动。"""
    browser = find_browser()
    profile = profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    target = url or "https://we.51job.com"
    try:
        subprocess.Popen(
            [browser, f"--user-data-dir={profile}", "--no-first-run",
             "--new-window", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False
