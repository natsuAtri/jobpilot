"""共享的 Jinja2 模板环境。"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi.templating import Jinja2Templates

from .config import STATIC_DIR, TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_last_at = 0.0
_last_value = "0"


def asset_version() -> str:
    """静态资源版本号 = 静态目录里最新的 mtime。

    每次渲染现算（1 秒内复用），所以重新构建 CSS 后刷新就能拿到新文件，
    不需要重启服务。之前是模块级常量，构建完仍然带着旧版本号，浏览器
    会继续用缓存里的旧样式。
    """
    global _last_at, _last_value
    now = time.time()
    if now - _last_at < 1.0:
        return _last_value
    latest = 0.0
    try:
        for path in Path(STATIC_DIR).glob("*"):
            if path.is_file():
                latest = max(latest, path.stat().st_mtime)
    except OSError:
        pass
    _last_at, _last_value = now, str(int(latest or now))
    return _last_value


ASSET_VERSION = asset_version()
templates.env.globals["asset_version"] = asset_version
