"""FastMCP Depends factories for document MCP tools.

Use ``from fastmcp.dependencies import Depends`` on tool methods.
Do not import FastAPI ``Depends``.
"""

from __future__ import annotations

from typing import Optional

from document_processing_mcp.core.context import ApplicationContext, build_application_context
from document_processing_mcp.core.settings import Settings, settings as load_settings
from document_processing_mcp.storage.blob_store import BlobStore
from document_processing_mcp.storage.call_log_store import CallLogStore

_app_context: Optional[ApplicationContext] = None


def get_app_context() -> ApplicationContext:
    global _app_context
    if _app_context is None:
        _app_context = build_application_context()
    return _app_context


def set_app_context(context: ApplicationContext) -> None:
    global _app_context
    _app_context = context


def reset_app_context() -> None:
    global _app_context
    _app_context = None


def get_settings() -> Settings:
    return load_settings()


def get_blob_store() -> BlobStore:
    return get_app_context().blob_store


def get_call_log_store() -> CallLogStore:
    return get_app_context().call_log_store
