"""Mapper prompt payload stays compact (no duplicate full-block dumps)."""

from __future__ import annotations

import json

from document_processing_mcp.services.field_mapper import _dumps_compact, _summarize_json_for_mapper


def test_summarize_json_samples_long_object_arrays() -> None:
    data = {
        "accountName": "Acme",
        "products": [
            {"productCode": f"P{i}", "price": i} for i in range(20)
        ],
    }
    compact = _summarize_json_for_mapper(data)
    assert compact["accountName"] == "Acme"
    assert compact["products"]["_n"] == 20
    assert compact["products"]["_keys"] == ["productCode", "price"]
    assert len(compact["products"]["_sample"]) == 2
    raw = json.dumps(data, separators=(",", ":"))
    slim = _dumps_compact(compact, 4000)
    assert len(slim) < len(raw)


def test_dumps_compact_clips_long_payload() -> None:
    blob = {"x": "a" * 500}
    text = _dumps_compact(blob, 80)
    assert "truncated" in text
    assert len(text) <= 80
