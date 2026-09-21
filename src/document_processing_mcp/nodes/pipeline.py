"""LangGraph nodes for the Word document pipeline (tools-backed services)."""

from __future__ import annotations

import json
from pathlib import Path

from document_processing_mcp.models.state import DocumentProcessingState
from document_processing_mcp.core.settings import settings
from document_processing_mcp.services.confidence import build_confidence_report
from document_processing_mcp.services.document_generator import generate_styled_document
from document_processing_mcp.services.document_validator import validate_documents
from document_processing_mcp.services.extraction_validator import validate_extraction
from document_processing_mcp.services.field_mapper import map_json_to_template
from document_processing_mcp.services.style_extractor import extract_word_styles


def load_data_node(state: DocumentProcessingState) -> DocumentProcessingState:
    """Load JSON payload that will be mapped onto the Word template."""
    from document_processing_mcp.flow_debug import flow_breakpoint

    flow_breakpoint("load_data_node", data_path=state.get("data_path"))
    errors = list(state.get("errors") or [])
    data_path = state.get("data_path")
    if not data_path:
        errors.append("data_path is required")
        return {**state, "errors": errors, "status": "failed"}

    path = Path(data_path)
    if not path.exists():
        errors.append(f"Data file not found: {path}")
        return {**state, "errors": errors, "status": "failed"}

    with path.open(encoding="utf-8") as fh:
        payload = json.load(fh)

    if not isinstance(payload, dict):
        errors.append("JSON root must be an object")
        return {**state, "errors": errors, "status": "failed"}

    cfg = settings()
    retries = state.get("max_retries")
    threshold = state.get("validation_threshold")
    optimized = bool(state.get("optimized_flow"))
    return {
        **state,
        "json_data": payload,
        "retry_count": state.get("retry_count", 0),
        "max_retries": (
            retries
            if optimized
            else (cfg.document_max_retries if retries is None else retries)
        ),
        "validation_threshold": (
            threshold
            if optimized
            else (
                cfg.document_validation_threshold if threshold is None else threshold
            )
        ),
        "status": "data_loaded",
        "errors": errors,
    }


def extract_styles_node(state: DocumentProcessingState) -> DocumentProcessingState:
    """Step 1 — extract Word XML styles and placeholders from the template."""
    from document_processing_mcp.flow_debug import flow_breakpoint

    flow_breakpoint("extract_styles_node", template_path=state.get("template_path"))
    errors = list(state.get("errors") or [])
    if state.get("status") == "failed":
        return state

    template_path = state.get("template_path")
    if not template_path:
        errors.append("template_path is required")
        return {**state, "errors": errors, "status": "failed"}

    try:
        extracted = extract_word_styles(template_path)
    except Exception as exc:  # noqa: BLE001 - surface to graph state
        errors.append(f"Style extraction failed: {exc}")
        return {**state, "errors": errors, "status": "failed"}

    updates: dict = {
        "extracted": extracted,
        "status": "styles_extracted",
        "errors": errors,
    }
    if state.get("optimized_flow"):
        from document_processing_mcp.services.llm_optimization import apply_optimization

        try:
            updates.update(apply_optimization({**state, **updates}, extracted))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"LLM optimisation config failed: {exc}")
            return {**state, "errors": errors, "status": "failed"}
    return {**state, **updates}


