"""Thin FastMCP HTTP client for the local Gradio UI."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, str, int, float, bool)):
        return value
    json_fn = getattr(value, "model_dump_json", None)
    if callable(json_fn):
        return json.loads(json_fn())
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            dumped = dump(mode="json")
        except TypeError:
            dumped = dump()
        if isinstance(dumped, dict):
            return dumped
        if dumped is not value:
            return _jsonable(dumped)
    fields = getattr(type(value), "model_fields", None)
    if fields:
        return {name: _jsonable(getattr(value, name, None)) for name in fields}
    try:
        dumped = vars(value)
        if isinstance(dumped, dict) and dumped:
            return dumped
    except TypeError:
        pass
    return value


def tool_result_payload(result: Any) -> Any:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    converted = _jsonable(getattr(result, "data", None))
    if isinstance(converted, dict):
        return converted
    texts: list[str] = []
    for block in result.content or []:
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    return {"content": texts, "is_error": bool(getattr(result, "is_error", False))}


def default_mcp_url() -> str:
    return (os.getenv("DOCUMENT_MCP_URL") or "http://127.0.0.1:8001/mcp").rstrip("/")


async def call_mcp_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    url: str | None = None,
    timeout: float = 600.0,
) -> Any:
    from fastmcp import Client

    target = (url or default_mcp_url()).rstrip("/")
    async with Client(target, timeout=timeout) as client:
        result = await client.call_tool(name, arguments or {})
        return tool_result_payload(result)


def call_mcp_tool_sync(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    url: str | None = None,
    timeout: float = 600.0,
) -> Any:
    return asyncio.run(call_mcp_tool(name, arguments, url=url, timeout=timeout))


def health_sync(*, url: str | None = None) -> dict[str, Any]:
    try:
        payload = call_mcp_tool_sync("health", url=url, timeout=15.0)
        if isinstance(payload, dict):
            return payload
        return {"ok": True, "payload": payload}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
