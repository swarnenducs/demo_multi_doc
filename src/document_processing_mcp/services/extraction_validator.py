"""LLM critic for deterministic Word XML extraction (confidence on extracted structure)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from document_processing_mcp.models.schemas import (
    ExtractionValidationResult,
    ExtractedTemplate,
    ValidationIssue,
)
from document_processing_mcp.services.trace_log import traced_invoke


class _LLMExtractionIssue(BaseModel):
    severity: str = Field(default="medium", description="high | medium | low")
    field: str = Field(default="", description="Placeholder or block id if known")
    message: str = Field(description="What is wrong with the extraction")
    expected: str = Field(default="")
    actual: str = Field(default="")


class _LLMExtractionPayload(BaseModel):
    passed: bool = False
    extraction_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    placeholder_detection_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    structure_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    issues: list[_LLMExtractionIssue] = Field(default_factory=list)
    summary: str = Field(default="")
    missed_placeholder_suspects: list[str] = Field(default_factory=list)


def _table_summaries(template: ExtractedTemplate) -> list[dict[str, Any]]:
    by_table: dict[int, dict[int, dict[int, str]]] = defaultdict(lambda: defaultdict(dict))
    for b in template.blocks:
        if b.block_type != "table_cell" or b.table_index is None:
            continue
        if b.row_index is None or b.cell_index is None:
            continue
        by_table[b.table_index][b.row_index][b.cell_index] = b.text.strip()

    summaries: list[dict[str, Any]] = []
    for t_idx in sorted(by_table):
        rows = by_table[t_idx]
        if 0 not in rows:
            continue
        headers = [rows[0][c] for c in sorted(rows[0])]
        summaries.append(
            {
                "table_index": t_idx,
                "headers": headers,
                "body_row_count": max(0, len(rows) - 1),
            }
        )
    return summaries


def validate_extraction(
    template: ExtractedTemplate,
    *,
    model_id: str | None = None,
) -> ExtractionValidationResult:
    """
    LLM critic of XML extraction quality (validator role via init_chat_model).
    Soft-skips (returns passed=True, confidence=None-ish) only via caller when unavailable;
    this function requires a live validator LLM.
    """
    from document_processing_mcp.services.llm_factory import (
        LLMAsJudge,
        is_validator_available,
    )
    from document_processing_mcp.services.prompts.extraction_validator_prompt import (
        build_extraction_validator_chain,
    )

    if not is_validator_available() and not model_id:
        raise RuntimeError(
            "Extraction validator LLM is required. Check VALIDATOR_PROVIDER credentials."
        )

    llm, config = LLMAsJudge(
        model_id=model_id,
        structured_schema=_LLMExtractionPayload,
    ).as_tuple()
    def _clip(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 24)] + f"\n...<truncated:{len(text)}>"

    def _dumps_compact(obj: Any, limit: int) -> str:
        return _clip(json.dumps(obj, separators=(",", ":"), default=str), limit)

    # Prefer blocks that carry placeholders; keep payload small for Groq TPM.
    prioritized = [
        b
        for b in template.blocks
        if (b.text or "").strip()
        and (b.placeholder_keys or "<" in b.text or "XX%" in b.text or "X%" in b.text)
    ]
    if len(prioritized) < 12:
        extras = [
            b
            for b in template.blocks
            if (b.text or "").strip() and b not in prioritized
        ]
        prioritized.extend(extras[: max(0, 8 - len(prioritized))])

    block_summaries = [
        {
            "t": b.block_type,
            "text": (b.text or "")[:120],
            "ph": b.placeholder_keys,
        }
        for b in prioritized
    ]
    chain = build_extraction_validator_chain(llm)
    result: _LLMExtractionPayload = traced_invoke(
        chain,
        {
            "template_path": Path(template.template_path).name if template.template_path else "",
            "placeholders_json": _dumps_compact(template.placeholders, 800),
            "blocks_json": _dumps_compact(block_summaries, 1800),
            "tables_json": _dumps_compact(_table_summaries(template), 800),
        },
        role="extraction_validator",
        provider=config.provider,
        model=config.model,
    )  # type: ignore[assignment]

    issues = [
        ValidationIssue(
            severity=i.severity if i.severity in {"high", "medium", "low"} else "medium",  # type: ignore[arg-type]
            field=i.field or None,
            message=i.message,
            expected=i.expected or None,
            actual=i.actual or None,
        )
        for i in result.issues
    ]

    return ExtractionValidationResult(
        passed=bool(result.passed),
        extraction_confidence=round(min(max(result.extraction_confidence, 0.0), 1.0), 4),
        placeholder_detection_confidence=round(
            min(max(result.placeholder_detection_confidence, 0.0), 1.0), 4
        ),
        structure_confidence=round(min(max(result.structure_confidence, 0.0), 1.0), 4),
        issues=issues,
        summary=result.summary or f"Extraction critic ({config.label})",
        detected_placeholders=list(template.placeholders),
        missed_placeholder_suspects=list(result.missed_placeholder_suspects),
        validator_source="llm",
        validator_provider=config.provider,
        validator_model=config.model,
    )
