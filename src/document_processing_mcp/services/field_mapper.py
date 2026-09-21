"""Map JSON onto Word template using the mapper LLM only (no rule fallback)."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

from document_processing_mcp.models.schemas import (
    ExtractedTemplate,
    FieldMapping,
    MappingResult,
    TableColumnMap,
    TableFillPlan,
)
from document_processing_mcp.services.confidence import enrich_mapping_scores
from document_processing_mcp.services.trace_log import traced_invoke


class _LLMFieldMapping(BaseModel):
    """Azure/OpenAI-compatible schema — no Any / untyped fields."""

    json_path: str = Field(description="Dot path in JSON")
    placeholder: str = Field(description="Exact template key")
    value: str = Field(default="", description="JSON value as text")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    rationale: str = Field(default="", description="≤12 words")


class _LLMTableColumnMap(BaseModel):
    header: str
    json_field: str
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class _LLMTableFillPlan(BaseModel):
    table_index: int = Field(ge=0)
    array_json_path: str
    columns: list[_LLMTableColumnMap] = Field(default_factory=list)
    rationale: str = Field(default="", description="≤12 words")


class _LLMMappingPayload(BaseModel):
    """Structured mapper output — all properties have explicit JSON types for Azure."""

    mappings: list[_LLMFieldMapping] = Field(default_factory=list)
    table_fills: list[_LLMTableFillPlan] = Field(default_factory=list)
    unmapped_json_keys: list[str] = Field(default_factory=list)
    unmapped_placeholders: list[str] = Field(default_factory=list)
    notes: str = Field(default="")


def _resolve_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    token = ""
    i = 0
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if token:
                if not isinstance(current, dict) or token not in current:
                    return None
                current = current[token]
                token = ""
            i += 1
            continue
        if ch == "[":
            if token:
                if not isinstance(current, dict) or token not in current:
                    return None
                current = current[token]
                token = ""
            end = path.find("]", i)
            if end == -1:
                return None
            try:
                idx = int(path[i + 1 : end])
            except ValueError:
                return None
            if not isinstance(current, list) or idx >= len(current):
                return None
            current = current[idx]
            i = end + 1
            continue
        token += ch
        i += 1
    if token:
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 24)] + f"...<truncated:{len(text)}>"


def _dumps_compact(obj: Any, limit: int) -> str:
    return _clip(json.dumps(obj, separators=(",", ":"), default=str), limit)


def _summarize_json_for_mapper(data: Any, *, array_samples: int = 2) -> Any:
    """Keep keys and a few array rows; drop repeated product-line clones."""
    if isinstance(data, dict):
        return {key: _summarize_json_for_mapper(value, array_samples=array_samples) for key, value in data.items()}
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            sample = [
                _summarize_json_for_mapper(row, array_samples=array_samples)
                for row in data[:array_samples]
            ]
            if len(data) <= array_samples:
                return sample
            return {"_n": len(data), "_keys": list(data[0].keys()), "_sample": sample}
        return data[:array_samples]
    if isinstance(data, str) and len(data) > 240:
        return data[:240] + "…"
    return data


def _table_summaries(template: ExtractedTemplate) -> list[dict[str, Any]]:
    """Build table header summaries from extracted blocks for the LLM."""
    by_table: dict[int, dict[int, dict[int, str]]] = defaultdict(
        lambda: defaultdict(dict)
    )
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
                "i": t_idx,
                "h": headers,
                "rows": max(0, len(rows) - 1),
            }
        )
    return summaries


def _placeholder_occurrences(template: ExtractedTemplate) -> list[dict[str, Any]]:
    """One entry per placeholder hit with surrounding block text for disambiguation."""
    occurrences: list[dict[str, Any]] = []
    for b in template.blocks:
        if not b.placeholder_keys:
            continue
        text = (b.text or "").strip()
        if not text:
            continue
        for key in b.placeholder_keys:
            occurrences.append(
                {
                    "p": key,
                    "t": b.block_type,
                    "ctx": text[:180],
                }
            )
    return occurrences


def _normalize_mapped_value(placeholder: str, value: Any) -> Any:
    """Ensure bare XX%/X% replacements keep a trailing % when value is numeric."""
    if value is None:
        return value
    key = (placeholder or "").strip().upper()
    if key in {"XX%", "X%"}:
        text = str(value).strip()
        if text and not text.endswith("%"):
            return f"{text}%"
    return value


def _llm_mapping(
    template: ExtractedTemplate,
    data: dict[str, Any],
    *,
    model_id: str | None = None,
) -> MappingResult:
    from document_processing_mcp.services.llm_factory import MapperLLM, is_mapper_available
    from document_processing_mcp.services.prompts import build_mapper_chain

    if not is_mapper_available() and not model_id:
        raise RuntimeError(
            "Mapper LLM is required. Check MAPPER_PROVIDER credentials "
            "(for azure_openai: AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT + MAPPER_MODEL)."
        )

    try:
        mapper = MapperLLM(model_id=model_id, structured_schema=_LLMMappingPayload)
        llm, config = mapper.as_tuple()
    except (ImportError, RuntimeError, ValueError) as exc:
        raise RuntimeError(f"Failed to build mapper LLM: {exc}") from exc

    block_summaries = [
        {
            "id": b.block_id,
            "t": b.block_type,
            "ph": b.placeholder_keys,
            "ti": b.table_index,
        }
        for b in template.blocks
        if b.text.strip() and b.placeholder_keys
    ]
    tables = _table_summaries(template)
    occurrences = _placeholder_occurrences(template)
    chain = build_mapper_chain(llm)

    try:
        result: _LLMMappingPayload = traced_invoke(
            chain,
            {
                "placeholders_json": _dumps_compact(template.placeholders, 1500),
                "occurrences_json": _dumps_compact(occurrences, 2800),
                "tables_json": _dumps_compact(tables, 1500),
                "blocks_json": _dumps_compact(block_summaries, 1200),
                "data_json": _dumps_compact(_summarize_json_for_mapper(data), 4000),
            },
            role="mapper",
            provider=config.provider,
            model=config.model,
        )  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Mapper LLM invoke failed ({config.label}): {exc}") from exc

    mappings: list[FieldMapping] = []
    for item in result.mappings:
        resolved = _resolve_path(data, item.json_path) if item.json_path else None
        if resolved is None and item.value != "":
            # keep LLM-provided string when path does not resolve
            resolved = item.value
        elif resolved is None:
            resolved = item.value if item.value != "" else None
        resolved = _normalize_mapped_value(item.placeholder, resolved)
        mappings.append(
            FieldMapping(
                json_path=item.json_path,
                placeholder=item.placeholder,
                value=resolved,
                confidence=item.confidence,
                rationale=item.rationale or None,
            )
        )

    valid_fills: list[TableFillPlan] = []
    for plan in result.table_fills:
        arr = _resolve_path(data, plan.array_json_path)
        if isinstance(arr, list) and arr and isinstance(arr[0], dict) and plan.columns:
            valid_fills.append(
                TableFillPlan(
                    table_index=plan.table_index,
                    array_json_path=plan.array_json_path,
                    columns=[
                        TableColumnMap(
                            header=c.header,
                            json_field=c.json_field,
                            confidence=c.confidence,
                        )
                        for c in plan.columns
                    ],
                    rationale=plan.rationale or None,
                )
            )

    from document_processing_mcp.services.table_fill_infer import infer_table_fills, merge_table_fills

    if not mappings and not valid_fills:
        valid_fills = infer_table_fills(template, data)
        if not valid_fills:
            raise RuntimeError(
                "Mapper LLM returned no mappings/table_fills. "
                "Check template placeholders and JSON content."
            )

    payload = MappingResult(
        mappings=mappings,
        table_fills=valid_fills,
        unmapped_json_keys=list(result.unmapped_json_keys),
        unmapped_placeholders=list(result.unmapped_placeholders),
        notes=result.notes or f"LLM #1 analyzed template+JSON ({config.label})",
        mapper_source="llm",
        mapper_provider=config.provider,
        mapper_model=config.model,
    )
    enriched = enrich_mapping_scores(payload, len(template.placeholders) or 1)
    enriched = merge_table_fills(enriched, infer_table_fills(template, data))
    if enriched.table_fills and not template.placeholders:
        enriched.coverage_score = 1.0
        enriched.mapping_confidence = max(
            enriched.mapping_confidence,
            sum(c.confidence for p in enriched.table_fills for c in p.columns)
            / max(1, sum(len(p.columns) for p in enriched.table_fills)),
        )
    elif enriched.table_fills:
        enriched.coverage_score = max(enriched.coverage_score, 0.85)
    return enriched


def map_json_to_template(
    template: ExtractedTemplate,
    data: dict[str, Any],
    *,
    model_id: str | None = None,
) -> MappingResult:
    """Step 2: LLM-only mapping of JSON → placeholders / table fills (init_chat_model)."""
    return _llm_mapping(template, data, model_id=model_id)
