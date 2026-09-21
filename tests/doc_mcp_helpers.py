"""In-process FastMCP client helpers (no ip_api)."""

from __future__ import annotations

from typing import Any

from fastmcp import Client


def _jsonable(value: Any) -> Any:
    """Pydantic / FastMCP Root → JSON-serializable dict."""
    if value is None or isinstance(value, (dict, list, str, int, float, bool)):
        return value
    json_fn = getattr(value, "model_dump_json", None)
    if callable(json_fn):
        import json

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
        return texts[0]
    return {"content": texts, "is_error": bool(result.is_error)}


async def list_tool_names(server: Any) -> list[str]:
    async with Client(server) as client:
        tools = await client.list_tools()
        return [t.name for t in tools]


async def call_tool(server: Any, name: str, arguments: dict[str, Any] | None = None) -> Any:
    async with Client(server) as client:
        result = await client.call_tool(name, arguments or {})
        return tool_result_payload(result)
