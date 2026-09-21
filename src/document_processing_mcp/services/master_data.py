"""Load legal/sales master-data blocks from SQLAlchemy (Azure SQL or SQLite).

Template placeholders such as ``<Legal_Department_Master_Data>`` are filled from
the ``master_data`` table unless JSON ``system_instruction`` sets ``override`` true.
"""

from __future__ import annotations

from typing import Any

from document_processing_mcp.models.schemas import ExtractedTemplate, FieldMapping, MappingResult
from document_processing_mcp.services.placeholders import normalize_placeholder_key

LEGAL_PLACEHOLDER = "Legal_Department_Master_Data"
SALES_PLACEHOLDER = "Sales_Excellence_Master_Data"

SYSTEM_INSTRUCTION_BLOCKS: dict[str, str] = {
    "legal_notice_block": LEGAL_PLACEHOLDER,
    "sales_notice_block": SALES_PLACEHOLDER,
}

SALES_EXCELLENCE_BLOCK = (
    "Sales Excellence\n"
    "200 Connell Drive, Suite 1000\n"
    "Berkeley Heights, NJ 07922\n"
    "E-mail: pmo@ABCTec.com"
)

LEGAL_DEPARTMENT_BLOCK = (
    "Legal Department\n"
    "200 Connell Drive, Suite 1000\n"
    "Berkeley Heights, NJ 07922\n"
    "E-mail: pmo@ABCTec.com"
)

DEFAULT_MASTER_DATA_ROWS: tuple[dict[str, str], ...] = (
    {
        "id": "md-legal-department",
        "placeholder_key": LEGAL_PLACEHOLDER,
        "category": "legal",
        "content": LEGAL_DEPARTMENT_BLOCK,
    },
    {
        "id": "md-sales-excellence",
        "placeholder_key": SALES_PLACEHOLDER,
        "category": "sales",
        "content": SALES_EXCELLENCE_BLOCK,
    },
)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_master_placeholder(key: str) -> bool:
    normalized = normalize_placeholder_key(key).replace(" ", "_")
    if normalized in {LEGAL_PLACEHOLDER, SALES_PLACEHOLDER}:
        return True
    return normalized.endswith("_Master_Data") or normalized.endswith("Master_Data")


def _instruction_map(json_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = json_data.get("system_instruction")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for block_id, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        placeholder = str(spec.get("placeholder") or SYSTEM_INSTRUCTION_BLOCKS.get(block_id) or "").strip()
        if not placeholder:
            continue
        out[normalize_placeholder_key(placeholder).replace(" ", "_")] = spec
    return out


def candidate_placeholder_keys(
    json_data: dict[str, Any],
    extracted: ExtractedTemplate | None,
) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        key = normalize_placeholder_key(raw).replace(" ", "_")
        if key and key not in seen and is_master_placeholder(key):
            seen.add(key)
            keys.append(key)

    for default in (LEGAL_PLACEHOLDER, SALES_PLACEHOLDER):
        _add(default)
    for spec_key in _instruction_map(json_data):
        _add(spec_key)
    if extracted is not None:
        for ph in extracted.placeholders:
            _add(ph)
    return keys


def _load_sql_content(placeholder_keys: list[str]) -> dict[str, str]:
    if not placeholder_keys:
        return {}
    from sqlalchemy import select

    from document_processing_mcp.storage.db import get_session_factory
    from document_processing_mcp.storage.sql_models import MasterData

    factory = get_session_factory()
    found: dict[str, str] = {}
    with factory() as session:
        rows = session.scalars(
            select(MasterData).where(
                MasterData.placeholder_key.in_(placeholder_keys),
                MasterData.active == "true",
            )
        ).all()
        for row in rows:
            found[row.placeholder_key] = row.content
    return found


def apply_master_data_to_json(
    json_data: dict[str, Any],
    *,
    extracted: ExtractedTemplate | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Return updated JSON plus a trace of source=sql|payload for each key."""
    payload = dict(json_data)
    keys = candidate_placeholder_keys(payload, extracted)
    instructions = _instruction_map(payload)
    need_sql = [
        key
        for key in keys
        if not _truthy(instructions.get(key, {}).get("override"))
    ]
    sql_values = _load_sql_content(need_sql) if need_sql else {}
    applied: list[dict[str, str]] = []
    for key in keys:
        spec = instructions.get(key) or {}
        if _truthy(spec.get("override")):
            value = spec.get("value")
            text = "" if value is None else str(value)
            if not text.strip():
                text = sql_values.get(key, "")
                source = "sql_fallback"
            else:
                source = "payload"
        else:
            text = sql_values.get(key, "")
            source = "sql"
        if not text:
            applied.append({"placeholder": key, "source": "missing"})
            continue
        payload[key] = text
        applied.append({"placeholder": key, "source": source})
    return payload, applied


def merge_master_data_into_mapping(
    template: ExtractedTemplate,
    data: dict[str, Any],
    mapping: MappingResult,
) -> MappingResult:
    """Force high-confidence mappings for master-data keys already on JSON."""
    wanted = {
        normalize_placeholder_key(ph).replace(" ", "_")
        for ph in template.placeholders
        if is_master_placeholder(ph)
    }
    mappings = list(mapping.mappings)
    by_ph = {
        normalize_placeholder_key(m.placeholder or "").replace(" ", "_"): i
        for i, m in enumerate(mappings)
        if m.placeholder
    }
    for key in wanted:
        value = data.get(key)
        if value is None or not str(value).strip():
            continue
        field = FieldMapping(
            json_path=key,
            placeholder=key,
            value=str(value),
            confidence=1.0,
            rationale="master_data SQL or system_instruction override",
        )
        idx = by_ph.get(key)
        if idx is None:
            mappings.append(field)
        else:
            mappings[idx] = field
    unmapped = [
        ph
        for ph in mapping.unmapped_placeholders
        if normalize_placeholder_key(ph).replace(" ", "_") not in wanted
        or not str(data.get(normalize_placeholder_key(ph).replace(" ", "_")) or "").strip()
    ]
    return mapping.model_copy(update={"mappings": mappings, "unmapped_placeholders": unmapped})
