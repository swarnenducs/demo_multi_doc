"""Tests for generated contract filename helpers."""

from __future__ import annotations

from document_processing_mcp.services.naming import (
    build_contract_output_filename,
    job_id_short_suffix,
    sanitize_template_stem,
)


def test_job_id_short_suffix_uuid_last_block() -> None:
    assert (
        job_id_short_suffix("a1b2c3d4-e5f6-7890-abcd-50642e6a035d") == "50642e6a035d"
    )


def test_build_contract_output_filename() -> None:
    name = build_contract_output_filename(
        "a1b2c3d4-e5f6-7890-abcd-50642e6a035d",
        "complete_contract_template_GPO.docx",
    )
    assert name == "50642e6a035d_complete_contract_template_GPO.docx"


def test_sanitize_template_stem() -> None:
    assert sanitize_template_stem("My Contract (v2).docx") == "My_Contract_v2"
