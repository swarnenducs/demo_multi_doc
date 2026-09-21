"""Shared application context for document MCP (no FastAPI Depends)."""

from __future__ import annotations

from dataclasses import dataclass

from document_processing_mcp.core.settings import Settings, settings
from document_processing_mcp.storage.blob_store import BlobStore, get_blob_store
from document_processing_mcp.storage.call_log_store import CallLogStore
from document_processing_mcp.storage.db import ensure_schema


@dataclass
class ApplicationContext:
    """Process-wide collaborators used by FastMCP tools."""

    settings: Settings
    blob_store: BlobStore
    call_log_store: CallLogStore


def build_application_context() -> ApplicationContext:
    cfg = settings()
    cfg.ensure_directories()
    ensure_schema()
    return ApplicationContext(
        settings=cfg,
        blob_store=get_blob_store(),
        call_log_store=CallLogStore(),
    )
