"""SQLAlchemy writer for xid-correlated trace rows.

Same engine resolution as the job sink, so trace rows follow the configured
backend: local SQLite by default, Azure SQL when ``AZURE_SQL_*`` is set.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from document_processing_mcp.storage.db import ensure_schema, get_session_factory
from document_processing_mcp.storage.sql_models import CallLog

_JSON_COLUMNS = ("request_json", "response_json", "meta_json")


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


def _row_to_dict(row: CallLog) -> dict[str, Any]:
    data: dict[str, Any] = {
        "log_id": row.id,
        "xid": row.xid,
        "job_id": row.job_id,
        "kind": row.kind,
        "name": row.name,
        "status": row.status,
        "provider": row.provider,
        "model": row.model,
        "error_message": row.error_message,
        "latency_ms": row.latency_ms,
        "created_at": row.created_at,
    }
    for column in _JSON_COLUMNS:
        raw = getattr(row, column)
        data[column] = raw
        key = column.removesuffix("_json")
        if not raw:
            data[key] = None
            continue
        try:
            data[key] = json.loads(raw)
        except json.JSONDecodeError:
            data[key] = raw
    return data


class CallLogStore:
    """Persist trace rows through SQLAlchemy (never a raw driver)."""

    def __init__(self) -> None:
        ensure_schema()

    def insert_call_log(
        self,
        *,
        xid: str,
        kind: str,
        name: str,
        status: str,
        job_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        request_json: str | None = None,
        response_json: str | None = None,
        error_message: str | None = None,
        latency_ms: float | None = None,
        meta_json: str | None = None,
        log_id: str | None = None,
    ) -> str:
        lid = log_id or str(uuid.uuid4())
        with _session() as session:
            session.add(
                CallLog(
                    id=lid,
                    xid=xid,
                    job_id=job_id,
                    kind=kind,
                    name=name,
                    status=status,
                    provider=provider,
                    model=model,
                    request_json=request_json,
                    response_json=response_json,
                    error_message=error_message,
                    latency_ms=latency_ms,
                    meta_json=meta_json,
                    created_at=_now_iso(),
                )
            )
        return lid

    def list_call_logs_by_xid(self, xid: str, *, limit: int = 200) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), 500))
        with _session() as session:
            rows = session.scalars(
                select(CallLog)
                .where(CallLog.xid == xid)
                .order_by(CallLog.created_at.asc())
                .limit(capped)
            ).all()
        return [_row_to_dict(row) for row in rows]
