"""Shared base for class-based FastMCP agent servers."""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod

from fastmcp import FastMCP


class BaseAgentMCPServer(FastMCP, ABC):
    """
    Class-based FastMCP server.

    Subclasses call ``super().__init__(...)`` then implement ``register_tools``.
    Register bound instance methods with ``self.add_tool(self.method)`` so
    ``self`` is not exposed in the MCP tool schema. Inject stores with
    ``fastmcp.dependencies.Depends`` (not FastAPI Depends).
    """

    def __init__(
        self,
        name: str,
        *,
        instructions: str,
        host: str | None = None,
        port: int | None = None,
        version: str = "0.1.0",
    ) -> None:
        super().__init__(name=name, instructions=instructions, version=version)
        self.host = host or os.getenv("MCP_HOST", "127.0.0.1")
        self.port = int(port if port is not None else os.getenv("MCP_PORT", "8001"))
        self.register_tools()

    @abstractmethod
    def register_tools(self) -> None:
        """Register ``@self.tool`` handlers on this server instance."""

    def run_http(self, *, show_banner: bool = False) -> None:
        """Serve Streamable HTTP for FastAPI / remote MCP clients."""
        try:
            self.run(
                transport="http",
                host=self.host,
                port=self.port,
                show_banner=show_banner,
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            return

    def run_stdio(self, *, show_banner: bool = False) -> None:
        """Serve stdio (Cursor / local MCP hosts)."""
        try:
            self.run(transport="stdio", show_banner=show_banner)
        except (KeyboardInterrupt, asyncio.CancelledError):
            return
