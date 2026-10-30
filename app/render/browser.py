"""用本机 Chromium 把 HTML 打成 PDF / PNG。

为什么走命令行而不是 Playwright：
M2 只需要「HTML → PDF」，命令行零额外依赖、不用下 150MB 浏览器、
也能复用用户已经装好的 Chrome。Playwright 留给 M4 的表单填写。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

from ..config import TMP_DIR, ensure_dirs


class BrowserError(RuntimeError):
    pass


CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)

_cached: str | None = None


def find_browser() -> str:
    global _cached
    if _cached and Path(_cached).exists():
        return _cached
    env = os.environ.get("RA_BROWSER")
    if env and Path(env).exists():
        _cached = env
        return env
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            _cached = candidate
            return candidate
    for name in ("chrome", "chromium", "google-chrome", "msedge"):
        found = shutil.which(name)
        if found:
            _cached = found
            return found
    raise BrowserError(
        "找不到 Chrome / Edge。请安装，或用环境变量 RA_BROWSER 指向浏览器可执行文件。"
    )


def _write_temp_html(html: str) -> Path:
    ensure_dirs()
    path = TMP_DIR / f"{uuid.uuid4().hex}.html"
    path.write_text(html, encoding="utf-8")
    return path


def _run(args: list[str], timeout: float) -> None:
    try:
        proc = subprocess.run(
            args, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise BrowserError("浏览器渲染超时") from exc
    except OSError as exc:
        raise BrowserError(f"无法启动浏览器：{exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "ignore")[-400:]
        raise BrowserError(f"浏览器返回 {proc.returncode}：{tail}")


def _base_flags(browser: str) -> list[str]:
    ensure_dirs()
    return [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-crash-reporter",
        "--disable-breakpad",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--user-data-dir={TMP_DIR / 'profile'}",
    ]


def _file_url(path: Path) -> str:
    return path.resolve().as_uri()


def print_to_pdf(html: str, out: Path, *, timeout: float = 90.0) -> Path:
    browser = find_browser()
    html_path = _write_temp_html(html)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    args = _base_flags(browser) + [
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        "--virtual-time-budget=4000",
        f"--print-to-pdf={out}",
        _file_url(html_path),
    ]
    try:
        _run(args, timeout)
    finally:
        html_path.unlink(missing_ok=True)
    if not out.exists() or out.stat().st_size == 0:
        raise BrowserError("浏览器没有生成 PDF")
    return out


def screenshot_png(
    html: str,
    out: Path,
    *,
    width: int = 794,
    height: int = 1123,
    scale: float = 2.0,
    timeout: float = 90.0,
) -> Path:
    browser = find_browser()
    html_path = _write_temp_html(html)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    args = _base_flags(browser) + [
        "--hide-scrollbars",
        f"--force-device-scale-factor={scale}",
        f"--window-size={width},{height}",
        "--virtual-time-budget=4000",
        f"--screenshot={out}",
        _file_url(html_path),
    ]
    try:
        _run(args, timeout)
    finally:
        html_path.unlink(missing_ok=True)
    if not out.exists() or out.stat().st_size == 0:
        raise BrowserError("浏览器没有生成截图")
    return out
