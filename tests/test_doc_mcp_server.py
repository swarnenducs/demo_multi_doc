"""Tests for document_process_mcp FastMCP tools."""

from __future__ import annotations

import asyncio

from document_processing_mcp.server import DocumentProcessMCP
from doc_mcp_helpers import call_tool, list_tool_names


def test_document_mcp_lists_tools() -> None:
    server = DocumentProcessMCP(host="127.0.0.1", port=18001)
    tools = asyncio.run(list_tool_names(server))
    assert "health" in tools
    assert "generate_document" in tools


def test_document_mcp_health() -> None:
    from document_processing_mcp.core.dependencies import get_app_context
    from document_processing_mcp.storage.blob_store import BlobStore

    server = DocumentProcessMCP(host="127.0.0.1", port=18001)
    payload = asyncio.run(call_tool(server, "health"))
    assert isinstance(payload, dict)
    assert payload["ok"] is True
    from document_processing_mcp.models.mcp_responses import McpHealthResponse

    McpHealthResponse.model_validate(payload)
    assert payload["mcp"] == "document_process_mcp"
    assert payload["agent"] == "document_process_mcp"
    assert "blob_enabled" in payload
    assert isinstance(get_app_context().blob_store, BlobStore)


def test_document_mcp_hides_injected_context() -> None:
    """Class methods + Depends must not expose self or app_context to the client."""
    from fastmcp import Client

    server = DocumentProcessMCP(host="127.0.0.1", port=18001)

    async def _schema() -> dict[str, object]:
        async with Client(server) as client:
            tools = await client.list_tools()
            health = next(t for t in tools if t.name == "health")
            generate = next(t for t in tools if t.name == "generate_document")
            return {
                "health": set((health.inputSchema or {}).get("properties") or {}),
                "generate": set((generate.inputSchema or {}).get("properties") or {}),
            }

    schemas = asyncio.run(_schema())
    assert "self" not in schemas["health"]
    assert "app_context" not in schemas["health"]
    assert "self" not in schemas["generate"]
    assert "template_path" in schemas["generate"]
