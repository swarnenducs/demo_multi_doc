"""Blob refs + MCP job sink (local SQLite, no Azure)."""

from __future__ import annotations

from pathlib import Path

from document_processing_mcp.core.settings import reload_settings
from document_processing_mcp.services.document_job import materialize_input
from document_processing_mcp.storage.blob_store import BlobStore, is_blob_ref
from document_processing_mcp.storage.job_sink import JobSink


def _isolate_local(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_BASE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FILE_STORAGE_BACKEND", "local")
    monkeypatch.delenv("AZURE_SQL_SERVER", raising=False)
    monkeypatch.delenv("AZURE_SQL_PASSWORD", raising=False)
    monkeypatch.delenv("SQLALCHEMY_DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_KEY", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_SAS_TOKEN", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_SAS_URL", raising=False)
    reload_settings()


def test_blob_paths_segregate_upload_and_download(tmp_path: Path, monkeypatch) -> None:
    _isolate_local(tmp_path, monkeypatch)
    from document_processing_mcp.storage.blob_store import blob_job_root, blob_name_for_job

    assert blob_job_root("abc") == "jobs/abc"
    assert blob_name_for_job("abc", "template.docx", kind="upload") == (
        "jobs/abc/upload/template.docx"
    )
    assert blob_name_for_job("abc", "out.docx", kind="download") == (
        "jobs/abc/download/out.docx"
    )
    assert is_blob_ref("blob://docuploadsolution/jobs/abc/upload/template.docx")
    assert is_blob_ref("blob://docuploadsolution/jobs/abc/download/out.docx")


def test_blob_auth_mode_sas_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_BASE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FILE_STORAGE_BACKEND", "azure_blob")
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_KEY", raising=False)
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "mystorageacct")
    monkeypatch.setenv("AZURE_STORAGE_SAS_TOKEN", "?sv=2024-11-04&sig=abc")
    monkeypatch.delenv("AZURE_STORAGE_SAS_URL", raising=False)
    reload_settings()
    store = BlobStore()
    assert store.enabled is True
    assert store.auth_mode() == "sas_token"


def test_blob_auth_mode_sas_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_BASE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FILE_STORAGE_BACKEND", "azure_blob")
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_KEY", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_SAS_TOKEN", raising=False)
    monkeypatch.setenv(
        "AZURE_STORAGE_SAS_URL",
        "https://mystorageacct.blob.core.windows.net/docuploadsolution?sv=2024-11-04&sig=abc",
    )
    reload_settings()
    store = BlobStore()
    assert store.enabled is True
    assert store.auth_mode() == "sas_url"


def test_blob_auth_uses_sas_when_account_key_also_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_BASE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FILE_STORAGE_BACKEND", "azure_blob")
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "mystorageacct")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_KEY", "fake-key")
    monkeypatch.setenv("AZURE_STORAGE_SAS_TOKEN", "sv=2024-11-04&sig=abc")
    monkeypatch.delenv("AZURE_STORAGE_SAS_URL", raising=False)
    reload_settings()
    store = BlobStore()
    assert store.auth_mode() == "sas_token"


def test_blob_auth_sas_with_connection_string_account_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_BASE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FILE_STORAGE_BACKEND", "azure_blob")
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_KEY", raising=False)
    monkeypatch.setenv(
        "AZURE_STORAGE_CONNECTION_STRING",
        "DefaultEndpointsProtocol=https;AccountName=mystorageacct;AccountKey=fake-key;"
        "EndpointSuffix=core.windows.net",
    )
    monkeypatch.setenv("AZURE_STORAGE_SAS_TOKEN", "sv=2024-11-04&sig=abc")
    monkeypatch.delenv("AZURE_STORAGE_SAS_URL", raising=False)
    reload_settings()
    store = BlobStore()
    assert store.auth_mode() == "sas_token"


def test_persist_job_inputs_stays_local_without_azure(tmp_path: Path, monkeypatch) -> None:
    _isolate_local(tmp_path, monkeypatch)
    tpl = tmp_path / "template.docx"
    data = tmp_path / "data.json"
    out = tmp_path / "out.docx"
    tpl.write_bytes(b"docx")
    data.write_text("{}", encoding="utf-8")
    store = BlobStore()
    assert store.enabled is False
    tpl_ref, data_ref, out_ref = store.persist_job_inputs("job-1", tpl, data, out)
    assert tpl_ref == str(tpl)
    assert data_ref == str(data)
    assert out_ref == str(out)


def test_materialize_input_copies_local_file(tmp_path: Path, monkeypatch) -> None:
    _isolate_local(tmp_path, monkeypatch)
    src = tmp_path / "src.json"
    dest = tmp_path / "work" / "data.json"
    src.write_text('{"a": 1}', encoding="utf-8")
    copied = materialize_input(str(src), dest)
    assert copied.read_text(encoding="utf-8") == '{"a": 1}'


def test_format_elapsed_ms() -> None:
    from document_processing_mcp.services.document_job import format_elapsed_ms

    assert format_elapsed_ms(450) == "0.5 s"
    assert format_elapsed_ms(12_300) == "12.3 s"
    assert format_elapsed_ms(65_400) == "1m 5.4s"
    assert format_elapsed_ms(3_661_000) == "1h 1m 1s"


def test_job_sink_completes_accuracy(tmp_path: Path, monkeypatch) -> None:
    _isolate_local(tmp_path, monkeypatch)
    sink = JobSink()
    sink.ensure_job(
        "job-sink-1",
        template_path="blob://docuploadsolution/jobs/job-sink-1/upload/template.docx",
        data_path="blob://docuploadsolution/jobs/job-sink-1/upload/data.json",
        output_path="blob://docuploadsolution/jobs/job-sink-1/download/out.docx",
        xid="xid-1",
    )
    sink.complete_job(
        "job-sink-1",
        output_path="blob://docuploadsolution/jobs/job-sink-1/download/out.docx",
        confidence={
            "overall_confidence": 0.8,
            "scores_pct": {"overall_confidence_pct": 80.0},
        },
        xid="xid-1",
    )
    from document_processing_mcp.storage.db import get_session_factory
    from document_processing_mcp.storage.sql_models import DocumentAccuracyReport, DocumentJob

    session = get_session_factory()()
    try:
        row = session.get(DocumentJob, "job-sink-1")
        assert row is not None
        assert row.status == "completed"
        report = session.get(DocumentAccuracyReport, "job-sink-1")
        assert report is not None
        assert report.overall_confidence_pct == 80.0
    finally:
        session.close()
    reload_settings()
