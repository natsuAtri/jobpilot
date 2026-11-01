"""统一的 LLM 调用入口。

所有厂商都按 OpenAI 兼容协议调用（DeepSeek / 通义 / Kimi / 智谱 / Ollama
都提供 /chat/completions 端点），差异在 base_url 与 model 名。
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from . import registry
from .tasks import get_task

DEFAULT_TIMEOUT = 90.0
# 有些端点不认 response_format，命中后记下来，后面同一端点直接不加
_JSON_MODE_UNSUPPORTED: set[str] = set()


@asynccontextmanager
async def session(timeout: float = DEFAULT_TIMEOUT):
    """一轮对话共用一个连接池。

    不能做成模块级单例：每个用户回合都在 asyncio.run() 里新建事件循环，
    跨循环复用 AsyncClient 会炸。所以只在「一轮」内复用。
    """
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
    ) as shared:
        yield shared


class LLMError(RuntimeError):
    """调用失败，附带可读原因，便于直接显示在界面上。"""


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str
    usage: dict[str, Any]


DEFAULT_TIMEOUT = 120.0


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


async def complete(
    task: str,
    messages: Sequence[dict[str, str]],
    *,
    temperature: float | None = None,
    json_mode: bool = False,
    max_tokens: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.AsyncClient | None = None,
) -> LLMResult:
    spec = get_task(task)

    try:
        provider, model = registry.resolve(task)
    except LookupError as exc:
        raise LLMError(f"{exc}，请到设置页配置。") from exc

    payload: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": spec.temperature if temperature is None else temperature,
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    endpoint_key = f"{provider.base_url}|{model}"
    if json_mode and endpoint_key in _JSON_MODE_UNSUPPORTED:
        json_mode = False
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"

    # 网络抖动/限流是常态：失败重试两次，退避 1.5s / 3s
    import time as _time

    _t0 = _time.time()
    resp = None
    last_error: str = ""
    own = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        for attempt in range(3):
            try:
                resp = await http.post(_endpoint(provider.base_url), json=payload, headers=headers)
            except httpx.RequestError as exc:
                last_error = f"无法连接 {provider.label}：{exc}"
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise LLMError(last_error) from exc

            if resp.status_code < 400:
                break
            last_error = f"{provider.label} 返回 {resp.status_code}：{resp.text[:300]}"
            if (resp.status_code in (400, 422) and json_mode
                    and endpoint_key not in _JSON_MODE_UNSUPPORTED):
                _JSON_MODE_UNSUPPORTED.add(endpoint_key)
                payload.pop("response_format", None)
                json_mode = False
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise LLMError(last_error)
    finally:
        if own:
            await http.aclose()

    if resp is None or resp.status_code >= 400:
        raise LLMError(last_error or f"{provider.label} 请求失败")

    try:
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMError(f"{provider.label} 返回了无法解析的响应") from exc

    usage = data.get("usage", {}) if isinstance(data, dict) else {}
    try:
        from .. import trace

        trace.record(
            "llm", task,
            args={"model": model, "messages": len(messages), "json": json_mode},
            result={"chars": len(text or ""), "preview": (text or "")[:120]},
            latency_ms=int((_time.time() - _t0) * 1000),
            tokens=(usage or {}).get("total_tokens"),
        )
    except Exception:  # noqa: BLE001 - 观测不影响主流程
        pass

    return LLMResult(
        text=text or "",
        model=model,
        provider=provider.label,
        usage=usage,
    )


async def complete_json(
    task: str,
    messages: Sequence[dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """要求模型返回 JSON 并解析。

    解析失败不静默重试，直接把原文抛给调用方——抽取环节需要知道
    「模型说了什么」而不是「重试到像样为止」。
    """
    if os.environ.get("RA_LLM_STUB") == "1":
        from . import stub

        return stub.generic_stub(task, list(messages))

    last_error = None
    for attempt in range(2):
        result = await complete(
            task, messages, temperature=temperature, json_mode=True,
            max_tokens=max_tokens, client=client,
        )
        raw = result.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.lstrip().startswith("json"):
                raw = raw.lstrip()[4:]
            raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            last_error = exc
            if attempt == 0:
                continue
    raise LLMError(f"模型未返回合法 JSON：{raw[:200]}") from last_error


async def ping(provider_id: int, model: str | None = None) -> str:
    """设置页的「测试连接」：发一条极短消息，返回模型回复。"""
    provider = registry.get_provider(provider_id)
    if provider is None:
        raise LLMError("厂商不存在")

    use_model = model or (provider.models[0] if provider.models else "")
    if not use_model:
        raise LLMError("请先填写至少一个模型名")

    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"

    payload = {
        "model": use_model,
        "messages": [{"role": "user", "content": "回复两个字：可用"}],
        "temperature": 0,
        "max_tokens": 16,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(_endpoint(provider.base_url), json=payload, headers=headers)
    except httpx.RequestError as exc:
        raise LLMError(f"连接失败：{exc}") from exc
    if resp.status_code >= 400:
        raise LLMError(f"HTTP {resp.status_code}：{resp.text[:300]}")
    try:
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001 - 上游格式多变
        raise LLMError("响应格式不符合 OpenAI 兼容协议") from exc


@dataclass
class ToolReply:
    """带工具调用的一轮回复。"""
    content: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str = ""


async def chat_with_tools(
    task: str,
    messages: Sequence[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.AsyncClient | None = None,
) -> ToolReply:
    """一次带 tools 的对话补全。模型可能返回 tool_calls 而不是文本。"""
    if os.environ.get("RA_LLM_STUB") == "1":
        from .stub import generic_stub

        return ToolReply(content=json.dumps(generic_stub(task, list(messages)), ensure_ascii=False),
                         tool_calls=[])

    spec = get_task(task)
    try:
        provider, model = registry.resolve(task)
    except LookupError as exc:
        raise LLMError(f"{exc}，请在 .env 里配置模型。") from exc

    payload: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": spec.temperature if temperature is None else temperature,
        "stream": False,
        "tools": tools,
        "tool_choice": "auto",
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    endpoint_key = f"{provider.base_url}|{model}"
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"

    import time as _time

    _t0 = _time.time()
    last_error = ""
    resp = None
    own = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        for attempt in range(3):
            try:
                resp = await http.post(_endpoint(provider.base_url), json=payload, headers=headers)
            except httpx.RequestError as exc:
                last_error = f"无法连接 {provider.label}：{exc}"
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                raise LLMError(last_error) from exc
            if resp.status_code < 400:
                break
            last_error = f"{provider.label} 返回 {resp.status_code}：{resp.text[:300]}"
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise LLMError(last_error)
    finally:
        if own:
            await http.aclose()

    if resp is None or resp.status_code >= 400:
        raise LLMError(last_error or f"{provider.label} 请求失败")

    try:
        data = resp.json()
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LLMError(f"{provider.label} 返回了无法解析的响应") from exc

    raw_calls = message.get("tool_calls") or []
    # 严格网关会因为回传多余字段（如 index）报 400，只留必要字段
    calls: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_calls):
        if not isinstance(raw, dict):
            continue
        fn = raw.get("function")
        if not isinstance(fn, dict):
            fn = {}
        calls.append({
            "id": raw.get("id") or f"call_{index}",
            "type": "function",
            "function": {"name": fn.get("name") or "", "arguments": fn.get("arguments") or "{}"},
        })
    finish_reason = data["choices"][0].get("finish_reason") or ""
    usage = data.get("usage", {}) if isinstance(data, dict) else {}
    try:
        from .. import trace

        trace.record(
            "llm", f"{task}:tools",
            args={"model": model, "tools": len(tools)},
            result={"tool_calls": [c.get("function", {}).get("name") for c in calls],
                    "chars": len(message.get("content") or "")},
            latency_ms=int((_time.time() - _t0) * 1000),
            tokens=(usage or {}).get("total_tokens"),
        )
    except Exception:  # noqa: BLE001
        pass

    return ToolReply(content=message.get("content") or "", tool_calls=calls,
                     finish_reason=finish_reason)
