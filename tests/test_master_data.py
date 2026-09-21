"""Master-data SQL hydration and system_instruction override."""

from __future__ import annotations

from document_processing_mcp.models.schemas import ExtractedTemplate, FieldMapping, MappingResult
from document_processing_mcp.nodes.pipeline import enrich_master_data_node
from document_processing_mcp.services.master_data import (
    LEGAL_DEPARTMENT_BLOCK,
    LEGAL_PLACEHOLDER,
    SALES_EXCELLENCE_BLOCK,
    SALES_PLACEHOLDER,
    merge_master_data_into_mapping,
)
from document_processing_mcp.storage.db import ensure_schema


def test_enrich_loads_sql_when_override_false() -> None:
    ensure_schema()
    extracted = ExtractedTemplate(
        template_path="x.docx",
        placeholders=[LEGAL_PLACEHOLDER, SALES_PLACEHOLDER],
    )
    state = enrich_master_data_node(
        {
            "status": "styles_extracted",
            "json_data": {
                "system_instruction": {
                    "legal_notice_block": {
                        "override": False,
                        "placeholder": LEGAL_PLACEHOLDER,
                    },
                    "sales_notice_block": {
                        "override": False,
                        "placeholder": SALES_PLACEHOLDER,
                    },
                }
            },
            "extracted": extracted,
            "errors": [],
        }
    )
    assert state["status"] == "master_data_enriched"
    assert state["json_data"][LEGAL_PLACEHOLDER] == LEGAL_DEPARTMENT_BLOCK
    assert state["json_data"][SALES_PLACEHOLDER] == SALES_EXCELLENCE_BLOCK
    sources = {row["placeholder"]: row["source"] for row in state["master_data_applied"]}
    assert sources[LEGAL_PLACEHOLDER] == "sql"
    assert sources[SALES_PLACEHOLDER] == "sql"


def test_enrich_uses_payload_when_override_true() -> None:
    ensure_schema()
    custom = "Legal Department\nSuite 200 — payload override"
    extracted = ExtractedTemplate(
        template_path="x.docx",
        placeholders=[LEGAL_PLACEHOLDER, SALES_PLACEHOLDER],
    )
    state = enrich_master_data_node(
        {
            "status": "styles_extracted",
            "json_data": {
                "system_instruction": {
                    "legal_notice_block": {
                        "override": True,
                        "placeholder": LEGAL_PLACEHOLDER,
                        "value": custom,
                    },
                    "sales_notice_block": {
                        "override": False,
                        "placeholder": SALES_PLACEHOLDER,
                    },
                }
            },
            "extracted": extracted,
            "errors": [],
        }
    )
    assert state["json_data"][LEGAL_PLACEHOLDER] == custom
    assert state["json_data"][SALES_PLACEHOLDER] == SALES_EXCELLENCE_BLOCK
    sources = {row["placeholder"]: row["source"] for row in state["master_data_applied"]}
    assert sources[LEGAL_PLACEHOLDER] == "payload"
    assert sources[SALES_PLACEHOLDER] == "sql"


def test_merge_master_data_into_mapping() -> None:
    extracted = ExtractedTemplate(
        template_path="x.docx",
        placeholders=[LEGAL_PLACEHOLDER, "GPO Name"],
    )
    mapping = MappingResult(
        mappings=[
            FieldMapping(json_path="agreement.gpo_name", placeholder="GPO Name", value="Acme"),
        ],
        unmapped_placeholders=[LEGAL_PLACEHOLDER],
    )
    merged = merge_master_data_into_mapping(
        extracted,
        {LEGAL_PLACEHOLDER: LEGAL_DEPARTMENT_BLOCK},
        mapping,
    )
    by_ph = {m.placeholder: m for m in merged.mappings}
    assert by_ph[LEGAL_PLACEHOLDER].value == LEGAL_DEPARTMENT_BLOCK
    assert by_ph[LEGAL_PLACEHOLDER].confidence == 1.0
    assert LEGAL_PLACEHOLDER not in merged.unmapped_placeholders


def test_graph_includes_enrich_node() -> None:
    from document_processing_mcp.graph import build_graph

    graph = build_graph()
    assert "enrich_master_data" in graph.nodes
