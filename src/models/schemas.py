"""Shared Pydantic models for Word style extraction, field mapping, and generation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RunStyle(BaseModel):
    """Character-level (run) formatting captured from Word XML."""

    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    font_name: str | None = None
    font_size_pt: float | None = None
    color_hex: str | None = None
    highlight: str | None = None
    raw_rpr_xml: str | None = Field(
        default=None,
        description="Original w:rPr XML fragment so generation can preserve exact style",
    )


class ParagraphStyle(BaseModel):
    """Paragraph-level formatting captured from Word XML."""

    style_id: str | None = None
    style_name: str | None = None
    alignment: str | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    line_spacing: float | None = None
    indent_left_pt: float | None = None
    indent_right_pt: float | None = None
    raw_ppr_xml: str | None = Field(
        default=None,
        description="Original w:pPr XML fragment so generation can preserve exact style",
    )


class StyleDefinition(BaseModel):
    """A named style from word/styles.xml."""

    style_id: str
    name: str | None = None
    style_type: str | None = None  # paragraph | character | table | numbering
    based_on: str | None = None
    paragraph: ParagraphStyle | None = None
    run: RunStyle | None = None
    raw_xml: str | None = None


class ContentBlock(BaseModel):
    """A content unit in the template (paragraph or table cell text run)."""

    block_id: str
    block_type: str  # paragraph | table_cell
    text: str
    placeholder_keys: list[str] = Field(default_factory=list)
    paragraph_style: ParagraphStyle | None = None
    run_styles: list[RunStyle] = Field(default_factory=list)
    table_index: int | None = None
    row_index: int | None = None
    cell_index: int | None = None
    xpath_hint: str | None = None


class ExtractedTemplate(BaseModel):
    """Full extraction result from a Word template."""

    template_path: str
    styles: list[StyleDefinition] = Field(default_factory=list)
    blocks: list[ContentBlock] = Field(default_factory=list)
    placeholders: list[str] = Field(default_factory=list)
    styles_xml: str | None = None
    document_xml: str | None = None
    numbering_xml: str | None = None


class FieldMapping(BaseModel):
    """One mapping from a JSON path to a template placeholder/block."""

    json_path: str
    placeholder: str | None = None
    block_id: str | None = None
    value: Any = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    rationale: str | None = None


class TableColumnMap(BaseModel):
    """LLM-decided mapping of one table header → JSON object field."""

    header: str
    json_field: str
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class TableFillPlan(BaseModel):
    """
    LLM plan to fill a Word table from a JSON array of objects.
    No hardcoded product knowledge — the LLM chooses array_path and column maps.
    """

    table_index: int = 0
    array_json_path: str = Field(
        description="JSON path to list-of-objects, e.g. products or items"
    )
    columns: list[TableColumnMap] = Field(default_factory=list)
    rationale: str | None = None


class MappingResult(BaseModel):
    """LLM mapping of JSON data onto template fields."""

    mappings: list[FieldMapping] = Field(default_factory=list)
    table_fills: list[TableFillPlan] = Field(
        default_factory=list,
        description="LLM plans for expanding tables from JSON arrays",
    )
    unmapped_json_keys: list[str] = Field(default_factory=list)
    unmapped_placeholders: list[str] = Field(default_factory=list)
    notes: str | None = None
    mapping_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Average per-field mapping confidence (0-1)",
    )
    coverage_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of template placeholders that were mapped",
    )
    mapper_source: str | None = Field(
        default=None,
        description="llm | rules — which mapper produced this result",
    )
    mapper_provider: str | None = Field(
        default=None,
        description="openai | rules — provider for LLM #1 mapper",
    )
    mapper_model: str | None = Field(
        default=None,
        description="Model id used by LLM #1 mapper, e.g. gpt-5",
    )


class GenerationResult(BaseModel):
    """Output of the styled Word document generation step."""

    output_path: str
    applied_mappings: int = 0
    preserved_styles: bool = True
    message: str | None = None
    generation_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence inherited from mapping + generation integrity",
    )
    integrity_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Deterministic checks: leftovers, file open, style parts kept",
    )
    leftover_placeholders: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    """One issue found by the document validator."""

    severity: Literal["high", "medium", "low"] = "medium"
    field: str | None = None
    message: str
    expected: str | None = None
    actual: str | None = None


class ValidationResult(BaseModel):
    """LLM #2 validation of template vs generated doc vs JSON."""

    passed: bool = False
    validation_score: float = Field(default=0.0, ge=0.0, le=1.0)
    issues: list[ValidationIssue] = Field(default_factory=list)
    summary: str | None = None
    validator_source: str | None = Field(
        default=None,
        description="groq | openai | rules | groq+rules — which validator produced this result",
    )
    validator_provider: str | None = Field(
        default=None,
        description="groq | openai | rules — provider for LLM #2 validator",
    )
    validator_model: str | None = Field(
        default=None,
        description="Model id used by LLM #2 validator, e.g. openai/gpt-oss-120b",
    )


class ExtractionValidationResult(BaseModel):
    """LLM critic of deterministic Word XML extraction (placeholders / structure)."""

    passed: bool = False
    extraction_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence that extracted XML/structure is usable for mapping (0-1)",
    )
    placeholder_detection_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence that placeholders were detected correctly (0-1)",
    )
    structure_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence that paragraphs/tables were parsed correctly (0-1)",
    )
    issues: list[ValidationIssue] = Field(default_factory=list)
    summary: str | None = None
    detected_placeholders: list[str] = Field(default_factory=list)
    missed_placeholder_suspects: list[str] = Field(
        default_factory=list,
        description="Tokens in text that look like fills but were not extracted",
    )
    validator_source: str | None = None
    validator_provider: str | None = None
    validator_model: str | None = None


class ConfidenceReport(BaseModel):
    """Aggregated confidence across extraction, mapping, generation, and validation."""

    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extraction_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="LLM confidence on extracted Word XML / placeholders (0-1)",
    )
    extraction_passed: bool | None = None
    mapping_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="LLM #1 confidence finding/mapping placeholders (0-1)",
    )
    coverage_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Share of placeholders successfully mapped (0-1)",
    )
    table_mapping_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="LLM #1 confidence on table column fills (0-1)",
    )
    generation_integrity: float = Field(default=0.0, ge=0.0, le=1.0)
    generation_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    validation_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="LLM #2 document validation score (0-1)",
    )
    validation_passed: bool | None = None
    per_field: list[dict[str, Any]] = Field(default_factory=list)
    per_table_column: list[dict[str, Any]] = Field(default_factory=list)
    # Convenience percent view for UI/API
    scores_pct: dict[str, Any] = Field(default_factory=dict)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "extraction": 0.10,
            "mapping": 0.35,
            "coverage": 0.20,
            "integrity": 0.15,
            "validation": 0.20,
        }
    )
    mapper_llm: str | None = Field(
        default=None,
        description="LLM #1 label, e.g. openai/gpt-5",
    )
    validator_llm: str | None = Field(
        default=None,
        description="LLM #2 label, e.g. groq/openai/gpt-oss-120b",
    )
    notes: str | None = None
