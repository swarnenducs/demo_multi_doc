"""Stamp LLM-proposed <snake_case> markers onto a copy of the uploaded .docx.

Does not ask the LLM to emit Word XML. Only replaces sample strings and optionally
inserts one table after a known paragraph block.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

from document_processing_mcp.services.document_generator import (
    _paragraph_full_text,
    _qn,
    _set_paragraph_text_preserving_style,
)
from document_processing_mcp.services.placeholders import find_placeholders

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
_SNAKE = re.compile(r"[^a-z0-9]+")


def normalize_marker_token(raw: str) -> str:
    text = (raw or "").strip()
    text = text.strip("<>").strip()
    text = _SNAKE.sub("_", text.lower()).strip("_")
    return f"<{text}>" if text else ""


def _replacements_from_plan(plan: Any) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for row in getattr(plan, "markers", None) or []:
        sample = str(getattr(row, "sample_value", "") or "").strip()
        marker = normalize_marker_token(str(getattr(row, "marker", "") or ""))
        confidence = str(getattr(row, "confidence", "") or "").upper()
        if not sample or not marker or len(sample) < 2:
            continue
        if confidence == "LOW":
            continue
        items.append((sample, marker))
    items.sort(key=lambda pair: len(pair[0]), reverse=True)
    return items


def _insert_simple_table(
    body: etree._Element,
    after_paragraph: etree._Element,
    columns: list[str],
    marker_row: list[str],
) -> None:
    if not columns:
        return
    tbl = etree.Element(_qn("tbl"))
    tbl_pr = etree.SubElement(tbl, _qn("tblPr"))
    tbl_w = etree.SubElement(tbl_pr, _qn("tblW"))
    tbl_w.set(_qn("w"), "5000")
    tbl_w.set(_qn("type"), "pct")
    borders = etree.SubElement(tbl_pr, _qn("tblBorders"))
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = etree.SubElement(borders, _qn(edge))
        node.set(_qn("val"), "single")
        node.set(_qn("sz"), "4")
        node.set(_qn("space"), "0")
        node.set(_qn("color"), "auto")
    grid = etree.SubElement(tbl, _qn("tblGrid"))
    for _ in columns:
        etree.SubElement(grid, _qn("gridCol")).set(_qn("w"), "1200")

    def add_row(values: list[str], *, header: bool) -> None:
        tr = etree.SubElement(tbl, _qn("tr"))
        for value in values:
            tc = etree.SubElement(tr, _qn("tc"))
            p = etree.SubElement(tc, _qn("p"))
            r = etree.SubElement(p, _qn("r"))
            if header:
                rpr = etree.SubElement(r, _qn("rPr"))
                etree.SubElement(rpr, _qn("b"))
            t = etree.SubElement(r, _qn("t"))
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            t.text = value

    add_row(columns, header=True)
    add_row(marker_row or [normalize_marker_token(c) for c in columns], header=False)
    after_paragraph.addnext(tbl)


def apply_marker_plan(
    source_docx: str | Path,
    dest_docx: str | Path,
    plan: Any,
    *,
    block_id_to_index: dict[str, int] | None = None,
) -> dict[str, Any]:
    src = Path(source_docx)
    dest = Path(dest_docx)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)

    replacements = _replacements_from_plan(plan)
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)

    tmp = dest.with_suffix(dest.suffix + ".tmp")
    replaced = 0
    with zipfile.ZipFile(dest, "r") as zin, zipfile.ZipFile(
        tmp, "w", compression=zipfile.ZIP_DEFLATED
    ) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                root = etree.fromstring(data, parser)
                body = root.find("w:body", NS)
                if body is not None:
                    body_paragraphs = [
                        child for child in list(body) if child.tag == _qn("p")
                    ]
                    for paragraph in body.iter(_qn("p")):
                        original = _paragraph_full_text(paragraph)
                        if not original:
                            continue
                        updated = original
                        for sample, marker in replacements:
                            if sample in updated:
                                updated = updated.replace(sample, marker)
                                replaced += 1
                        if updated != original:
                            _set_paragraph_text_preserving_style(paragraph, updated)

                    tables = getattr(plan, "tables", None) or []
                    for table in tables:
                        if not getattr(table, "required", False):
                            continue
                        if getattr(table, "use_existing_table", False):
                            continue
                        columns = [str(c) for c in (getattr(table, "columns", None) or []) if str(c).strip()]
                        markers = [
                            normalize_marker_token(str(c))
                            for c in (getattr(table, "marker_row", None) or columns)
                        ]
                        after_id = str(getattr(table, "after_block_id", "") or "").strip()
                        idx = (block_id_to_index or {}).get(after_id)
                        if idx is None or idx < 0 or idx >= len(body_paragraphs):
                            continue
                        _insert_simple_table(body, body_paragraphs[idx], columns, markers)
                        break

                data = etree.tostring(
                    root,
                    encoding="UTF-8",
                    xml_declaration=True,
                    standalone=True,
                )
            info = zipfile.ZipInfo(filename=item.filename, date_time=item.date_time)
            info.compress_type = item.compress_type
            zout.writestr(info, data)
    tmp.replace(dest)

    from document_processing_mcp.services.placeholders import template_has_markers

    has, keys = template_has_markers(template_path=str(dest))
    return {
        "path": str(dest),
        "replacements_applied": replaced,
        "markers_present": has,
        "placeholder_keys": keys,
    }
