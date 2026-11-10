"""Generate a Word document from LLM mapping plans while preserving Word XML styles."""

from __future__ import annotations

import copy
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

from models.schemas import (
    ExtractedTemplate,
    GenerationResult,
    MappingResult,
    TableFillPlan,
)
from services.placeholders import PLACEHOLDER_PATTERNS
from services.table_fill_infer import (
    MAX_TIER_TABLES,
    humanize_group_label,
    normalize_header,
    resolve_row_groups,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}

# JSON fields that should render with a currency symbol in table cells.
_PRICE_FIELD_TOKENS = {
    "eaprice",
    "ea_price",
    "unitprice",
    "unit_price",
    "price",
    "marketprice",
    "market_price",
    "uomprice",
    "priceuom",
    "priceea",
    "priceeach",
}


def _qn(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _currency_symbol(row_data: dict[str, Any]) -> str:
    """Map row ``currency`` (``$`` / ``USD``) to a display symbol; default ``$``."""
    raw = row_data.get("currency")
    if raw is None or str(raw).strip() == "":
        raw = row_data.get("Currency")
    text = str(raw or "").strip()
    if not text:
        return "$"
    upper = text.upper()
    if upper in {"USD", "US$", "DOLLAR", "DOLLARS"}:
        return "$"
    if text in {"$", "€", "£", "¥"}:
        return text
    return text


def _format_table_cell_value(field: str | None, row_data: dict[str, Any]) -> str:
    """Format a table cell; price fields become ``$1.25`` using row currency."""
    if field is None:
        return ""
    if field not in row_data:
        return ""
    value = row_data.get(field)
    if value is None or value == "":
        return ""
    text = str(value).strip()
    token = normalize_header(field)
    is_price = token in _PRICE_FIELD_TOKENS or (
        token.endswith("price") and token not in {"productcode", "productdescription"}
    )
    if not is_price:
        return text
    if text.startswith(("$", "€", "£", "¥")):
        return text
    return f"{_currency_symbol(row_data)}{text}"


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


def _ensure_repeating_header_row(header_row: etree._Element) -> None:
    """Mark the first table row so Word repeats column headers on each new page."""
    tr_pr = header_row.find("w:trPr", NS)
    if tr_pr is None:
        tr_pr = etree.Element(_qn("trPr"))
        header_row.insert(0, tr_pr)
    if tr_pr.find("w:tblHeader", NS) is None:
        tr_pr.append(etree.Element(_qn("tblHeader")))


def _row_props_for_data_row(header_row: etree._Element) -> etree._Element | None:
    """Clone header row props for a data row, without the repeating-header flag."""
    tr_pr = header_row.find("w:trPr", NS)
    if tr_pr is None:
        return None
    cloned = copy.deepcopy(tr_pr)
    for node in cloned.findall("w:tblHeader", NS):
        cloned.remove(node)
    return cloned if len(cloned) else None


def _fill_table_rows(
    table: etree._Element,
    field_per_col: list[str | None],
    rows_data: list[dict[str, Any]],
) -> int:
    """Replace body rows of ``table`` with one row per item in ``rows_data``."""
    trs = table.findall("w:tr", NS)
    if not trs:
        return 0
    header_row = trs[0]
    header_cells = header_row.findall("w:tc", NS)
    # When a long tier table continues onto the next page, repeat PRODUCT CODE /
    # DESCRIPTION / ... so readers still know which column is which.
    _ensure_repeating_header_row(header_row)

    # Prefer an existing body row as the visual template when present;
    # otherwise clone header style but strip bold for data cells.
    if len(trs) > 1:
        style_cells = trs[1].findall("w:tc", NS)
    else:
        style_cells = header_cells

    for old in trs[1:]:
        table.remove(old)

    inserted = 0
    for row_data in rows_data:
        new_tr = etree.Element(_qn("tr"))
        data_tr_pr = _row_props_for_data_row(header_row)
        if data_tr_pr is not None:
            new_tr.append(data_tr_pr)
        for idx, field in enumerate(field_per_col):
            value = _format_table_cell_value(field, row_data)
            tpl_cell = style_cells[idx] if idx < len(style_cells) else (
                header_cells[idx] if idx < len(header_cells) else None
            )
            # Data rows: same font/size/color as template, never bold
            new_tr.append(_make_simple_cell(value, tpl_cell, strip_bold=True))
        table.append(new_tr)
        inserted += 1
    return inserted


def _reference_paragraph(table: etree._Element) -> etree._Element | None:
    """Nearest preceding body paragraph with text (used to style group headings)."""
    node = table.getprevious()
    while node is not None:
        if node.tag == _qn("p") and _paragraph_full_text(node).strip():
            return node
        node = node.getprevious()
    return None


def _make_group_heading(label: str, reference: etree._Element | None) -> etree._Element:
    """Plain heading paragraph (e.g. "Tire 1") styled like the text above the table."""
    p = etree.Element(_qn("p"))

    ppr = None
    if reference is not None:
        ref_ppr = reference.find("w:pPr", NS)
        if ref_ppr is not None:
            ppr = copy.deepcopy(ref_ppr)
            # Never copy list numbering or a section break onto the heading.
            for tag in ("numPr", "sectPr", "keepNext"):
                for node in ppr.findall(f"w:{tag}", NS):
                    ppr.remove(node)
    if ppr is None:
        ppr = etree.Element(_qn("pPr"))
    # keepNext keeps the heading on the same page as its table.
    pos = 1 if len(ppr) and ppr[0].tag == _qn("pStyle") else 0
    ppr.insert(pos, etree.Element(_qn("keepNext")))
    p.append(ppr)

    run = etree.SubElement(p, _qn("r"))
    rpr = None
    if reference is not None:
        ref_run = reference.find("w:r", NS)
        ref_rpr = ref_run.find("w:rPr", NS) if ref_run is not None else None
        if ref_rpr is not None:
            rpr = copy.deepcopy(ref_rpr)
            for tag in ("b", "bCs", "u"):
                for node in rpr.findall(f"w:{tag}", NS):
                    rpr.remove(node)
    if rpr is not None:
        run.append(rpr)

    text_el = etree.SubElement(run, _qn("t"))
    text_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_el.text = label
    return p


def _expand_tables_from_plans(
    root: etree._Element,
    json_data: dict[str, Any] | None,
    plans: list[TableFillPlan],
    warnings: list[str] | None = None,
) -> int:
    """
    Fill tables using LLM TableFillPlan — column mapping comes from the LLM, not hardcoded aliases.

    When ``array_json_path`` points to an object of arrays (e.g. ``products`` =
    ``{"Tire1": [...], "Tire2": [...], ...}``):

    - only one tier has rows -> the template table is filled as is, no heading
    - 2..MAX_TIER_TABLES tiers -> the template table is repeated once per tier,
      each preceded by a heading ("Tire 1", "Tire 2", ...)
    - more tiers than MAX_TIER_TABLES -> extras are skipped and reported in ``warnings``
    """
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
        groups = resolve_row_groups(json_data, plan.array_json_path)
        if not groups or not plan.columns:
            continue

        trs = table.findall("w:tr", NS)
        if not trs:
            continue
        header_cells = trs[0].findall("w:tc", NS)
        headers = [_cell_text(c) for c in header_cells]

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

        if len(groups) > MAX_TIER_TABLES:
            skipped = ", ".join(str(key) for key, _ in groups[MAX_TIER_TABLES:])
            if warnings is not None:
                warnings.append(
                    f"{plan.array_json_path}: {skipped} skipped (max {MAX_TIER_TABLES} tier tables)"
                )
            groups = groups[:MAX_TIER_TABLES]
        # A single tier renders exactly like the template: one table, no heading.
        show_headings = len(groups) > 1

        # Untouched copy of the template table, cloned for every extra group.
        pristine = copy.deepcopy(table)
        reference = _reference_paragraph(table)
        anchor = table
        for group_idx, (group_key, rows_data) in enumerate(groups):
            if group_idx == 0:
                target = table
                if show_headings and group_key is not None:
                    table.addprevious(
                        _make_group_heading(humanize_group_label(group_key), reference)
                    )
            else:
                target = copy.deepcopy(pristine)
                # Paragraphs between tables also stop Word from merging them.
                spacer = etree.Element(_qn("p"))
                anchor.addnext(spacer)
                previous = spacer
                if show_headings and group_key is not None:
                    heading = _make_group_heading(humanize_group_label(group_key), reference)
                    spacer.addnext(heading)
                    previous = heading
                previous.addnext(target)
            inserted += _fill_table_rows(target, field_per_col, rows_data)
            anchor = target

    return inserted


def _apply_replacements_to_document_xml(
    document_xml: str,
    replacements: dict[str, str],
    json_data: dict[str, Any] | None = None,
    table_fills: list[TableFillPlan] | None = None,
    warnings: list[str] | None = None,
) -> tuple[str, int]:
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    root = etree.fromstring(document_xml.encode("utf-8"), parser)
    body = root.find("w:body", NS)
    if body is None:
        return document_xml, 0

    rows_added = _expand_tables_from_plans(root, json_data, table_fills or [], warnings)
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

    table_warnings: list[str] = []
    new_document_xml, applied = _apply_replacements_to_document_xml(
        template.document_xml,
        replacements,
        json_data=json_data,
        table_fills=mapping.table_fills,
        warnings=table_warnings,
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
            + (f" Warnings: {'; '.join(table_warnings)}." if table_warnings else "")
        ),
    )
