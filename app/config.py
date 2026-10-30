"""路径与运行配置。

所有可写数据集中在 data/ 下，便于备份与迁移；不写入系统临时目录。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("RA_DATA_DIR", BASE_DIR / "data"))
TMP_DIR = DATA_DIR / "tmp"
UPLOADS_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "export"
DB_PATH = DATA_DIR / "app.db"
SECRETS_PATH = DATA_DIR / "secrets.json"

MIGRATIONS_DIR = BASE_DIR / "migrations"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ASSETS_DIR = BASE_DIR / "assets"


def ensure_dirs() -> None:
    for path in (DATA_DIR, TMP_DIR, UPLOADS_DIR, EXPORT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _point_temp_to_local() -> None:
    """把进程临时目录指到 data/tmp。

    原因：Playwright 的 node 驱动和 Chromium 会在系统临时目录建文件，
    在某些受限环境（沙箱、权限收紧的机器）里对系统 Temp 目录没有写权限，
    直接 EPERM。统一指到应用自己的目录，既规避权限问题，也便于清理。
    """
    ensure_dirs()
    for var in ("TEMP", "TMP", "TMPDIR"):
        os.environ[var] = str(TMP_DIR)


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """读取工作目录下的 .env，注入环境变量（不覆盖已有值）。

    格式：KEY=VALUE，支持 # 注释和引号包裹。
    """
    path = path or (BASE_DIR / ".env")
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return loaded
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            loaded[key] = value
            os.environ.setdefault(key, value)
    return loaded


_point_temp_to_local()
load_dotenv()
