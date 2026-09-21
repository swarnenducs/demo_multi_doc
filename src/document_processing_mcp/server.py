"""Separate FastMCP server: ``document_process_mcp`` (document LangGraph pipeline only)."""

from __future__ import annotations

import os
import time

from fastmcp.dependencies import Depends

from document_processing_mcp.core.context import ApplicationContext, build_application_context
from document_processing_mcp.core.dependencies import get_app_context, set_app_context
from document_processing_mcp.mcp.base import BaseAgentMCPServer
from document_processing_mcp.models.mcp_responses import GenerateDocumentResponse, McpHealthResponse
from document_processing_mcp.storage.db import build_database_url, engine_uses_mssql

MCP_NAME = "document_process_mcp"


class DocumentProcessMCP(BaseAgentMCPServer):
    """Standalone MCP: Word template + JSON → filled .docx (LangGraph)."""

    def __init__(self, *, host: str | None = None, port: int | None = None) -> None:
        from document_processing_mcp.flow_debug import install_flow_logger

        install_flow_logger()
        port = port if port is not None else int(os.getenv("DOCUMENT_MCP_PORT", "8001"))
        set_app_context(build_application_context())
        super().__init__(
            name=MCP_NAME,
            instructions=(
                "MCP server document_process_mcp. "
                "Fill a Word .docx template from JSON via LangGraph. "
                "Pass Azure Blob refs (blob://container/...) or local paths. "
                "Tools: health, generate_document."
            ),
            host=host,
            port=port,
            version="0.1.0",
        )

    def register_tools(self) -> None:
        self.add_tool(self.health)
        self.add_tool(self.generate_document)

    def health(
        self,
        app_context: ApplicationContext = Depends(get_app_context),
    ) -> McpHealthResponse:
        """Liveness check for document_process_mcp."""
        blobs = app_context.blob_store
        db_url = build_database_url()
        return McpHealthResponse(
            ok=True,
            mcp=MCP_NAME,
            agent="document_process_mcp",
            transport="http|stdio",
            host=self.host,
            port=self.port,
            blob_enabled=blobs.enabled,
            blob_container=blobs.container_name if blobs.enabled else None,
            azure_sql=engine_uses_mssql(db_url),
        )

    def generate_document(
        self,
        template_path: str,
        data_path: str | None = None,
        data_json: str | None = None,
        output_path: str | None = None,
        job_id: str | None = None,
        xid: str | None = None,
        skip_validation: bool = False,
        skip_extraction_validation: bool = False,
        max_retries: int | None = None,
        validation_threshold: float | None = None,
        optimized_flow: bool | None = None,
    ) -> GenerateDocumentResponse:
        """
        Run the LangGraph document pipeline (extract → map → generate → validate).

        ``template_path`` / ``data_path`` / ``output_path`` may be local files or
        Azure Blob refs (``blob://container/jobs/{job_id}/upload|download/...``).
        When ``job_id`` is set, MCP downloads, processes, uploads the .docx, and
        updates ``document_jobs`` + ``document_accuracy_reports``.
        Provide either ``data_path`` or ``data_json``.
        ``optimized_flow`` uses ``llm_optimization.json`` (off unless set or
        ``DOCUMENT_LLM_OPTIMIZATION_ENABLED=true``).
        """
        from document_processing_mcp.core.request_context import bind_xid, require_xid
        from document_processing_mcp.flow_debug import flow_breakpoint
        from document_processing_mcp.services.document_job import run_generate_document
        from document_processing_mcp.services.trace_log import log_event

        corr = (xid or "").strip() or require_xid()
        started = time.perf_counter()
        flow_breakpoint(
            "mcp_generate_document",
            template_path=template_path,
            data_path=data_path,
            job_id=job_id,
            xid=corr,
        )
        from document_processing_mcp.core.settings import settings as mcp_settings

        quality = mcp_settings()
        use_opt = (
            quality.document_llm_optimization_enabled
            if optimized_flow is None
            else optimized_flow
        )
        if use_opt:
            retries = max_retries
            threshold = validation_threshold
        else:
            retries = quality.document_max_retries if max_retries is None else max_retries
            threshold = (
                quality.document_validation_threshold
                if validation_threshold is None
                else validation_threshold
            )
        with bind_xid(corr, job_id=job_id):
            payload_out = run_generate_document(
                template_path=template_path,
                data_path=data_path,
                data_json=data_json,
                output_path=output_path,
                job_id=job_id,
                xid=corr,
                skip_validation=skip_validation,
                skip_extraction_validation=skip_extraction_validation,
                max_retries=retries,
                validation_threshold=threshold,
                optimized_flow=use_opt,
            )
            log_event(
                kind="mcp_tool",
                name="generate_document",
                request_payload={
                    "template_path": template_path,
                    "data_path": data_path,
                    "output_path": output_path,
                    "job_id": job_id,
                },
                response_payload={
                    "ok": payload_out.ok,
                    "status": payload_out.status,
                    "errors": payload_out.errors,
                    "output_path": payload_out.output_path,
                    "db_updated": payload_out.db_updated,
                },
                status="ok" if payload_out.ok else "error",
                latency_ms=(time.perf_counter() - started) * 1000.0,
                xid=corr,
                job_id=job_id,
            )
            return payload_out.model_copy(update={"mcp": MCP_NAME})


def main(argv: list[str] | None = None) -> int:
    import argparse

    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="document_process_mcp FastMCP server")
    parser.add_argument(
        "--transport",
        choices=["http", "stdio"],
        default=os.getenv("DOCUMENT_MCP_TRANSPORT", "http"),
    )
    parser.add_argument("--host", default=os.getenv("DOCUMENT_MCP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("DOCUMENT_MCP_PORT", "8001")),
    )
    parser.add_argument("--banner", action="store_true")
    args = parser.parse_args(argv)

    server = DocumentProcessMCP(host=args.host, port=args.port)
    if args.transport == "stdio":
        server.run_stdio(show_banner=args.banner)
    else:
        print(f"{MCP_NAME} listening on http://{args.host}:{args.port}/mcp")
        server.run_http(show_banner=args.banner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