def synthesize_markers_node(state: DocumentProcessingState) -> DocumentProcessingState:
    """If the Word file has no <markers>, LLM proposes them; code stamps a marked copy.

    Off when ``DOCUMENT_MARKER_SYNTHESIS_ENABLED=false`` (alias
    ``DOCUMENT_NON_TAG_ENABLED``). Tagged templates still continue.
    """
    from document_processing_mcp.core.settings import settings
    from document_processing_mcp.flow_debug import flow_breakpoint
    from document_processing_mcp.services.marker_synthesizer import synthesize_markers_if_needed
    from document_processing_mcp.services.placeholders import template_has_markers
    from document_processing_mcp.services.style_extractor import extract_word_styles

    flow_breakpoint("synthesize_markers_node", status=state.get("status"))
    errors = list(state.get("errors") or [])
    if state.get("status") == "failed":
        return state

    extracted = state.get("extracted")
    if extracted is None:
        errors.append("extracted template is required before marker synthesis")
        return {**state, "errors": errors, "status": "failed"}

    if not settings().document_marker_synthesis_enabled:
        has, keys = template_has_markers(
            extracted, template_path=extracted.template_path
        )
        marker_detection = {
            "had_markers": has,
            "placeholder_keys": keys,
            "reason": "marker_synthesis_disabled",
            "library_match": None,
        }
        if not has:
            errors.append(
                "Template has no placeholders and DOCUMENT_MARKER_SYNTHESIS_ENABLED is off. "
                "Use a tagged Word file (<field>, {{field}}, …) or set "
                "DOCUMENT_MARKER_SYNTHESIS_ENABLED=true."
            )
            return {
                **state,
                "errors": errors,
                "status": "failed",
                "marker_detection": marker_detection,
            }
        return {
            **state,
            "marker_detection": marker_detection,
            "status": "markers_ready",
            "errors": errors,
        }

    try:
        result = synthesize_markers_if_needed(
            extracted,
            state.get("json_data"),
            model_id=state.get("mapper_model_id"),
            output_dir=Path(state.get("output_path") or extracted.template_path).parent,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Marker synthesis failed: {exc}")
        return {**state, "errors": errors, "status": "failed"}

    updates: dict = {
        "marker_detection": {
            "had_markers": bool(result.get("skipped")),
            "placeholder_keys": result.get("placeholder_keys") or [],
            "reason": result.get("reason"),
            "library_match": result.get("library_match"),
        },
        "errors": errors,
    }
    if result.get("skipped"):
        updates["status"] = "markers_ready"
        return {**state, **updates}

    marked_path = str(result.get("template_path") or "")
    try:
        extracted = extract_word_styles(marked_path)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Re-extract after marker synthesis failed: {exc}")
        return {**state, "errors": errors, "status": "failed"}

    updates.update(
        {
            "extracted": extracted,
            "template_path": marked_path,
            "synthesized_template_path": marked_path,
            "status": "markers_synthesized",
            "marker_detection": {
                **updates["marker_detection"],
                "placeholder_keys": list(extracted.placeholders or []),
                "replacements_applied": result.get("replacements_applied"),
                "notes": result.get("plan_notes"),
            },
        }
    )
    return {**state, **updates}


def enrich_master_data_node(state: DocumentProcessingState) -> DocumentProcessingState:
    """Fill ``*_Master_Data`` keys from SQL unless ``system_instruction`` override is true."""
    from document_processing_mcp.flow_debug import flow_breakpoint
    from document_processing_mcp.services.master_data import apply_master_data_to_json
    from document_processing_mcp.storage.db import ensure_schema

    flow_breakpoint("enrich_master_data_node", status=state.get("status"))
    errors = list(state.get("errors") or [])
    if state.get("status") == "failed":
        return state

    json_data = state.get("json_data")
    if not isinstance(json_data, dict):
        errors.append("json_data is required before master-data enrichment")
        return {**state, "errors": errors, "status": "failed"}

    try:
        ensure_schema()
        payload, applied = apply_master_data_to_json(
            json_data,
            extracted=state.get("extracted"),
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Master-data enrichment failed: {exc}")
        return {
            **state,
            "status": "master_data_skipped",
            "errors": errors,
            "master_data_applied": [{"placeholder": "*", "source": "error"}],
        }

    return {
        **state,
        "json_data": payload,
        "master_data_applied": applied,
        "status": "master_data_enriched",
        "errors": errors,
    }


def validate_extraction_node(state: DocumentProcessingState) -> DocumentProcessingState:
    """LLM critic of extracted Word XML / placeholders (confidence on extraction)."""
    from document_processing_mcp.flow_debug import flow_breakpoint

    flow_breakpoint("validate_extraction_node", skip=state.get("skip_extraction_validation"))
    errors = list(state.get("errors") or [])
    if state.get("status") == "failed":
        return state

    if state.get("skip_extraction_validation"):
        return {**state, "status": "extraction_validation_skipped", "errors": errors}

    extracted = state.get("extracted")
    if extracted is None:
        errors.append("extracted template is required before extraction validation")
        return {**state, "errors": errors, "status": "failed"}

    try:
        extraction_validation = validate_extraction(
            extracted,
            model_id=state.get("validator_model_id"),
        )
    except Exception:  # noqa: BLE001
        # Soft-fail: deterministic extract already succeeded; continue pipeline.
        return {
            **state,
            "status": "extraction_validation_skipped",
            "errors": errors,
        }

    return {
        **state,
        "extraction_validation": extraction_validation,
        "status": "extraction_validated",
        "errors": errors,
    }


def map_fields_node(state: DocumentProcessingState) -> DocumentProcessingState:
    """Step 2 — map JSON data onto template placeholders (LLM #1 required)."""
    from document_processing_mcp.flow_debug import flow_breakpoint

    flow_breakpoint("map_fields_node", retry_count=state.get("retry_count"))
    errors = list(state.get("errors") or [])
    if state.get("status") == "failed":
        return state

    extracted = state.get("extracted")
    json_data = state.get("json_data")
    if extracted is None or json_data is None:
        errors.append("extracted template and json_data are required before mapping")
        return {**state, "errors": errors, "status": "failed"}

    try:
        mapping = map_json_to_template(
            extracted,
            json_data,
            model_id=state.get("mapper_model_id"),
        )
        from document_processing_mcp.services.master_data import merge_master_data_into_mapping

        mapping = merge_master_data_into_mapping(extracted, json_data, mapping)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Field mapping failed: {exc}")
        return {**state, "errors": errors, "status": "failed"}

    return {
        **state,
        "mapping": mapping,
        "status": "fields_mapped",
        "errors": errors,
    }


def generate_document_node(state: DocumentProcessingState) -> DocumentProcessingState:
    """Step 3 — generate a new .docx with mapped values and preserved styles."""
    from document_processing_mcp.flow_debug import flow_breakpoint

    flow_breakpoint("generate_document_node", output_path=state.get("output_path"))
    errors = list(state.get("errors") or [])
    if state.get("status") == "failed":
        return state

    extracted = state.get("extracted")
    mapping = state.get("mapping")
    output_path = state.get("output_path")
    if extracted is None or mapping is None or not output_path:
        errors.append("extracted, mapping, and output_path are required for generation")
        return {**state, "errors": errors, "status": "failed"}

    try:
        generation = generate_styled_document(
            extracted, mapping, output_path, json_data=state.get("json_data")
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Document generation failed: {exc}")
        return {**state, "errors": errors, "status": "failed"}

    return {
        **state,
        "generation": generation,
        "status": "document_generated",
        "errors": errors,
    }


def validate_document_node(state: DocumentProcessingState) -> DocumentProcessingState:
    """Step 4 — validate template vs generated doc vs JSON (LLM #2 + rules)."""
    from document_processing_mcp.flow_debug import flow_breakpoint

    flow_breakpoint("validate_document_node", skip=state.get("skip_validation"))
    errors = list(state.get("errors") or [])
    if state.get("status") == "failed":
        return state

    from document_processing_mcp.services.llm_optimization import should_skip_document_critic

    if state.get("skip_validation") or should_skip_document_critic(state):
        from document_processing_mcp.models.schemas import ValidationResult

        validation = None
        if should_skip_document_critic(state) and not state.get("skip_validation"):
            validation = ValidationResult(
                passed=True,
                validation_score=1.0,
                summary="Document critic skipped: easy job and no leftover placeholders",
                validator_source="regex",
            )
        confidence = build_confidence_report(
            state.get("mapping"),
            state.get("generation"),
            validation,
            extraction_validation=state.get("extraction_validation"),
            marker_detection=state.get("marker_detection"),
        )
        updates = {
            **state,
            "confidence": confidence,
            "status": "completed",
            "errors": errors,
        }
        if validation is not None:
            updates["validation"] = validation
        return updates

    extracted = state.get("extracted")
    mapping = state.get("mapping")
    generation = state.get("generation")
    json_data = state.get("json_data")
    if extracted is None or mapping is None or generation is None or json_data is None:
        errors.append("extracted, mapping, generation, and json_data required for validation")
        return {**state, "errors": errors, "status": "failed"}

    try:
        validation = validate_documents(
            extracted,
            generation.output_path,
            json_data,
            mapping,
            model_id=state.get("validator_model_id"),
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Validation failed: {exc}")
        return {**state, "errors": errors, "status": "failed"}

    confidence = build_confidence_report(
        mapping,
        generation,
        validation,
        extraction_validation=state.get("extraction_validation"),
        marker_detection=state.get("marker_detection"),
    )

    return {
        **state,
        "validation": validation,
        "confidence": confidence,
        "status": "validated",
        "errors": errors,
    }


def finalize_node(state: DocumentProcessingState) -> DocumentProcessingState:
    """Mark pipeline complete and ensure confidence report exists."""
    from document_processing_mcp.flow_debug import flow_breakpoint

    flow_breakpoint("finalize_node", status=state.get("status"))
    errors = list(state.get("errors") or [])
    confidence = state.get("confidence")
    if confidence is None:
        confidence = build_confidence_report(
            state.get("mapping"),
            state.get("generation"),
            state.get("validation"),
            extraction_validation=state.get("extraction_validation"),
            marker_detection=state.get("marker_detection"),
        )
    return {
        **state,
        "confidence": confidence,
        "status": "completed",
        "errors": errors,
    }
