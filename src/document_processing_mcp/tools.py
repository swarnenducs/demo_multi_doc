"""LangChain/LangGraph tools wrapping each pipeline step.

These tools can be:
  - called directly from Python
  - bound to an agent via `get_document_tools()`
  - used by the fixed pipeline nodes (same underlying services)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from document_processing_mcp.models.schemas import (
    ConfidenceReport,
    ExtractedTemplate,
    GenerationResult,
    MappingResult,
    ValidationResult,
)
from document_processing_mcp.services.confidence import build_confidence_report
from document_processing_mcp.services.document_generator import generate_styled_document
from document_processing_mcp.services.document_validator import validate_documents
from document_processing_mcp.services.field_mapper import map_json_to_template
from document_processing_mcp.services.style_extractor import extract_word_styles
from document_processing_mcp.services.trace_log import traced_tool


# ---------------------------------------------------------------------------
# Tool argument schemas
# ---------------------------------------------------------------------------


class LoadJsonArgs(BaseModel):
    data_path: str = Field(description="Path to the JSON data file")


class ExtractStylesArgs(BaseModel):
    template_path: str = Field(description="Path to the Word .docx template")


class MapFieldsArgs(BaseModel):
    template_path: str = Field(description="Path to the Word .docx template")
    data_path: str = Field(description="Path to the JSON data file")
    extraction_json_path: str | None = Field(
        default=None,
        description="Optional path to a previously dumped extraction JSON (skips re-extract if unused)",
    )


class GenerateDocArgs(BaseModel):
    template_path: str = Field(description="Path to the Word .docx template")
    data_path: str = Field(description="Path to the JSON data file")
    output_path: str = Field(description="Where to write the generated .docx")


class ValidateDocsArgs(BaseModel):
    template_path: str = Field(description="Path to the original Word template")
    generated_path: str = Field(description="Path to the generated .docx")
    data_path: str = Field(description="Path to the source JSON data")


class ConfidenceArgs(BaseModel):
    mapping_json: str = Field(description="MappingResult as JSON string")
    generation_json: str = Field(description="GenerationResult as JSON string")
    validation_json: str | None = Field(
        default=None,
        description="ValidationResult as JSON string (optional)",
    )


# ---------------------------------------------------------------------------
# Tool implementations (return JSON-serializable dicts for agent use)
# ---------------------------------------------------------------------------


@traced_tool("load_json_data")
def load_json_data(data_path: str) -> dict[str, Any]:
    """Load JSON data that will be mapped onto the Word template."""
    path = Path(data_path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return {"ok": True, "data_path": str(path.resolve()), "keys": list(payload.keys()), "data": payload}


@traced_tool("extract_word_styles")
def extract_word_styles_tool(template_path: str) -> dict[str, Any]:
    """Step 1 — extract Word XML styles, content blocks, and placeholders from a .docx template."""
    extracted = extract_word_styles(template_path)
    payload = extracted.model_dump()
    # Keep tool responses lean for the agent; raw XML stays available via services
    payload.pop("styles_xml", None)
    payload.pop("document_xml", None)
    payload.pop("numbering_xml", None)
    return {
        "ok": True,
        "template_path": extracted.template_path,
        "style_count": len(extracted.styles),
        "block_count": len(extracted.blocks),
        "placeholders": extracted.placeholders,
        "extracted": payload,
    }


@traced_tool("map_json_to_template")
def map_json_to_template_tool(template_path: str, data_path: str, extraction_json_path: str | None = None) -> dict[str, Any]:
    """Step 2 — map JSON fields onto template placeholders (LLM #1 or rules) with confidence scores."""
    del extraction_json_path  # reserved for future cache reuse
    extracted = extract_word_styles(template_path)
    with Path(data_path).open(encoding="utf-8") as fh:
        data = json.load(fh)
    mapping = map_json_to_template(extracted, data)
    return {
        "ok": True,
        "mapper_source": mapping.mapper_source,
        "mapping_confidence": mapping.mapping_confidence,
        "coverage_score": mapping.coverage_score,
        "mapped_count": len(mapping.mappings),
        "unmapped_placeholders": mapping.unmapped_placeholders,
        "mapping": mapping.model_dump(),
    }


@traced_tool("generate_styled_document")
def generate_styled_document_tool(
    template_path: str,
    data_path: str,
    output_path: str,
) -> dict[str, Any]:
    """Step 3 — generate a styled .docx from template + JSON mapping, preserving Word XML styles."""
    extracted = extract_word_styles(template_path)
    with Path(data_path).open(encoding="utf-8") as fh:
        data = json.load(fh)
    mapping = map_json_to_template(extracted, data)
    generation = generate_styled_document(extracted, mapping, output_path, json_data=data)
    return {
        "ok": True,
        "output_path": generation.output_path,
        "applied_mappings": generation.applied_mappings,
        "generation_confidence": generation.generation_confidence,
        "integrity_score": generation.integrity_score,
        "leftover_placeholders": generation.leftover_placeholders,
        "generation": generation.model_dump(),
        "mapping_confidence": mapping.mapping_confidence,
    }


@traced_tool("validate_documents")
def validate_documents_tool(
    template_path: str,
    generated_path: str,
    data_path: str,
) -> dict[str, Any]:
    """Step 4 — validate template vs generated doc vs JSON using LLM #2 critic (with rule checks)."""
    extracted = extract_word_styles(template_path)
    with Path(data_path).open(encoding="utf-8") as fh:
        data = json.load(fh)
    mapping = map_json_to_template(extracted, data)
    validation = validate_documents(extracted, generated_path, data, mapping)
    return {
        "ok": True,
        "passed": validation.passed,
        "validation_score": validation.validation_score,
        "validator_source": validation.validator_source,
        "issue_count": len(validation.issues),
        "summary": validation.summary,
        "validation": validation.model_dump(),
    }


@traced_tool("compute_confidence_report")
def compute_confidence_report_tool(
    mapping_json: str,
    generation_json: str,
    validation_json: str | None = None,
) -> dict[str, Any]:
    """Aggregate mapping + generation + validation into an overall generator confidence score (Document MCP only)."""
    mapping = MappingResult.model_validate_json(mapping_json)
    generation = GenerationResult.model_validate_json(generation_json)
    validation = (
        ValidationResult.model_validate_json(validation_json) if validation_json else None
    )
    report = build_confidence_report(mapping, generation, validation)
    return {"ok": True, "confidence": report.model_dump()}


def get_document_tools() -> list[StructuredTool]:
    """Return all pipeline steps as LangChain tools (for agent mode)."""
    return [
        StructuredTool.from_function(
            func=load_json_data,
            name="load_json_data",
            description=load_json_data.__doc__ or "Load JSON data",
            args_schema=LoadJsonArgs,
        ),
        StructuredTool.from_function(
            func=extract_word_styles_tool,
            name="extract_word_styles",
            description=extract_word_styles_tool.__doc__ or "Extract Word styles",
            args_schema=ExtractStylesArgs,
        ),
        StructuredTool.from_function(
            func=map_json_to_template_tool,
            name="map_json_to_template",
            description=map_json_to_template_tool.__doc__ or "Map JSON to template",
            args_schema=MapFieldsArgs,
        ),
        StructuredTool.from_function(
            func=generate_styled_document_tool,
            name="generate_styled_document",
            description=generate_styled_document_tool.__doc__ or "Generate styled document",
            args_schema=GenerateDocArgs,
        ),
        StructuredTool.from_function(
            func=validate_documents_tool,
            name="validate_documents",
            description=validate_documents_tool.__doc__ or "Validate documents",
            args_schema=ValidateDocsArgs,
        ),
        StructuredTool.from_function(
            func=compute_confidence_report_tool,
            name="compute_confidence_report",
            description=compute_confidence_report_tool.__doc__ or "Compute confidence",
            args_schema=ConfidenceArgs,
        ),
    ]


# Re-export service types for callers that import from tools
__all__ = [
    "ConfidenceReport",
    "ExtractedTemplate",
    "GenerationResult",
    "MappingResult",
    "ValidationResult",
    "compute_confidence_report_tool",
    "extract_word_styles_tool",
    "generate_styled_document_tool",
    "get_document_tools",
    "load_json_data",
    "map_json_to_template_tool",
    "validate_documents_tool",
]
