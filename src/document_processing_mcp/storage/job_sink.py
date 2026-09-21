"""Update document_jobs + document_accuracy_reports after MCP processing.

ip_api creates the pending row. MCP completes it when ``job_id`` is passed
(needed when MAF calls MCP with no API job runner). SQL failures are logged
and returned as ``False`` so generation can still succeed.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator

from sqlalchemy.orm import Session

from document_processing_mcp.storage.db import ensure_schema, get_session_factory
from document_processing_mcp.storage.sql_models import (
    DOCUMENT_MCP,
    DocumentAccuracyReport,
    DocumentJob,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def _session() -> Iterator[Session]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def _upsert_accuracy_report(
    session: Session,
    *,
    job_id: str,
    xid: str | None,
    confidence: dict[str, Any] | None,
    validation: dict[str, Any] | None,
    extraction_validation: dict[str, Any] | None,
    mapper_llm: str | None,
    validator_llm: str | None,
    elapsed_ms: float | None = None,
    elapsed: str | None = None,
    mcp: str | None = None,
) -> None:
    now = _now_iso()
    scores = (confidence or {}).get("scores_pct") if isinstance(confidence, dict) else None
    if not isinstance(scores, dict):
        scores = {}

    def _pct(*keys: str) -> float | None:
        for key in keys:
            val = scores.get(key)
            if isinstance(val, (int, float)):
                return float(val)
        return None

    def _from_unit(key: str) -> float | None:
        raw = (confidence or {}).get(key)
        if isinstance(raw, (int, float)):
            return round(float(raw) * 100.0, 1)
        return None

    def _flag(value: Any) -> str | None:
        if value is True:
            return "true"
        if value is False:
            return "false"
        return None

    row = session.get(DocumentAccuracyReport, job_id)
    payload = dict(
        xid=xid,
        overall_confidence_pct=_pct("overall_confidence_pct") or _from_unit("overall_confidence"),
        extraction_confidence_pct=_pct("extraction_confidence_pct")
        or _from_unit("extraction_confidence"),
        mapping_confidence_pct=_pct("placeholder_mapping_confidence_pct")
        or _from_unit("mapping_confidence"),
        coverage_pct=_pct("placeholder_coverage_pct") or _from_unit("coverage_score"),
        table_mapping_confidence_pct=_pct("table_mapping_confidence_pct")
        or _from_unit("table_mapping_confidence"),
        generation_integrity_pct=_pct("generation_integrity_pct")
        or _from_unit("generation_integrity"),
        generation_confidence_pct=_pct("generation_confidence_pct")
        or _from_unit("generation_confidence"),
        validation_score_pct=_pct("validation_score_pct") or _from_unit("validation_score"),
        extraction_passed=_flag((confidence or {}).get("extraction_passed")),
        validation_passed=_flag(
            (confidence or {}).get("validation_passed")
            if confidence and confidence.get("validation_passed") is not None
            else (validation or {}).get("passed")
        ),
        mapper_llm=mapper_llm or (confidence or {}).get("mapper_llm"),
        validator_llm=validator_llm or (confidence or {}).get("validator_llm"),
        notes=((confidence or {}).get("notes") or "")[:2000] or None,
        confidence_json=None,
        validation_json=json.dumps(validation) if validation else None,
        extraction_validation_json=json.dumps(extraction_validation)
        if extraction_validation
        else None,
        scores_pct_json=json.dumps(
            {
                key: value
                for key, value in scores.items()
                if isinstance(value, (int, float, str, bool)) or value is None
            }
        )
        if scores
        else None,
        elapsed_ms=elapsed_ms,
        elapsed=elapsed,
        mcp=mcp or DOCUMENT_MCP,
        updated_at=now,
    )
    if row is None:
        session.add(DocumentAccuracyReport(job_id=job_id, created_at=now, **payload))
    else:
        for key, value in payload.items():
            setattr(row, key, value)


class JobSink:
    """Thin SQL writer for document MCP (does not own session/voice tables)."""

    def __init__(self) -> None:
        ensure_schema()

    def ensure_job(
        self,
        job_id: str,
        *,
        template_path: str,
        data_path: str,
        output_path: str | None,
        xid: str | None = None,
    ) -> None:
        now = _now_iso()
        with _session() as session:
            row = session.get(DocumentJob, job_id)
            if row is not None:
                return
            session.add(
                DocumentJob(
                    id=job_id,
                    mcp=DOCUMENT_MCP,
                    status="processing",
                    template_path=template_path,
                    data_path=data_path,
                    output_path=output_path,
                    created_at=now,
                    updated_at=now,
                    xid=xid,
                )
            )

    def update_status(self, job_id: str, status: str, error: str | None = None) -> None:
        now = _now_iso()
        with _session() as session:
            row = session.get(DocumentJob, job_id)
            if row is None:
                return
            row.status = status
            row.updated_at = now
            if error:
                row.error_message = error

    def persist_output_path(self, job_id: str, output_path: str) -> None:
        with _session() as session:
            row = session.get(DocumentJob, job_id)
            if row is None:
                return
            row.output_path = output_path
            row.updated_at = _now_iso()

    def complete_job(
        self,
        job_id: str,
        *,
        output_path: str | None = None,
        confidence: dict[str, Any] | None = None,
        validation: dict[str, Any] | None = None,
        extraction_validation: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        mapper_llm: str | None = None,
        validator_llm: str | None = None,
        error: str | None = None,
        xid: str | None = None,
    ) -> None:
        now = _now_iso()
        status = "failed" if error else "completed"
        elapsed_ms = None
        elapsed = None
        result_dict = result if isinstance(result, dict) else {}
        raw_ms = result_dict.get("elapsed_ms")
        if isinstance(raw_ms, (int, float)):
            elapsed_ms = float(raw_ms)
        raw_elapsed = result_dict.get("elapsed")
        if isinstance(raw_elapsed, str) and raw_elapsed.strip():
            elapsed = raw_elapsed.strip()
        with _session() as session:
            row = session.get(DocumentJob, job_id)
            if row is None:
                session.add(
                    DocumentJob(
                        id=job_id,
                        mcp=DOCUMENT_MCP,
                        status=status,
                        template_path=str(result_dict.get("template_path") or ""),
                        data_path=str(result_dict.get("data_path") or ""),
                        output_path=output_path,
                        created_at=now,
                        updated_at=now,
                        completed_at=now,
                        xid=xid,
                    )
                )
                session.flush()
                row = session.get(DocumentJob, job_id)
            if row is None:
                return
            row.status = status
            row.updated_at = now
            row.completed_at = now
            row.error_message = error
            row.confidence_json = json.dumps(confidence) if confidence else None
            row.validation_json = json.dumps(validation) if validation else None
            row.extraction_validation_json = (
                json.dumps(extraction_validation) if extraction_validation else None
            )
            row.result_json = json.dumps(result) if result else None
            row.mapper_llm = mapper_llm
            row.validator_llm = validator_llm
            row.elapsed_ms = elapsed_ms
            row.elapsed = elapsed
            if not getattr(row, "mcp", None):
                row.mcp = DOCUMENT_MCP
            if output_path:
                row.output_path = output_path
            if xid and not row.xid:
                row.xid = xid
            if confidence or validation or extraction_validation:
                _upsert_accuracy_report(
                    session,
                    job_id=job_id,
                    xid=xid or row.xid,
                    confidence=confidence,
                    validation=validation,
                    extraction_validation=extraction_validation,
                    mapper_llm=mapper_llm,
                    validator_llm=validator_llm,
                    elapsed_ms=elapsed_ms,
                    elapsed=elapsed,
                    mcp=getattr(row, "mcp", None) or DOCUMENT_MCP,
                )


def try_job_sink(job_id: str | None) -> JobSink | None:
    if not job_id:
        return None
    try:
        return JobSink()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Document MCP job sink unavailable: %s", exc)
        return None
