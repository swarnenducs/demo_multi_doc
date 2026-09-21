"""Word XML round-trip must keep Office namespaces (Word rejects ns0/ns5 prefixes)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from document_processing_mcp.models.schemas import MappingResult
from document_processing_mcp.services.document_generator import generate_styled_document
from document_processing_mcp.services.style_extractor import extract_word_styles

GPO = (
    Path(__file__).resolve().parents[2]
    / "samples"
    / "templates"
    / "complete_contract_template_GPO.docx"
)


def test_gpo_template_roundtrip_keeps_word_namespaces(tmp_path: Path) -> None:
    extracted = extract_word_styles(GPO)
    output = tmp_path / "gpo_out.docx"
    generate_styled_document(extracted, MappingResult(mapping_confidence=1.0), output)

    with zipfile.ZipFile(output) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
        assert zf.testzip() is None

    assert "xmlns:w15=" in xml
    assert "xmlns:wp14=" in xml
    assert "xmlns:w14=" in xml
    assert "<ns5:" not in xml
    assert "xmlns:ns5=" not in xml
