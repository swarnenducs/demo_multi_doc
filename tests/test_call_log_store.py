"""Document MCP trace rows persist through SQLAlchemy, not raw sqlite3."""

from __future__ import annotations

from pathlib import Path

from document_processing_mcp.core.settings import reload_settings
from document_processing_mcp.storage.call_log_store import CallLogStore
from document_processing_mcp.storage.db import build_database_url, engine_uses_mssql


def _isolate_local(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_BASE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(tmp_path / "app.db"))
    for key in ("AZURE_SQL_SERVER", "AZURE_SQL_PASSWORD", "SQLALCHEMY_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    reload_settings()


def test_sqlite_is_the_default_backend(tmp_path: Path, monkeypatch) -> None:
    _isolate_local(tmp_path, monkeypatch)

    url = build_database_url()

    assert url.startswith("sqlite:///")
    assert engine_uses_mssql(url) is False


def test_trace_rows_round_trip(tmp_path: Path, monkeypatch) -> None:
    _isolate_local(tmp_path, monkeypatch)
    store = CallLogStore()

    first = store.insert_call_log(
        xid="xid-doc",
        kind="llm",
        name="mapper",
        status="ok",
        request_json='{"prompt": "map"}',
        latency_ms=3.5,
    )
    second = store.insert_call_log(
        xid="xid-doc", kind="tool", name="generate_document", status="error", error_message="boom"
    )

    rows = store.list_call_logs_by_xid("xid-doc")

    assert {row["log_id"] for row in rows} == {first, second}
    mapper = next(row for row in rows if row["log_id"] == first)
    assert mapper["request"] == {"prompt": "map"}
    assert mapper["latency_ms"] == 3.5
    failed = next(row for row in rows if row["log_id"] == second)
    assert failed["status"] == "error"
    assert failed["error_message"] == "boom"


def test_trace_log_writes_through_the_store(tmp_path: Path, monkeypatch) -> None:
    _isolate_local(tmp_path, monkeypatch)
    from document_processing_mcp.core.request_context import reset_xid, set_xid
    from document_processing_mcp.services.trace_log import log_event

    token = set_xid("xid-trace")
    try:
        log_id = log_event(kind="tool", name="health", status="ok")
    finally:
        reset_xid(token)

    assert log_id is not None
    rows = CallLogStore().list_call_logs_by_xid("xid-trace")
    assert [row["log_id"] for row in rows] == [log_id]
