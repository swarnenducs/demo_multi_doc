"""Grouped product tables: products = {"Tier1": [...], ..., "TierN": [...]}."""

from __future__ import annotations

from pathlib import Path

from docx import Document

from models.schemas import MappingResult, TableColumnMap, TableFillPlan
from services.document_generator import generate_styled_document
from services.style_extractor import extract_word_styles
from services.table_fill_infer import (
    humanize_group_label,
    infer_table_fills,
    merge_table_fills,
    resolve_row_groups,
)

HEADERS = ["PRODUCT CODE", "ABCTEC PRODUCT DESCRIPTION", "EA/UOM", "UOM", "PRICE/ EA", "PRICE/ UOM"]


def _price_table_docx(path: Path) -> Path:
    doc = Document()
    doc.add_paragraph("ABCTec may adjust the pricing on an annual basis.")
    table = doc.add_table(rows=2, cols=len(HEADERS))
    for i, h in enumerate(HEADERS):
        table.rows[0].cells[i].text = h
    doc.add_paragraph("Closing paragraph after the price table.")
    doc.save(path)
    return path


def _product(tier: int, n: int) -> dict:
    return {
        "productCode": f"T{tier}-{n:03d}",
        "productDescription": f"Tier {tier} item {n}",
        "eaUom": "EA",
        "uom": "BOX",
        "eaPrice": 1.0 + n,
        "marketPrice": 2.0 + n,
        "currency": "$",
    }


def _tiered_data(counts: dict[str, int]) -> dict:
    return {
        "agreement": {"gpo_name": "Acme"},
        "products": {
            key: [_product(i + 1, n) for n in range(1, count + 1)]
            for i, (key, count) in enumerate(counts.items())
        },
    }


def test_humanize_group_label() -> None:
    assert humanize_group_label("Tire1") == "Tire 1"
    assert humanize_group_label("Tier10") == "Tier 10"
    assert humanize_group_label("price_tier_2") == "Price tier 2"


def test_resolve_row_groups_list_and_object() -> None:
    assert resolve_row_groups({"products": [{"a": 1}]}, "products") == [(None, [{"a": 1}])]
    data = {"products": {"Tier1": [{"a": 1}], "Tier2": [], "Tier3": [{"a": 3}]}}
    groups = resolve_row_groups(data, "products")
    assert [key for key, _ in groups] == ["Tier1", "Tier3"]  # empty tiers skipped


def test_unrelated_arrays_are_not_a_group() -> None:
    single = {"reference_data": {"cot_definitions": [{"cot_code": "HSP"}]}}
    assert resolve_row_groups(single, "reference_data") == []
    mixed = {"ref": {"cots": [{"cot_code": "H"}], "regions": [{"region": "N", "zip": 1}]}}
    assert resolve_row_groups(mixed, "ref") == []


def test_infer_picks_whole_group_object(tmp_path: Path) -> None:
    extracted = extract_word_styles(_price_table_docx(tmp_path / "t.docx"))
    data = _tiered_data({"Tire1": 2, "Tire2": 2, "Tire3": 2})
    fills = infer_table_fills(extracted, data)
    assert len(fills) == 1
    assert fills[0].array_json_path == "products"


def test_merge_prefers_group_over_single_tier_llm_plan(tmp_path: Path) -> None:
    llm_plan = TableFillPlan(
        table_index=0,
        array_json_path="products.Tire1",
        columns=[TableColumnMap(header=h, json_field="productCode", confidence=0.9) for h in HEADERS],
    )
    inferred = TableFillPlan(table_index=0, array_json_path="products", columns=llm_plan.columns)
    merged = merge_table_fills(MappingResult(table_fills=[llm_plan]), [inferred])
    assert merged.table_fills[0].array_json_path == "products"


def _generate_with_result(tmp_path: Path, data: dict):
    extracted = extract_word_styles(_price_table_docx(tmp_path / "t.docx"))
    mapping = MappingResult(table_fills=infer_table_fills(extracted, data), mapping_confidence=1.0)
    out = tmp_path / "out.docx"
    result = generate_styled_document(extracted, mapping, out, json_data=data)
    return Document(str(out)), result


def _generate(tmp_path: Path, counts: dict[str, int]) -> Document:
    return _generate_with_result(tmp_path, _tiered_data(counts))[0]


def _headings(doc: Document) -> list[str]:
    return [p.text for p in doc.paragraphs if p.text.startswith("Tire ")]


