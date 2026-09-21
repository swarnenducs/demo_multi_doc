"""Infer Word table → JSON array fills from headers + payload (no extra LLM).

Used when the mapper skips empty/header-only price tables so unmarked GPO
templates still get product rows.
"""

from __future__ import annotations

import re
from typing import Any

from collections import defaultdict

from document_processing_mcp.models.schemas import (
    ExtractedTemplate,
    MappingResult,
    TableColumnMap,
    TableFillPlan,
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Normalized header token → preferred JSON keys (first hit in the row wins).
_HEADER_TO_KEYS: dict[str, tuple[str, ...]] = {
    "productcode": ("productCode", "product_code", "sku", "itemCode", "code"),
    "abctecproductdescription": ("productDescription", "product_description", "description"),
    "productdescription": ("productDescription", "product_description", "description"),
    "description": ("productDescription", "description", "name"),
    "eauom": ("eaUom", "ea_uom", "eachUom"),
    "uom": ("uom", "unitOfMeasure", "unit"),
    "priceea": ("eaPrice", "ea_price", "unit_price", "unitPrice", "price"),
    "priceuom": ("marketPrice", "market_price", "uomPrice", "priceUom"),
    "priceeach": ("eaPrice", "ea_price", "unit_price"),
}


def normalize_header(text: str) -> str:
    return _NON_ALNUM.sub("", (text or "").lower())


def _object_arrays(data: dict[str, Any], prefix: str = "") -> list[tuple[str, list[dict[str, Any]]]]:
    found: list[tuple[str, list[dict[str, Any]]]] = []
    if not isinstance(data, dict):
        return found
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, list) and value and all(isinstance(x, dict) for x in value[:3]):
            found.append((path, value))  # type: ignore[arg-type]
        elif isinstance(value, dict):
            found.extend(_object_arrays(value, path))
    return found


def match_header_to_field(header: str, row_keys: list[str]) -> str | None:
    keys_by_norm = {normalize_header(k): k for k in row_keys}
    token = normalize_header(header)
    if not token:
        return None
    if token in keys_by_norm:
        return keys_by_norm[token]
    if token in _HEADER_TO_KEYS:
        for cand in _HEADER_TO_KEYS[token]:
            n = normalize_header(cand)
            if n in keys_by_norm:
                return keys_by_norm[n]
    for alias, candidates in _HEADER_TO_KEYS.items():
        if alias == token:
            continue
        if len(alias) >= 5 and (token.endswith(alias) or alias.endswith(token) or alias in token):
            for cand in candidates:
                n = normalize_header(cand)
                if n in keys_by_norm:
                    return keys_by_norm[n]
    for orig in row_keys:
        nk = normalize_header(orig)
        if nk and (nk in token or token in nk) and len(nk) >= 3:
            return orig
    return None


def _score_array_for_headers(headers: list[str], row: dict[str, Any]) -> int:
    keys = list(row.keys())
    return sum(1 for h in headers if match_header_to_field(h, keys))


def _table_headers(template: ExtractedTemplate) -> list[tuple[int, list[str]]]:
    by_table: dict[int, dict[int, dict[int, str]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for b in template.blocks:
        if b.block_type != "table_cell" or b.table_index is None:
            continue
        if b.row_index is None or b.cell_index is None:
            continue
        by_table[b.table_index][b.row_index][b.cell_index] = (b.text or "").strip()
    out: list[tuple[int, list[str]]] = []
    for t_idx in sorted(by_table):
        rows = by_table[t_idx]
        if 0 not in rows:
            continue
        headers = [rows[0][c] for c in sorted(rows[0])]
        out.append((t_idx, headers))
    return out


def infer_table_fills(template: ExtractedTemplate, data: dict[str, Any]) -> list[TableFillPlan]:
    arrays = _object_arrays(data)
    if not arrays:
        return []
    plans: list[TableFillPlan] = []
    for t_idx, headers in _table_headers(template):
        headers = [str(h).strip() for h in headers if str(h).strip()]
        if len(headers) < 2:
            continue
        best_path = ""
        best_row: dict[str, Any] = {}
        best_score = 0
        for path, rows in arrays:
            score = _score_array_for_headers(headers, rows[0])
            # Prefer product-like arrays when scores tie.
            bonus = 1 if "product" in path.lower() or "item" in path.lower() else 0
            ranked = score * 10 + bonus
            if score >= 2 and ranked > best_score:
                best_score = ranked
                best_path = path
                best_row = rows[0]
        if not best_path:
            continue
        keys = list(best_row.keys())
        columns = [
            TableColumnMap(
                header=header,
                json_field=field,
                confidence=0.95,
            )
            for header in headers
            if (field := match_header_to_field(header, keys))
        ]
        if len(columns) < 2:
            continue
        plans.append(
            TableFillPlan(
                table_index=t_idx,
                array_json_path=best_path,
                columns=columns,
                rationale="Inferred from table headers and JSON array",
            )
        )
    return plans


def merge_table_fills(mapping: MappingResult, inferred: list[TableFillPlan]) -> MappingResult:
    if not inferred:
        return mapping
    by_index = {p.table_index: p for p in mapping.table_fills}
    for plan in inferred:
        current = by_index.get(plan.table_index)
        if current is None or len(current.columns) < len(plan.columns):
            by_index[plan.table_index] = plan
    fills = [by_index[i] for i in sorted(by_index)]
    return mapping.model_copy(update={"table_fills": fills})
