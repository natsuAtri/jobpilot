"""API Key 存储。

Key 不写进数据库，单独放在 data/secrets.json（权限 600），
数据库只存引用名。开源时不会连带泄漏，也便于用户手动替换。
"""
from __future__ import annotations

import json
import os
import stat

from ..config import SECRETS_PATH, ensure_dirs


def _read() -> dict[str, str]:
    ensure_dirs()
    if not SECRETS_PATH.exists():
        return {}
    try:
        return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write(data: dict[str, str]) -> None:
    ensure_dirs()
    SECRETS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:  # 仅 POSIX 有意义；Windows 上忽略
        os.chmod(SECRETS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def set_secret(ref: str, value: str) -> None:
    data = _read()
    if value:
        data[ref] = value
    else:
        data.pop(ref, None)
    _write(data)


def get_secret(ref: str | None) -> str | None:
    """取密钥：环境变量优先（.env），其次 data/secrets.json。"""
    if not ref:
        return None
    # .env 里的 per-provider 覆盖，例如 RA_KEY_PROVIDER_1=sk-xxx
    specific = os.environ.get(f"RA_KEY_{ref.upper()}")
    if specific:
        return specific
    # 全局统一密钥，适合只用一个厂商的简单配置
    global_key = os.environ.get("RA_LLM_API_KEY")
    if global_key:
        return global_key
    return _read().get(ref)


def delete_secret(ref: str | None) -> None:
    if not ref:
        return
    data = _read()
    if data.pop(ref, None) is not None:
        _write(data)


def mask(value: str | None, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 8}{value[-keep:]}"
