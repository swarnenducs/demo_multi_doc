"""Generate a Word document from LLM mapping plans while preserving Word XML styles."""

from __future__ import annotations

import copy
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

from document_processing_mcp.models.schemas import (
    ExtractedTemplate,
    GenerationResult,
    MappingResult,
    TableFillPlan,
)
from document_processing_mcp.services.placeholders import PLACEHOLDER_PATTERNS

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def _qn(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _build_replacement_map(mapping: MappingResult) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for item in mapping.mappings:
        if not item.placeholder:
            continue
        value = "" if item.value is None else str(item.value)
        key = item.placeholder.strip()
        replacements[key] = value
        replacements[item.placeholder] = value
        # Bare percent tokens are matched case-insensitively in patterns;
        # also register canonical upper key used by find_placeholders.
        if key.upper() in {"XX%", "X%"}:
            replacements[key.upper()] = value
            if value and not str(value).endswith("%"):
                replacements[key.upper()] = f"{value}%"
                replacements[key] = f"{value}%"
        # Mirror angle-key XX → also fill bare XX% when only <XX> was mapped.
        if key.upper() == "XX" and "XX%" not in {k.upper() for k in replacements}:
            pct = value if str(value).endswith("%") else f"{value}%"
            replacements["XX%"] = pct
    return replacements


def _replace_in_text(text: str, replacements: dict[str, str]) -> tuple[str, int]:
    applied = 0
    result = text
    lower_map = {k.lower(): v for k, v in replacements.items()}

    for pattern, _open, _close in PLACEHOLDER_PATTERNS:

        def _sub(match: re.Match[str]) -> str:
            nonlocal applied
            key = match.group(1).strip()
            if key in replacements:
                applied += 1
                return replacements[key]
            if key.lower() in lower_map:
                applied += 1
                return lower_map[key.lower()]
            return match.group(0)

        result = pattern.sub(_sub, result)
    return result, applied


def _paragraph_full_text(paragraph: etree._Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == _qn("t") and node.text:
            parts.append(node.text)
        elif node.tag == _qn("tab"):
            parts.append("\t")
        elif node.tag == _qn("br"):
            parts.append("\n")
    return "".join(parts)


def _set_paragraph_text_preserving_style(paragraph: etree._Element, new_text: str) -> None:
    runs = paragraph.findall("w:r", NS)
    if not runs:
        run = etree.SubElement(paragraph, _qn("r"))
        text_el = etree.SubElement(run, _qn("t"))
        text_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text_el.text = new_text
        return

    first_run = runs[0]
    rpr = first_run.find("w:rPr", NS)
    for run in runs:
        paragraph.remove(run)

    new_run = etree.Element(_qn("r"))
    if rpr is not None:
        new_run.append(rpr)
    text_el = etree.SubElement(new_run, _qn("t"))
    text_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_el.text = new_text
    paragraph.append(new_run)


def _replace_within_runs(paragraph: etree._Element, replacements: dict[str, str]) -> int:
    applied = 0
    for text_el in paragraph.iter(_qn("t")):
        if not text_el.text:
            continue
        updated, count = _replace_in_text(text_el.text, replacements)
        if count:
            text_el.text = updated
            text_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            applied += count
    return applied


def _still_has_mapped_placeholder(text: str, replacements: dict[str, str]) -> bool:
    lower_map = {k.lower() for k in replacements}
    for pattern, _o, _c in PLACEHOLDER_PATTERNS:
        for match in pattern.finditer(text):
            key = match.group(1).strip()
            if key in replacements or key.lower() in lower_map:
                return True
    return False


def _cell_text(cell: etree._Element) -> str:
    return " ".join(
        _paragraph_full_text(p).strip() for p in cell.findall("w:p", NS)
    ).strip()


def _strip_bold_from_rpr(rpr: etree._Element) -> etree._Element:
    """Keep font/size/color from a run style, but force data cells to be non-bold."""
    cleaned = copy.deepcopy(rpr)
    for tag in ("b", "bCs"):
        node = cleaned.find(f"w:{tag}", NS)
        if node is not None:
            cleaned.remove(node)
    return cleaned


def _make_simple_cell(
    text: str,
    template_cell: etree._Element | None = None,
    *,
    strip_bold: bool = True,
) -> etree._Element:
    """
    Build a table cell cloning paragraph/run style from ``template_cell``.

    For data rows, ``strip_bold=True`` (default) removes bold markers so headers
    can stay bold while body values use the same font without bold.
    """
    if template_cell is not None:
        cell = copy.deepcopy(template_cell)
        for p in list(cell.findall("w:p", NS)):
            cell.remove(p)
        p = etree.SubElement(cell, _qn("p"))
        tpl_p = template_cell.find("w:p", NS)
        if tpl_p is not None:
            ppr = tpl_p.find("w:pPr", NS)
            if ppr is not None:
                p.append(copy.deepcopy(ppr))
            tpl_r = tpl_p.find("w:r", NS)
            r = etree.SubElement(p, _qn("r"))
            if tpl_r is not None:
                rpr = tpl_r.find("w:rPr", NS)
                if rpr is not None:
                    r.append(_strip_bold_from_rpr(rpr) if strip_bold else copy.deepcopy(rpr))
            t = etree.SubElement(r, _qn("t"))
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            t.text = text
        else:
            r = etree.SubElement(p, _qn("r"))
            t = etree.SubElement(r, _qn("t"))
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            t.text = text
        return cell

    cell = etree.Element(_qn("tc"))
    p = etree.SubElement(cell, _qn("p"))
    r = etree.SubElement(p, _qn("r"))
    t = etree.SubElement(r, _qn("t"))
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    return cell


def _resolve_array(data: dict[str, Any], path: str) -> list[dict[str, Any]]:
    current: Any = data
    for part in path.replace("]", "").split("."):
        if not part:
            continue
        if "[" in part:
            key, idx_s = part.split("[", 1)
            if key:
                if not isinstance(current, dict) or key not in current:
                    return []
                current = current[key]
            try:
                current = current[int(idx_s)]  # type: ignore[index]
            except (ValueError, TypeError, IndexError):
                return []
        else:
            if not isinstance(current, dict) or part not in current:
                return []
            current = current[part]
    if isinstance(current, list) and current and isinstance(current[0], dict):
        return current  # type: ignore[return-value]
    return []


def _expand_tables_from_plans(
    root: etree._Element,
    json_data: dict[str, Any] | None,
    plans: list[TableFillPlan],
) -> int:
    """Fill tables using LLM TableFillPlan — column mapping comes from the LLM, not hardcoded aliases."""
    if not json_data or not plans:
        return 0

    body = root.find("w:body", NS)
    if body is None:
        return 0

    tables = body.findall("w:tbl", NS)
    inserted = 0

    for plan in plans:
        if plan.table_index < 0 or plan.table_index >= len(tables):
            continue
        table = tables[plan.table_index]
        rows_data = _resolve_array(json_data, plan.array_json_path)
        if not rows_data or not plan.columns:
            continue

        trs = table.findall("w:tr", NS)
        if not trs:
            continue
        header_row = trs[0]
        header_cells = header_row.findall("w:tc", NS)
        headers = [_cell_text(c) for c in header_cells]

        # Prefer an existing body row as the visual template when present;
        # otherwise clone header style but strip bold for data cells.
        if len(trs) > 1:
            style_cells = trs[1].findall("w:tc", NS)
        else:
            style_cells = header_cells

        col_by_header = {c.header.strip().lower(): c.json_field for c in plan.columns}
        col_by_norm = {
            "".join(ch for ch in c.header.lower() if ch.isalnum()): c.json_field
            for c in plan.columns
        }
        field_per_col: list[str | None] = []
        for h in headers:
            field = col_by_header.get(h.strip().lower())
            if not field:
                field = col_by_norm.get("".join(ch for ch in h.lower() if ch.isalnum()))
            field_per_col.append(field)
        if not any(field_per_col):
            continue

        for old in trs[1:]:
            table.remove(old)

        for row_data in rows_data:
            new_tr = etree.Element(_qn("tr"))
            tr_pr = header_row.find("w:trPr", NS)
            if tr_pr is not None:
                new_tr.append(copy.deepcopy(tr_pr))
            for idx, field in enumerate(field_per_col):
                value = "" if field is None else str(row_data.get(field, ""))
                tpl_cell = style_cells[idx] if idx < len(style_cells) else (
                    header_cells[idx] if idx < len(header_cells) else None
                )
                # Data rows: same font/size/color as template, never bold
                new_tr.append(_make_simple_cell(value, tpl_cell, strip_bold=True))
            table.append(new_tr)
            inserted += 1

    return inserted


def _apply_replacements_to_document_xml(
    document_xml: str,
    replacements: dict[str, str],
    json_data: dict[str, Any] | None = None,
    table_fills: list[TableFillPlan] | None = None,
) -> tuple[str, int]:
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    root = etree.fromstring(document_xml.encode("utf-8"), parser)
    body = root.find("w:body", NS)
    if body is None:
        return document_xml, 0

    rows_added = _expand_tables_from_plans(root, json_data, table_fills or [])
    total_applied = rows_added

    for paragraph in body.iter(_qn("p")):
        original = _paragraph_full_text(paragraph)
        if not original:
            continue

        applied = _replace_within_runs(paragraph, replacements)
        total_applied += applied

        remaining = _paragraph_full_text(paragraph)
        if _still_has_mapped_placeholder(remaining, replacements):
            updated, count = _replace_in_text(remaining, replacements)
            if count:
                _set_paragraph_text_preserving_style(paragraph, updated)
                total_applied += count

    xml_bytes = etree.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=True,
        standalone=True,
    )
    return xml_bytes.decode("utf-8"), total_applied


def _integrity_checks(
    dest: Path,
    new_document_xml: str,
    mapping: MappingResult,
) -> tuple[float, list[str], bool]:
    leftovers: list[str] = []
    seen: set[str] = set()
    mapped_keys = {m.placeholder for m in mapping.mappings if m.placeholder}
    mapped_lower = {k.lower() for k in mapped_keys}
    for pattern, _o, _c in PLACEHOLDER_PATTERNS:
        for match in pattern.finditer(new_document_xml):
            key = match.group(1).strip()
            if (key in mapped_keys or key.lower() in mapped_lower) and key not in seen:
                seen.add(key)
                leftovers.append(key)

    styles_ok = False
    try:
        with zipfile.ZipFile(dest, "r") as zf:
            styles_ok = "word/styles.xml" in zf.namelist() and "word/document.xml" in zf.namelist()
            _ = zf.read("word/document.xml")
    except (OSError, zipfile.BadZipFile):
        styles_ok = False

    score = 1.0
    if leftovers:
        score -= min(0.5, 0.15 * len(leftovers))
    if not styles_ok:
        score -= 0.4
    if mapping.mappings and applied_ratio_penalty(mapping, dest):
        score -= 0.1

    return round(min(max(score, 0.0), 1.0), 4), leftovers, styles_ok


def applied_ratio_penalty(mapping: MappingResult, dest: Path) -> bool:
    try:
        with zipfile.ZipFile(dest, "r") as zf:
            text = zf.read("word/document.xml").decode("utf-8")
    except (OSError, zipfile.BadZipFile):
        return True
    missing = 0
    checked = 0
    for item in mapping.mappings:
        if item.value is None:
            continue
        value = str(item.value)
        if not value:
            continue
        checked += 1
        if value not in text:
            missing += 1
    return checked > 0 and (missing / checked) > 0.25


def generate_styled_document(
    template: ExtractedTemplate,
    mapping: MappingResult,
    output_path: str | Path,
    json_data: dict[str, Any] | None = None,
) -> GenerationResult:
    """
    Apply LLM mapping plans:
      - scalar placeholder replacements
      - table_fills (array → rows) decided by the mapper LLM
    """
    src = Path(template.template_path)
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.resolve() == src.resolve():
        raise ValueError("output_path must differ from the template path")

    shutil.copy2(src, dest)

    replacements = _build_replacement_map(mapping)
    if not template.document_xml:
        raise ValueError("Extracted template is missing document_xml")

    new_document_xml, applied = _apply_replacements_to_document_xml(
        template.document_xml,
        replacements,
        json_data=json_data,
        table_fills=mapping.table_fills,
    )

    tmp_path = dest.with_suffix(dest.suffix + ".tmp")
    with zipfile.ZipFile(dest, "r") as zin, zipfile.ZipFile(
        tmp_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                data = new_document_xml.encode("utf-8")
            zout.writestr(item, data)

    tmp_path.replace(dest)

    integrity, leftovers, styles_ok = _integrity_checks(dest, new_document_xml, mapping)
    generation_confidence = round(
        (0.6 * mapping.mapping_confidence) + (0.4 * integrity),
        4,
    )

    return GenerationResult(
        output_path=str(dest.resolve()),
        applied_mappings=applied,
        preserved_styles=styles_ok,
        integrity_score=integrity,
        generation_confidence=generation_confidence,
        leftover_placeholders=leftovers,
        message=(
            f"Generated {dest.name} with {applied} replacement/row unit(s); "
            f"table_fills={len(mapping.table_fills)}; "
            f"integrity={integrity:.2f}, generation_confidence={generation_confidence:.2f}."
        ),
    )
