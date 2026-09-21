"""SQLAlchemy 2.x engine for Azure SQL (document jobs) or local SQLite."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from document_processing_mcp.core.settings import settings

logger = logging.getLogger(__name__)

_UNUSED_AZURE_TABLES = ("legal_entities", "pricelists")
_LEGACY_JOB_TABLE = "document_jobs"
_LEGACY_ACCURACY_TABLE = "document_accuracy_reports"
_LEGACY_VIEWS = (
    "document_jobs_overview",
    "document_accuracy_overview",
)


_engine_cache: dict[str, Engine] = {}
_session_cache: dict[str, sessionmaker[Session]] = {}


def _odbc_escape(value: str) -> str:
    """ODBC brace-wrap: closing braces in passwords must be doubled."""
    return value.replace("}", "}}")


def build_database_url(*, sqlite_path: Path | None = None) -> str:
    """Resolve SQLAlchemy URL: explicit URL > Azure SQL parts > SQLite file."""
    if sqlite_path is not None:
        return f"sqlite:///{Path(sqlite_path).expanduser().resolve()}"

    cfg = settings()
    if cfg.sqlalchemy_database_url:
        return cfg.sqlalchemy_database_url.strip()

    if cfg.azure_sql_server and cfg.azure_sql_password:
        host = cfg.azure_sql_server.strip()
        if host.lower().startswith("tcp:"):
            host = host[4:]
        host = host.split(",")[0]
        user = cfg.azure_sql_user
        password = cfg.azure_sql_password
        database = cfg.azure_sql_database
        dialect = (cfg.azure_sql_dialect or "pyodbc").strip().lower()
        if dialect in {"pyodbc", "odbc"}:
            driver = cfg.azure_sql_odbc_driver
            odbc = (
                f"Driver={{{driver}}};"
                f"Server=tcp:{host},1433;"
                f"Database={database};"
                f"Uid={user};"
                f"Pwd={{{_odbc_escape(password)}}};"
                "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
            )
            return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc)}"
        return (
            f"mssql+pymssql://{quote_plus(user)}:{quote_plus(password)}"
            f"@{host}:1433/{quote_plus(database)}"
        )

    return f"sqlite:///{cfg.sqlite_database_path}"


def engine_uses_mssql(url: str | None = None) -> bool:
    raw = url or build_database_url()
    return make_url(raw).get_backend_name() in {"mssql"}


def get_engine(*, sqlite_path: Path | None = None) -> Engine:
    url = build_database_url(sqlite_path=sqlite_path)
    cached = _engine_cache.get(url)
    if cached is not None:
        return cached
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite:///"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_recycle"] = 1800
        if url.startswith("mssql+pyodbc"):
            kwargs["use_setinputsizes"] = False
    engine = create_engine(url, **kwargs)
    _engine_cache[url] = engine
    return engine


def get_session_factory(*, sqlite_path: Path | None = None) -> sessionmaker[Session]:
    url = build_database_url(sqlite_path=sqlite_path)
    cached = _session_cache.get(url)
    if cached is not None:
        return cached
    factory = sessionmaker(
        bind=get_engine(sqlite_path=sqlite_path),
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    _session_cache[url] = factory
    return factory


def _exec_mssql(engine: Engine, sql: str) -> None:
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Azure SQL statement skipped: %s", exc)


def _prepare_mssql_schema(engine: Engine) -> None:
    """Rename legacy tables, drop leftover catalog tables, drop old portal views."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    views = set(insp.get_view_names())

    for view in _LEGACY_VIEWS:
        if view in views:
            _exec_mssql(engine, f"DROP VIEW IF EXISTS {view}")

    if _LEGACY_JOB_TABLE in tables and "job_table_document_mcp" not in tables:
        _exec_mssql(engine, "EXEC sp_rename 'document_jobs', 'job_table_document_mcp'")
        tables.discard(_LEGACY_JOB_TABLE)
        tables.add("job_table_document_mcp")
    if _LEGACY_ACCURACY_TABLE in tables and "accuracy_report_document_mcp" not in tables:
        _exec_mssql(
            engine,
            "EXEC sp_rename 'document_accuracy_reports', 'accuracy_report_document_mcp'",
        )

    if "job_table_document_mcp" in tables or _LEGACY_JOB_TABLE in tables:
        _exec_mssql(
            engine,
            "DROP INDEX IF EXISTS idx_document_jobs_xid ON job_table_document_mcp",
        )
        _exec_mssql(
            engine,
            "DROP INDEX IF EXISTS idx_document_jobs_xid ON document_jobs",
        )

    for unused in _UNUSED_AZURE_TABLES:
        if unused in tables:
            _exec_mssql(engine, f"DROP TABLE IF EXISTS {unused}")
            logger.info("Dropped unused Azure SQL table %s", unused)


