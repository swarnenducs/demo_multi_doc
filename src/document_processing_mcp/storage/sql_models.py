"""SQLAlchemy models for document job, accuracy, master-data, and call-log rows (same tables as ip_api)."""

from __future__ import annotations

from sqlalchemy import Float, Index, Unicode, UnicodeText
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DOCUMENT_MCP = "document_process_mcp"
JOB_TABLE_DOCUMENT_MCP = "job_table_document_mcp"
ACCURACY_TABLE_DOCUMENT_MCP = "accuracy_report_document_mcp"
MASTER_DATA_TABLE = "master_data"


class Base(DeclarativeBase):
    pass


class DocumentJob(Base):
    __tablename__ = JOB_TABLE_DOCUMENT_MCP

    id: Mapped[str] = mapped_column(Unicode(64), primary_key=True)
    mcp: Mapped[str] = mapped_column(Unicode(64), nullable=False, default=DOCUMENT_MCP)
    status: Mapped[str] = mapped_column(Unicode(32), nullable=False)
    template_path: Mapped[str] = mapped_column(Unicode(1024), nullable=False)
    data_path: Mapped[str] = mapped_column(Unicode(1024), nullable=False)
    output_path: Mapped[str | None] = mapped_column(Unicode(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    confidence_json: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    validation_json: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    mapper_llm: Mapped[str | None] = mapped_column(Unicode(256), nullable=True)
    validator_llm: Mapped[str | None] = mapped_column(Unicode(256), nullable=True)
    created_at: Mapped[str] = mapped_column(Unicode(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(Unicode(64), nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Unicode(64), nullable=True)
    extraction_validation_json: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    result_json: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    xid: Mapped[str | None] = mapped_column(Unicode(64), nullable=True)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    elapsed: Mapped[str | None] = mapped_column(Unicode(64), nullable=True)

    __table_args__ = (Index("idx_job_table_document_mcp_xid", "xid"),)


class DocumentAccuracyReport(Base):
    """Per-job accuracy / confidence report (document processing)."""

    __tablename__ = ACCURACY_TABLE_DOCUMENT_MCP

    job_id: Mapped[str] = mapped_column(Unicode(64), primary_key=True)
    mcp: Mapped[str | None] = mapped_column(Unicode(64), nullable=True)
    xid: Mapped[str | None] = mapped_column(Unicode(64), nullable=True)
    overall_confidence_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_confidence_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mapping_confidence_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    table_mapping_confidence_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    generation_integrity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    generation_confidence_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_score_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_passed: Mapped[str | None] = mapped_column(Unicode(8), nullable=True)
    validation_passed: Mapped[str | None] = mapped_column(Unicode(8), nullable=True)
    mapper_llm: Mapped[str | None] = mapped_column(Unicode(256), nullable=True)
    validator_llm: Mapped[str | None] = mapped_column(Unicode(256), nullable=True)
    notes: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    confidence_json: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    validation_json: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    extraction_validation_json: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    scores_pct_json: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    created_at: Mapped[str] = mapped_column(Unicode(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(Unicode(64), nullable=False)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    elapsed: Mapped[str | None] = mapped_column(Unicode(64), nullable=True)


class MasterData(Base):
    """Reusable template blocks (legal notice, sales attn) filled from SQL."""

    __tablename__ = MASTER_DATA_TABLE

    id: Mapped[str] = mapped_column(Unicode(64), primary_key=True)
    placeholder_key: Mapped[str] = mapped_column(Unicode(256), nullable=False)
    category: Mapped[str] = mapped_column(Unicode(64), nullable=False)
    content: Mapped[str] = mapped_column(UnicodeText, nullable=False)
    active: Mapped[str] = mapped_column(Unicode(8), nullable=False, default="true")
    updated_at: Mapped[str] = mapped_column(Unicode(64), nullable=False)

    __table_args__ = (
        Index("idx_master_data_placeholder", "placeholder_key", unique=True),
        Index("idx_master_data_category", "category"),
    )


class CallLog(Base):
    """xid-correlated HTTP / tool / LLM trace rows (same table as ip_api)."""

    __tablename__ = "call_logs"

    id: Mapped[str] = mapped_column(Unicode(64), primary_key=True)
    xid: Mapped[str] = mapped_column(Unicode(64), nullable=False)
    job_id: Mapped[str | None] = mapped_column(Unicode(64), nullable=True)
    kind: Mapped[str] = mapped_column(Unicode(64), nullable=False)
    name: Mapped[str] = mapped_column(Unicode(512), nullable=False)
    status: Mapped[str] = mapped_column(Unicode(32), nullable=False)
    provider: Mapped[str | None] = mapped_column(Unicode(64), nullable=True)
    model: Mapped[str | None] = mapped_column(Unicode(128), nullable=True)
    request_json: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    response_json: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    error_message: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta_json: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    created_at: Mapped[str] = mapped_column(Unicode(64), nullable=False)

    __table_args__ = (
        Index("idx_call_logs_xid", "xid"),
        Index("idx_call_logs_job_id", "job_id"),
    )