def test_flat_list_renders_template_table_without_heading(tmp_path: Path) -> None:
    data = {"products": [_product(1, n) for n in range(1, 4)]}
    doc, _ = _generate_with_result(tmp_path, data)
    assert len(doc.tables) == 1
    assert len(doc.tables[0].rows) - 1 == 3
    assert _headings(doc) == []


def test_single_tier_renders_template_table_without_heading(tmp_path: Path) -> None:
    doc = _generate(tmp_path, {"Tire1": 4})
    assert len(doc.tables) == 1
    assert len(doc.tables[0].rows) - 1 == 4
    assert _headings(doc) == []


def test_one_filled_tier_among_empty_tiers_has_no_heading(tmp_path: Path) -> None:
    doc = _generate(tmp_path, {"Tire1": 3, "Tire2": 0, "Tire3": 0, "Tire4": 0, "Tire5": 0})
    assert len(doc.tables) == 1
    assert len(doc.tables[0].rows) - 1 == 3
    assert _headings(doc) == []


def test_two_tiers_render_two_tables_with_headings(tmp_path: Path) -> None:
    doc = _generate(tmp_path, {"Tire1": 2, "Tire2": 3})
    assert len(doc.tables) == 2
    assert [len(t.rows) - 1 for t in doc.tables] == [2, 3]
    assert _headings(doc) == ["Tire 1", "Tire 2"]
    # Each heading sits directly above its table.
    for table, label in zip(doc.tables, ["Tire 1", "Tire 2"]):
        prev = table._tbl.getprevious()
        assert "".join(t.text or "" for t in prev.iter() if t.tag.endswith("}t")) == label


def test_headings_are_not_bold(tmp_path: Path) -> None:
    doc = _generate(tmp_path, {"Tire1": 1, "Tire2": 1})
    for p in doc.paragraphs:
        if p.text.startswith("Tire "):
            assert all(not run.bold for run in p.runs)


def test_more_than_five_tiers_capped_with_warning(tmp_path: Path) -> None:
    counts = {f"Tire{i}": 1 for i in range(1, 8)}
    doc, result = _generate_with_result(tmp_path, _tiered_data(counts))
    assert len(doc.tables) == 5
    assert _headings(doc) == [f"Tire {i}" for i in range(1, 6)]
    assert "Tire6, Tire7 skipped" in (result.message or "")


def test_three_tiers_render_three_tables(tmp_path: Path) -> None:
    doc = _generate(tmp_path, {"Tire1": 2, "Tire2": 3, "Tire3": 1})
    assert len(doc.tables) == 3
    assert [len(t.rows) - 1 for t in doc.tables] == [2, 3, 1]
    assert doc.tables[1].rows[1].cells[0].text == "T2-001"
    texts = [p.text for p in doc.paragraphs]
    for label in ("Tire 1", "Tire 2", "Tire 3"):
        assert label in texts
    # Every table keeps the template header row.
    assert all(t.rows[0].cells[0].text == "PRODUCT CODE" for t in doc.tables)
    # Content after the table stays after the last tier table.
    assert texts[-1] == "Closing paragraph after the price table."


def test_five_tiers_with_empty_tier(tmp_path: Path) -> None:
    doc = _generate(tmp_path, {"Tire1": 2, "Tire2": 2, "Tire3": 0, "Tire4": 2, "Tire5": 4})
    assert len(doc.tables) == 4
    assert [len(t.rows) - 1 for t in doc.tables] == [2, 2, 2, 4]
    assert _headings(doc) == ["Tire 1", "Tire 2", "Tire 4", "Tire 5"]


def test_five_tiers_render_five_tables(tmp_path: Path) -> None:
    doc = _generate(tmp_path, {f"Tire{i}": i for i in range(1, 6)})
    assert len(doc.tables) == 5
    assert [len(t.rows) - 1 for t in doc.tables] == [1, 2, 3, 4, 5]
    assert _headings(doc) == [f"Tire {i}" for i in range(1, 6)]


def test_product_table_header_repeats_across_pages(tmp_path: Path) -> None:
    """Header row must carry w:tblHeader so Word reprints it on a page break."""
    doc = _generate(tmp_path, {"Tire1": 1, "Tire2": 1, "Tire5": 40})
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    for table in doc.tables:
        header = table.rows[0]._tr
        assert header.find("w:trPr/w:tblHeader", ns) is not None
        for row in table.rows[1:]:
            assert row._tr.find("w:trPr/w:tblHeader", ns) is None