def _migrate_mssql_document_tables(engine: Engine) -> None:
    """NVARCHAR + elapsed columns + portal overview views (no VARCHAR(MAX) JSON)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    table_names = set(insp.get_table_names())

    def _col_info(table: str) -> dict[str, dict]:
        if table not in table_names:
            return {}
        return {c["name"]: c for c in insp.get_columns(table)}

    def _already_unicode(col: dict | None) -> bool:
        if not col:
            return False
        typ = str(col.get("type") or "").upper()
        return any(token in typ for token in ("NVARCHAR", "NCHAR", "UNICODE", "NTEXT"))

    statements: list[str] = []
    job_info = _col_info("job_table_document_mcp")
    acc_info = _col_info("accuracy_report_document_mcp")
    job_cols = set(job_info)
    acc_cols = set(acc_info)

    if "job_table_document_mcp" in table_names:
        if "elapsed_ms" not in job_cols:
            statements.append("ALTER TABLE job_table_document_mcp ADD elapsed_ms FLOAT NULL")
        if "elapsed" not in job_cols:
            statements.append("ALTER TABLE job_table_document_mcp ADD elapsed NVARCHAR(64) NULL")
        if "mcp" not in job_cols:
            statements.append("ALTER TABLE job_table_document_mcp ADD mcp NVARCHAR(64) NULL")
            statements.append(
                "UPDATE job_table_document_mcp SET mcp = 'document_process_mcp' WHERE mcp IS NULL"
            )
        for col, spec in (
            ("status", "NVARCHAR(32) NOT NULL"),
            ("template_path", "NVARCHAR(1024) NOT NULL"),
            ("data_path", "NVARCHAR(1024) NOT NULL"),
            ("output_path", "NVARCHAR(1024) NULL"),
            ("error_message", "NVARCHAR(MAX) NULL"),
            ("confidence_json", "NVARCHAR(MAX) NULL"),
            ("validation_json", "NVARCHAR(MAX) NULL"),
            ("mapper_llm", "NVARCHAR(256) NULL"),
            ("validator_llm", "NVARCHAR(256) NULL"),
            ("created_at", "NVARCHAR(64) NOT NULL"),
            ("updated_at", "NVARCHAR(64) NOT NULL"),
            ("completed_at", "NVARCHAR(64) NULL"),
            ("extraction_validation_json", "NVARCHAR(MAX) NULL"),
            ("result_json", "NVARCHAR(MAX) NULL"),
            ("xid", "NVARCHAR(64) NULL"),
            ("elapsed", "NVARCHAR(64) NULL"),
        ):
            if col in job_cols and not _already_unicode(job_info.get(col)):
                statements.append(f"ALTER TABLE job_table_document_mcp ALTER COLUMN {col} {spec}")

    if "accuracy_report_document_mcp" in table_names:
        if "elapsed_ms" not in acc_cols:
            statements.append("ALTER TABLE accuracy_report_document_mcp ADD elapsed_ms FLOAT NULL")
        if "elapsed" not in acc_cols:
            statements.append(
                "ALTER TABLE accuracy_report_document_mcp ADD elapsed NVARCHAR(64) NULL"
            )
        if "mcp" not in acc_cols:
            statements.append("ALTER TABLE accuracy_report_document_mcp ADD mcp NVARCHAR(64) NULL")
            statements.append(
                "UPDATE accuracy_report_document_mcp SET mcp = 'document_process_mcp' WHERE mcp IS NULL"
            )
        for col, spec in (
            ("xid", "NVARCHAR(64) NULL"),
            ("extraction_passed", "NVARCHAR(8) NULL"),
            ("validation_passed", "NVARCHAR(8) NULL"),
            ("mapper_llm", "NVARCHAR(256) NULL"),
            ("validator_llm", "NVARCHAR(256) NULL"),
            ("notes", "NVARCHAR(MAX) NULL"),
            ("confidence_json", "NVARCHAR(MAX) NULL"),
            ("validation_json", "NVARCHAR(MAX) NULL"),
            ("extraction_validation_json", "NVARCHAR(MAX) NULL"),
            ("scores_pct_json", "NVARCHAR(MAX) NULL"),
            ("created_at", "NVARCHAR(64) NOT NULL"),
            ("updated_at", "NVARCHAR(64) NOT NULL"),
            ("elapsed", "NVARCHAR(64) NULL"),
        ):
            if col in acc_cols and not _already_unicode(acc_info.get(col)):
                statements.append(
                    f"ALTER TABLE accuracy_report_document_mcp ALTER COLUMN {col} {spec}"
                )

    statements.extend(
        [
            """
