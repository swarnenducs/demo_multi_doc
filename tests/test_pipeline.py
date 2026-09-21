"""Tests for style extraction, generation, and tools (LLM calls require live keys)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from docx import Document

from document_processing_mcp.graph import build_graph
from document_processing_mcp.models.schemas import FieldMapping, MappingResult
from document_processing_mcp.services.confidence import build_confidence_report, enrich_mapping_scores
from document_processing_mcp.services.document_generator import generate_styled_document
from document_processing_mcp.services.field_mapper import map_json_to_template
from document_processing_mcp.services.style_extractor import extract_word_styles
from document_processing_mcp.tools import get_document_tools
from doc_sample_template import build_sample_template


SAMPLE_DATA = {
    "invoice_number": "INV-99",
    "invoice_date": "2026-07-10",
    "customer": {
        "name": "Test Co",
        "address": "1 Test Rd",
        "email": "t@example.com",
    },
    "items": [
        {"description": "Item A", "quantity": 1, "unit_price": 10, "total": 10},
        {"description": "Item B", "quantity": 2, "unit_price": 5, "total": 10},
    ],
    "subtotal": 20,
    "tax": 4,
    "total_amount": 24,
    "notes": "Paid",
}


def _manual_mapping(extracted) -> MappingResult:
    """Hand-built mapping for offline generation tests (no rule engine)."""
    mappings = [
        FieldMapping(
            json_path="invoice_number",
            placeholder="invoice_number",
            value="INV-99",
            confidence=1.0,
        ),
        FieldMapping(
            json_path="customer.name",
            placeholder="customer.name",
            value="Test Co",
            confidence=1.0,
        ),
        FieldMapping(
            json_path="total_amount",
            placeholder="total_amount",
            value=24,
            confidence=1.0,
        ),
    ]
    # Attach remaining placeholders with matching JSON paths when present
    for ph in extracted.placeholders:
        if any(m.placeholder == ph for m in mappings):
            continue
        # simple dotted path equality for sample template
        parts = ph.split(".")
        cur: object = SAMPLE_DATA
        ok = True
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                ok = False
                break
            cur = cur[p]
        if ok:
            mappings.append(
                FieldMapping(json_path=ph, placeholder=ph, value=cur, confidence=1.0)
            )
    result = MappingResult(
        mappings=mappings,
        unmapped_placeholders=[
            p for p in extracted.placeholders if not any(m.placeholder == p for m in mappings)
        ],
        mapper_source="llm",
        mapper_provider="test",
        mapper_model="manual",
        notes="Manual mapping for offline unit tests",
    )
    return enrich_mapping_scores(result, len(extracted.placeholders) or 1)


def test_extract_styles_and_placeholders(tmp_path: Path) -> None:
    template = build_sample_template(tmp_path / "template.docx")
    extracted = extract_word_styles(template)

    assert extracted.styles
    assert "invoice_number" in extracted.placeholders
    assert "customer.name" in extracted.placeholders
    assert "total_amount" in extracted.placeholders


def test_mapper_requires_llm(tmp_path: Path) -> None:
    template = build_sample_template(tmp_path / "template.docx")
    extracted = extract_word_styles(template)
    with pytest.raises(RuntimeError, match="Mapper LLM is required"):
        map_json_to_template(extracted, SAMPLE_DATA)


def test_generation_integrity_with_manual_mapping(tmp_path: Path) -> None:
    template = build_sample_template(tmp_path / "template.docx")
    extracted = extract_word_styles(template)
    mapping = _manual_mapping(extracted)
    output = tmp_path / "out.docx"
    generation = generate_styled_document(extracted, mapping, output)

    assert generation.integrity_score >= 0.8
    assert generation.generation_confidence > 0
    assert not generation.leftover_placeholders

    report = build_confidence_report(
        mapping,
        generation,
        validation=None,
        marker_detection={
            "had_markers": False,
            "library_match": {"name": "complete_contract_template_GPO.docx", "score": 0.5},
        },
    )
    assert report.overall_confidence > 0
    assert report.per_field
    assert "unmarked" in (report.notes or "").lower()
    assert "complete_contract_template_GPO.docx" in (report.notes or "")

    doc = Document(output)
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "INV-99" in full_text
    assert "Test Co" in full_text
    assert "{{invoice_number}}" not in full_text


def test_end_to_end_graph_fails_without_mapper_llm(tmp_path: Path) -> None:
    template = build_sample_template(tmp_path / "template.docx")
    data_path = tmp_path / "data.json"
    data_path.write_text(json.dumps(SAMPLE_DATA), encoding="utf-8")
    output = tmp_path / "out.docx"

    result = build_graph().invoke(
        {
            "template_path": str(template),
            "data_path": str(data_path),
            "output_path": str(output),
            "errors": [],
            "status": "started",
            "retry_count": 0,
            "max_retries": 0,
            "validation_threshold": 0.7,
            "skip_validation": True,
        }
    )

    assert result["status"] == "failed"
    assert any("mapping" in e.lower() or "mapper" in e.lower() for e in result.get("errors", []))


def test_tools_are_registered() -> None:
    tools = get_document_tools()
    names = {t.name for t in tools}
    assert names == {
        "load_json_data",
        "extract_word_styles",
        "map_json_to_template",
        "generate_styled_document",
        "validate_documents",
        "compute_confidence_report",
    }
