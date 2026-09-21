"""Dynaconf fills missing env from .env; Pydantic Settings reads process env."""

from __future__ import annotations

import os
from pathlib import Path

from document_processing_mcp.core.dynaconf_loader import _export_scalar, apply_dynaconf_from_env_files
from document_processing_mcp.core.settings import reload_settings


def test_export_scalar_does_not_override_process_env(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_SQL_SERVER", "from-process")
    _export_scalar("AZURE_SQL_SERVER", "from-dotenv", override=False)
    assert os.environ["AZURE_SQL_SERVER"] == "from-process"


def test_pydantic_settings_reads_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORAGE_BASE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FILE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("IPP_FORCE_SQLITE", "1")
    monkeypatch.setenv("AZURE_SQL_SERVER", "ipp-sql-serv.database.windows.net")
    monkeypatch.setenv("AZURE_SQL_PASSWORD", "secret")
    cfg = reload_settings()
    assert cfg.uses_azure_sql is False
    assert cfg.file_storage_backend == "local"
    assert cfg.storage_base_path == (tmp_path / "storage").resolve()
    assert cfg.document_marker_synthesis_enabled is True


def test_marker_synthesis_env_off(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORAGE_BASE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FILE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("IPP_FORCE_SQLITE", "1")
    monkeypatch.setenv("DOCUMENT_MARKER_SYNTHESIS_ENABLED", "false")
    cfg = reload_settings()
    assert cfg.document_marker_synthesis_enabled is False


def test_marker_synthesis_alias_non_tag(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORAGE_BASE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("SQLITE_DATABASE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FILE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("IPP_FORCE_SQLITE", "1")
    monkeypatch.delenv("DOCUMENT_MARKER_SYNTHESIS_ENABLED", raising=False)
    monkeypatch.setenv("DOCUMENT_NON_TAG_ENABLED", "false")
    cfg = reload_settings()
    assert cfg.document_marker_synthesis_enabled is False


def test_apply_dynaconf_returns_loader() -> None:
    box = apply_dynaconf_from_env_files()
    assert box is not None