CREATE OR ALTER VIEW job_table_document_mcp_overview AS
SELECT
  id AS job_id,
  mcp,
  status,
  xid,
  mapper_llm,
  validator_llm,
  elapsed,
  elapsed_ms,
  created_at,
  updated_at,
  completed_at,
  LEFT(template_path, 256) AS template_path,
  LEFT(output_path, 256) AS output_path,
  LEFT(error_message, 400) AS error_message
FROM job_table_document_mcp
""",
            """
CREATE OR ALTER VIEW accuracy_report_document_mcp_overview AS
SELECT
  job_id,
  mcp,
  xid,
  overall_confidence_pct,
  extraction_confidence_pct,
  mapping_confidence_pct,
  coverage_pct,
  table_mapping_confidence_pct,
  generation_integrity_pct,
  generation_confidence_pct,
  validation_score_pct,
  extraction_passed,
  validation_passed,
  mapper_llm,
  validator_llm,
  elapsed,
  elapsed_ms,
  LEFT(notes, 400) AS notes,
  created_at,
  updated_at
FROM accuracy_report_document_mcp
""",
        ]
    )

    with engine.begin() as conn:
        if "job_table_document_mcp" in table_names:
            conn.execute(text("DROP INDEX IF EXISTS idx_job_table_document_mcp_xid ON job_table_document_mcp"))
    for stmt in statements:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Azure SQL migrate skipped a statement: %s", exc)
    if "job_table_document_mcp" in table_names:
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'idx_job_table_document_mcp_xid'
      AND object_id = OBJECT_ID('job_table_document_mcp')
)
    CREATE INDEX idx_job_table_document_mcp_xid ON job_table_document_mcp (xid)
"""
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Azure SQL migrate could not recreate xid index: %s", exc)


def _seed_master_data(engine: Engine) -> None:
    """Insert default legal/sales blocks only when the table is empty."""
    from datetime import datetime, timezone

    from sqlalchemy import func, select

    from document_processing_mcp.services.master_data import DEFAULT_MASTER_DATA_ROWS
    from document_processing_mcp.storage.sql_models import MasterData

    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        count = session.scalar(select(func.count()).select_from(MasterData)) or 0
        if count:
            return
        for row in DEFAULT_MASTER_DATA_ROWS:
            session.add(
                MasterData(
                    id=row["id"],
                    placeholder_key=row["placeholder_key"],
                    category=row["category"],
                    content=row["content"],
                    active="true",
                    updated_at=now,
                )
            )
        session.commit()


def ensure_schema(*, sqlite_path: Path | None = None) -> None:
    from document_processing_mcp.storage.sql_models import (
        Base,
        CallLog,
        DocumentAccuracyReport,
        DocumentJob,
        MasterData,
    )

    engine = get_engine(sqlite_path=sqlite_path)
    if engine_uses_mssql(str(engine.url)):
        _prepare_mssql_schema(engine)
    Base.metadata.create_all(
        engine,
        tables=[
            DocumentJob.__table__,
            DocumentAccuracyReport.__table__,
            CallLog.__table__,
            MasterData.__table__,
        ],
    )
    if engine_uses_mssql(str(engine.url)):
        _migrate_mssql_document_tables(engine)
    _seed_master_data(engine)


def reset_engines() -> None:
    """Drop cached engines (tests / reload_settings)."""
    for engine in _engine_cache.values():
        engine.dispose()
    _engine_cache.clear()
    _session_cache.clear()
