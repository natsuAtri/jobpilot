"""LLM 厂商与任务绑定的读写。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..db import get_conn
from . import secrets


@dataclass
class Provider:
    id: int
    label: str
    base_url: str
    api_key_ref: str | None
    models: list[str] = field(default_factory=list)
    capabilities: dict = field(default_factory=dict)
    enabled: bool = True
    api_key: str | None = None          # 运行时填充，不落库

    @property
    def masked_key(self) -> str:
        return secrets.mask(self.api_key)


def _row_to_provider(row) -> Provider:
    return Provider(
        id=row["id"],
        label=row["label"],
        base_url=row["base_url"],
        api_key_ref=row["api_key_ref"],
        models=json.loads(row["models_json"] or "[]"),
        capabilities=json.loads(row["capabilities_json"] or "{}"),
        enabled=bool(row["enabled"]),
    )


def list_providers(with_keys: bool = True) -> list[Provider]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM llm_provider ORDER BY id").fetchall()
    out = [_row_to_provider(r) for r in rows]
    if with_keys:
        for p in out:
            p.api_key = secrets.get_secret(p.api_key_ref)
    return out


def get_provider(provider_id: int, with_key: bool = True) -> Provider | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM llm_provider WHERE id = ?", (provider_id,)).fetchone()
    if row is None:
        return None
    p = _row_to_provider(row)
    if with_key:
        p.api_key = secrets.get_secret(p.api_key_ref)
    return p


def create_provider(
    label: str,
    base_url: str,
    api_key: str = "",
    model: str = "",
    capabilities: dict | None = None,
) -> int:
    """建一个厂商。一个厂商对应一个模型——用户只想填一次。"""
    models = [model.strip()] if model and model.strip() else []
    ref = None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO llm_provider(label, base_url, models_json, capabilities_json) "
            "VALUES (?, ?, ?, ?)",
            (
                label.strip(),
                base_url.strip().rstrip("/"),
                json.dumps(models, ensure_ascii=False),
                json.dumps(capabilities or {}, ensure_ascii=False),
            ),
        )
        provider_id = int(cur.lastrowid)
        ref = f"provider_{provider_id}"
        conn.execute("UPDATE llm_provider SET api_key_ref = ? WHERE id = ?", (ref, provider_id))
    if api_key:
        secrets.set_secret(ref, api_key.strip())
    return provider_id


def update_provider_key(provider_id: int, api_key: str) -> None:
    p = get_provider(provider_id, with_key=False)
    if p is None:
        return
    ref = p.api_key_ref or f"provider_{provider_id}"
    if not p.api_key_ref:
        with get_conn() as conn:
            conn.execute("UPDATE llm_provider SET api_key_ref = ? WHERE id = ?", (ref, provider_id))
    secrets.set_secret(ref, api_key.strip())


def delete_provider(provider_id: int) -> None:
    p = get_provider(provider_id, with_key=False)
    with get_conn() as conn:
        conn.execute("DELETE FROM llm_provider WHERE id = ?", (provider_id,))
    if p:
        secrets.delete_secret(p.api_key_ref)


def list_bindings() -> dict[str, dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT b.*, p.label AS provider_label FROM llm_binding b "
            "JOIN llm_provider p ON p.id = b.provider_id"
        ).fetchall()
    return {r["task"]: dict(r) for r in rows}


def set_binding(task: str, provider_id: int, model: str = "", params: dict | None = None) -> None:
    """绑定分组到厂商。模型名留空就用厂商自己的模型，不再二次填写。"""
    if not model:
        provider = get_provider(provider_id, with_key=False)
        model = provider.models[0] if provider and provider.models else ""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO llm_binding(task, provider_id, model, params_json) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(task) DO UPDATE SET "
            "provider_id = excluded.provider_id, model = excluded.model, "
            "params_json = excluded.params_json",
            (task, provider_id, model.strip(), json.dumps(params or {}, ensure_ascii=False)),
        )


def configured() -> bool:
    """是否通过 .env 配好了模型。"""
    import os

    return bool(
        (os.environ.get("RA_LLM_BASE_URL") or "").strip()
        and (os.environ.get("RA_LLM_API_KEY") or "").strip()
        and (os.environ.get("RA_LLM_MODEL") or "").strip()
    )


def resolve(task: str) -> tuple[Provider, str]:
    """把内部任务解析成 (厂商, 模型)。

    现在统一走 .env 配置：RA_LLM_BASE_URL / RA_LLM_API_KEY / RA_LLM_MODEL。
    task 参数保留，方便以后需要按任务分档时再扩展。
    """
    env_provider = _env_provider()
    if env_provider is None:
        raise LookupError(
            "还没有配置模型：请在项目根目录的 .env 里填入 "
            "RA_LLM_BASE_URL / RA_LLM_API_KEY / RA_LLM_MODEL"
        )
    return env_provider


def _env_provider() -> tuple[Provider, str] | None:
    """从 .env 构造一个「厂商」，让只用 .env 的配置也能跑。"""
    import os

    base_url = (os.environ.get("RA_LLM_BASE_URL") or "").strip()
    api_key = (os.environ.get("RA_LLM_API_KEY") or "").strip()
    model = (os.environ.get("RA_LLM_MODEL") or "").strip()
    if not (base_url and api_key and model):
        return None
    provider = Provider(
        id=0, label=".env", base_url=base_url.rstrip("/"), api_key_ref=None,
        models=[model], api_key=api_key,
    )
    return provider, model


def clear_binding(task: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM llm_binding WHERE task = ?", (task,))
