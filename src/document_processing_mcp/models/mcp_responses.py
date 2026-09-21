"""Pydantic payloads for document FastMCP tools (structured JSON)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class McpHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    mcp: str
    agent: str
    transport: str = "http|stdio"
    host: str | None = None
    port: int | None = None
    blob_enabled: bool = False
    blob_container: str | None = None
    azure_sql: bool = False


class GenerateDocumentResponse(BaseModel):
    """Public MCP result for ``generate_document``."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    mcp: str = "document_process_mcp"
    xid: str | None = None
    job_id: str | None = None
    status: str
    errors: list[str] = Field(default_factory=list)
    error: str | None = None
    output_path: str | None = None
    confidence: dict[str, Any] | None = None
    extraction_validation: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    mapper_llm: str | None = None
    validator_llm: str | None = None
    db_updated: bool = False
    elapsed_ms: float | None = None
    elapsed: str | None = None
