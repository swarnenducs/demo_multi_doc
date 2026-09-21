"""Score helpers — keep internal scores 0–1; present them as percentages."""

from __future__ import annotations

from typing import Any


def as_percent(score: float | None, digits: int = 1) -> float:
    """Convert 0–1 score to percentage number, e.g. 0.873 → 87.3."""
    if score is None:
        return 0.0
    return round(max(0.0, min(1.0, float(score))) * 100.0, digits)


def format_percent(score: float | None, digits: int = 1) -> str:
    """Human-readable percent string, e.g. '87.3%'."""
    return f"{as_percent(score, digits):.{digits}f}%"


def scores_to_percent_dict(
    *,
    overall: float | None = None,
    extraction: float | None = None,
    extraction_placeholder_detection: float | None = None,
    extraction_structure: float | None = None,
    placeholder_mapping: float | None = None,
    placeholder_coverage: float | None = None,
    table_mapping: float | None = None,
    generation_integrity: float | None = None,
    generation_confidence: float | None = None,
    validation: float | None = None,
    per_placeholder: list[dict[str, Any]] | None = None,
    per_table_column: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a UI/API-friendly percent report."""
    out: dict[str, Any] = {
        "overall_confidence_pct": as_percent(overall),
        "extraction_confidence_pct": as_percent(extraction),
        "extraction_placeholder_detection_pct": as_percent(extraction_placeholder_detection),
        "extraction_structure_pct": as_percent(extraction_structure),
        "placeholder_mapping_confidence_pct": as_percent(placeholder_mapping),
        "placeholder_coverage_pct": as_percent(placeholder_coverage),
        "table_mapping_confidence_pct": as_percent(table_mapping),
        "generation_integrity_pct": as_percent(generation_integrity),
        "generation_confidence_pct": as_percent(generation_confidence),
        "validation_score_pct": as_percent(validation),
    }
    if per_placeholder is not None:
        out["per_placeholder"] = [
            {
                **item,
                "confidence_pct": as_percent(item.get("confidence")),
            }
            for item in per_placeholder
        ]
    if per_table_column is not None:
        out["per_table_column"] = [
            {
                **item,
                "confidence_pct": as_percent(item.get("confidence")),
            }
            for item in per_table_column
        ]
    return out
