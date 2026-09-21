"""Extract Word XML styles and content structure from a .docx template."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from document_processing_mcp.models.schemas import (
    ContentBlock,
    ExtractedTemplate,
    ParagraphStyle,
    RunStyle,
    StyleDefinition,
)
from document_processing_mcp.services.placeholders import find_placeholders

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def _qn(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _twips_to_pt(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return int(value) / 20.0
    except ValueError:
        return None


def _half_points_to_pt(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return int(value) / 2.0
    except ValueError:
        return None


def _elem_to_xml(elem: ET.Element | None) -> str | None:
    if elem is None:
        return None
    return ET.tostring(elem, encoding="unicode")


def _parse_run_style(rpr: ET.Element | None) -> RunStyle | None:
    if rpr is None:
        return None

    bold = rpr.find("w:b", NS) is not None
    italic = rpr.find("w:i", NS) is not None
    underline_el = rpr.find("w:u", NS)
    underline = underline_el is not None and underline_el.get(_qn("val"), "single") != "none"

    font_el = rpr.find("w:rFonts", NS)
    font_name = None
    if font_el is not None:
        font_name = (
            font_el.get(_qn("ascii"))
            or font_el.get(_qn("hAnsi"))
            or font_el.get(_qn("cs"))
        )

    size_el = rpr.find("w:sz", NS)
    font_size_pt = _half_points_to_pt(size_el.get(_qn("val")) if size_el is not None else None)

    color_el = rpr.find("w:color", NS)
    color_hex = color_el.get(_qn("val")) if color_el is not None else None
    if color_hex and color_hex.lower() == "auto":
        color_hex = None

    highlight_el = rpr.find("w:highlight", NS)
    highlight = highlight_el.get(_qn("val")) if highlight_el is not None else None

    return RunStyle(
        bold=bold or None,
        italic=italic or None,
        underline=underline or None,
        font_name=font_name,
        font_size_pt=font_size_pt,
        color_hex=color_hex,
        highlight=highlight,
        raw_rpr_xml=_elem_to_xml(rpr),
    )


def _parse_paragraph_style(ppr: ET.Element | None) -> ParagraphStyle | None:
    if ppr is None:
        return None

    style_el = ppr.find("w:pStyle", NS)
    style_id = style_el.get(_qn("val")) if style_el is not None else None

    jc_el = ppr.find("w:jc", NS)
    alignment = jc_el.get(_qn("val")) if jc_el is not None else None

    spacing = ppr.find("w:spacing", NS)
    space_before = space_after = line_spacing = None
    if spacing is not None:
        space_before = _twips_to_pt(spacing.get(_qn("before")))
        space_after = _twips_to_pt(spacing.get(_qn("after")))
        line = spacing.get(_qn("line"))
        line_rule = spacing.get(_qn("lineRule"), "auto")
        if line is not None:
            try:
                # auto line spacing is in 240ths of a line
                line_spacing = int(line) / 240.0 if line_rule == "auto" else int(line) / 20.0
            except ValueError:
                line_spacing = None

    ind = ppr.find("w:ind", NS)
    indent_left = indent_right = None
    if ind is not None:
        indent_left = _twips_to_pt(ind.get(_qn("left")))
        indent_right = _twips_to_pt(ind.get(_qn("right")))

    return ParagraphStyle(
        style_id=style_id,
        alignment=alignment,
        space_before_pt=space_before,
        space_after_pt=space_after,
        line_spacing=line_spacing,
        indent_left_pt=indent_left,
        indent_right_pt=indent_right,
        raw_ppr_xml=_elem_to_xml(ppr),
    )


def _read_zip_text(zf: zipfile.ZipFile, name: str) -> str | None:
    try:
        return zf.read(name).decode("utf-8")
    except KeyError:
        return None


def _extract_style_definitions(styles_xml: str | None) -> list[StyleDefinition]:
    if not styles_xml:
        return []

    root = ET.fromstring(styles_xml)
    definitions: list[StyleDefinition] = []
    for style in root.findall("w:style", NS):
        style_id = style.get(_qn("styleId"))
        if not style_id:
            continue
        name_el = style.find("w:name", NS)
        based_on_el = style.find("w:basedOn", NS)
        definitions.append(
            StyleDefinition(
                style_id=style_id,
                name=name_el.get(_qn("val")) if name_el is not None else None,
                style_type=style.get(_qn("type")),
                based_on=based_on_el.get(_qn("val")) if based_on_el is not None else None,
                paragraph=_parse_paragraph_style(style.find("w:pPr", NS)),
                run=_parse_run_style(style.find("w:rPr", NS)),
                raw_xml=_elem_to_xml(style),
            )
        )
    return definitions


def _paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == _qn("t") and node.text:
            parts.append(node.text)
        elif node.tag == _qn("tab"):
            parts.append("\t")
        elif node.tag == _qn("br"):
            parts.append("\n")
    return "".join(parts)


def _run_styles_for_paragraph(paragraph: ET.Element) -> list[RunStyle]:
    styles: list[RunStyle] = []
    for run in paragraph.findall("w:r", NS):
        rpr = run.find("w:rPr", NS)
        parsed = _parse_run_style(rpr)
        if parsed is not None:
            styles.append(parsed)
    return styles


def _extract_blocks(document_xml: str) -> list[ContentBlock]:
    root = ET.fromstring(document_xml)
    body = root.find("w:body", NS)
    if body is None:
        return []

    blocks: list[ContentBlock] = []
    para_idx = 0

    for child in list(body):
        if child.tag == _qn("p"):
            text = _paragraph_text(child)
            blocks.append(
                ContentBlock(
                    block_id=f"p-{para_idx}",
                    block_type="paragraph",
                    text=text,
                    placeholder_keys=find_placeholders(text),
                    paragraph_style=_parse_paragraph_style(child.find("w:pPr", NS)),
                    run_styles=_run_styles_for_paragraph(child),
                    xpath_hint=f"//w:body/w:p[{para_idx + 1}]",
                )
            )
            para_idx += 1
        elif child.tag == _qn("tbl"):
            table_index = sum(1 for b in blocks if b.block_type == "table_cell" and b.table_index is not None)
            # count distinct tables already seen
            existing_tables = {b.table_index for b in blocks if b.table_index is not None}
            table_index = len(existing_tables)
            for row_i, row in enumerate(child.findall("w:tr", NS)):
                for cell_i, cell in enumerate(row.findall("w:tc", NS)):
                    for p_i, paragraph in enumerate(cell.findall("w:p", NS)):
                        text = _paragraph_text(paragraph)
                        blocks.append(
                            ContentBlock(
                                block_id=f"t{table_index}-r{row_i}-c{cell_i}-p{p_i}",
                                block_type="table_cell",
                                text=text,
                                placeholder_keys=find_placeholders(text),
                                paragraph_style=_parse_paragraph_style(paragraph.find("w:pPr", NS)),
                                run_styles=_run_styles_for_paragraph(paragraph),
                                table_index=table_index,
                                row_index=row_i,
                                cell_index=cell_i,
                                xpath_hint=(
                                    f"//w:body/w:tbl[{table_index + 1}]"
                                    f"/w:tr[{row_i + 1}]/w:tc[{cell_i + 1}]/w:p[{p_i + 1}]"
                                ),
                            )
                        )
    return blocks


def extract_word_styles(template_path: str | Path) -> ExtractedTemplate:
    """
    Step 1: Open a .docx (ZIP of XML parts) and extract:
    - named styles from word/styles.xml
    - paragraph/run style XML from document content
    - placeholder tokens found in the template text
    """
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    if path.suffix.lower() != ".docx":
        raise ValueError(f"Expected a .docx file, got: {path.suffix}")

    with zipfile.ZipFile(path, "r") as zf:
        styles_xml = _read_zip_text(zf, "word/styles.xml")
        document_xml = _read_zip_text(zf, "word/document.xml")
        numbering_xml = _read_zip_text(zf, "word/numbering.xml")

    if not document_xml:
        raise ValueError(f"word/document.xml missing in {path}")

    styles = _extract_style_definitions(styles_xml)
    blocks = _extract_blocks(document_xml)

    # Enrich paragraph style names from style definitions
    style_name_by_id = {s.style_id: s.name for s in styles}
    for block in blocks:
        if block.paragraph_style and block.paragraph_style.style_id:
            block.paragraph_style.style_name = style_name_by_id.get(
                block.paragraph_style.style_id
            )

    placeholders: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        for key in block.placeholder_keys:
            if key not in seen:
                seen.add(key)
                placeholders.append(key)

    return ExtractedTemplate(
        template_path=str(path.resolve()),
        styles=styles,
        blocks=blocks,
        placeholders=placeholders,
        styles_xml=styles_xml,
        document_xml=document_xml,
        numbering_xml=numbering_xml,
    )
