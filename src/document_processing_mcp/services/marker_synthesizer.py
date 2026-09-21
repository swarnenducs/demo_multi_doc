"""When a Word file has no fill tokens, ask the mapper LLM for marker plans.

The LLM returns JSON only. Code stamps <snake_case> tokens onto a copy of the
uploaded document (layout/style unchanged). A closest library template is
passed as naming context, not as the output layout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from document_processing_mcp.models.schemas import ExtractedTemplate
from document_processing_mcp.services.field_mapper import _table_summaries
from document_processing_mcp.services.library_match import (
    find_closest_library_template,
    json_key_hints,
)
from document_processing_mcp.services.marker_apply import apply_marker_plan
from document_processing_mcp.services.placeholders import template_has_markers
from document_processing_mcp.services.trace_log import traced_invoke


class _LLMMarkerHit(BaseModel):
    sample_value: str = Field(description="Exact text in the uploaded document")
    marker: str = Field(description="<snake_case_name>")
    location: str = Field(default="")
    confidence: str = Field(default="MEDIUM", description="HIGH | MEDIUM | LOW")


class _LLMTablePlan(BaseModel):
    type: str = Field(default="PRICE_LIST")
    required: bool = False
    use_existing_table: bool = False
    after_block_id: str = Field(default="", description="Paragraph block_id to insert after")
    columns: list[str] = Field(default_factory=list)
    marker_row: list[str] = Field(default_factory=list)
    confidence: str = Field(default="MEDIUM")


class _LLMMarkerPlan(BaseModel):
    markers: list[_LLMMarkerHit] = Field(default_factory=list)
    tables: list[_LLMTablePlan] = Field(default_factory=list)
    notes: str = Field(default="")


def _dumps(payload: Any, limit: int = 4000) -> str:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def synthesize_markers_if_needed(
    extracted: ExtractedTemplate,
    json_data: dict[str, Any] | None = None,
    *,
    model_id: str | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """No-op when markers already exist. Otherwise stamp a marked copy and return paths."""
    has, keys = template_has_markers(extracted, template_path=extracted.template_path)
    if has:
        return {
            "skipped": True,
            "reason": "markers_present",
            "placeholder_keys": keys,
            "template_path": extracted.template_path,
            "library_match": None,
        }

    from document_processing_mcp.core.settings import settings

    if not settings().document_marker_synthesis_enabled:
        raise RuntimeError(
            "Template has no placeholders and DOCUMENT_MARKER_SYNTHESIS_ENABLED is off. "
            "Use a tagged Word file or set DOCUMENT_MARKER_SYNTHESIS_ENABLED=true."
        )

    from document_processing_mcp.services.llm_factory import MapperLLM, is_mapper_available
    from document_processing_mcp.services.prompts.marker_synthesizer_prompt import (
        build_marker_synthesizer_chain,
    )

    if not is_mapper_available() and not model_id:
        raise RuntimeError(
            "No markers in the Word file, and mapper LLM is unavailable to propose them."
        )

    match = find_closest_library_template(
        extracted,
        exclude_path=extracted.template_path,
    )
    blocks = [
        {
            "id": b.block_id,
            "t": b.block_type,
            "text": (b.text or "")[:240],
        }
        for b in extracted.blocks
        if (b.text or "").strip()
    ]
    document_text = "\n".join(str(b.get("text") or "") for b in blocks)

    mapper = MapperLLM(model_id=model_id, structured_schema=_LLMMarkerPlan)
    llm, config = mapper.as_tuple()
    chain = build_marker_synthesizer_chain(llm)
    plan: _LLMMarkerPlan = traced_invoke(
        chain,
        {
            "document_text": document_text[:8000],
            "blocks_json": _dumps(blocks, 5000),
            "tables_json": _dumps(_table_summaries(extracted), 2000),
            "reference_name": (match or {}).get("name") or "",
            "reference_placeholders_json": _dumps((match or {}).get("placeholders") or [], 1500),
            "reference_preview": (match or {}).get("preview") or "",
            "json_keys_json": _dumps(json_key_hints(json_data), 2000),
        },
        role="marker_synthesizer",
        provider=config.provider,
        model=config.model,
    )

    src = Path(extracted.template_path)
    dest_dir = Path(output_dir) if output_dir else src.parent
    dest = dest_dir / f"{src.stem}.marked{src.suffix}"
    para_ids = [b.block_id for b in extracted.blocks if b.block_type == "paragraph"]
    block_id_to_index = {bid: i for i, bid in enumerate(para_ids)}
    applied = apply_marker_plan(src, dest, plan, block_id_to_index=block_id_to_index)
    return {
        "skipped": False,
        "reason": "synthesized",
        "template_path": applied["path"],
        "placeholder_keys": applied.get("placeholder_keys") or [],
        "replacements_applied": applied.get("replacements_applied") or 0,
        "library_match": match,
        "plan_notes": plan.notes,
        "markers_proposed": [m.model_dump() for m in plan.markers],
        "tables_proposed": [t.model_dump() for t in plan.tables],
    }
