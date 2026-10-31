"""共享的 Jinja2 模板环境。"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi.templating import Jinja2Templates

from .config import STATIC_DIR, TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _asset_version() -> str:
    """静态资源版本号 = 静态目录里最新的 mtime。

    开发时改了 CSS 直接刷新就能看到，不用手动清浏览器缓存。
    """
    latest = 0.0
    try:
        for path in Path(STATIC_DIR).glob("*"):
            if path.is_file():
                latest = max(latest, path.stat().st_mtime)
    except OSError:
        pass
    return str(int(latest or time.time()))


ASSET_VERSION = _asset_version()
templates.env.globals["asset_version"] = ASSET_VERSION
