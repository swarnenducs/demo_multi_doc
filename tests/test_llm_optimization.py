"""Opt-in LLM optimisation: complexity scoring and retry cascade (no live LLMs)."""

from __future__ import annotations

from pathlib import Path

from document_processing_mcp.graph import _bump_retry
from document_processing_mcp.models.schemas import ContentBlock, ExtractedTemplate
from document_processing_mcp.nodes.pipeline import extract_styles_node
from document_processing_mcp.services.llm_optimization import (
    load_optimization_config,
    pick_mapper_model_id,
    pick_validator_model_id,
    resolve_optimized_flow,
    score_complexity,
)
from doc_sample_template import build_sample_template


def _template(*, placeholders: list[str], blocks: list[ContentBlock] | None = None) -> ExtractedTemplate:
    return ExtractedTemplate(
        template_path="t.docx",
        placeholders=placeholders,
        blocks=blocks or [],
    )


def test_bundled_config_stays_off_by_default() -> None:
    cfg = load_optimization_config()
    assert cfg.path.name == "llm_optimization.json"
    assert cfg.enabled_by_default is False
    assert cfg.models["mapper_easy"]
    assert cfg.models["mapper_hard"]
    assert cfg.models["mapper_retry"]
    assert cfg.models["mapper_final"]


def test_resolve_optimized_flow_request_wins() -> None:
    assert resolve_optimized_flow(True, env_enabled=False) is True
    assert resolve_optimized_flow(False, env_enabled=True) is False
    assert resolve_optimized_flow(None, env_enabled=True) is True
    assert resolve_optimized_flow(None, env_enabled=False) is False


def test_score_complexity_easy_vs_hard() -> None:
    easy, signals = score_complexity(
        _template(placeholders=["invoice_number", "customer.name"]),
        rules={
            "easy_max_placeholders": 12,
            "easy_max_tables": 1,
            "hard_if_percent_tokens": True,
            "hard_if_duplicate_placeholders": True,
            "hard_if_header_only_tables": True,
        },
    )
    assert easy == "easy"
    assert signals["placeholder_count"] == 2

    hard, hard_signals = score_complexity(
        _template(
            placeholders=["rate_XX%", "qty", "total"],
            blocks=[
                ContentBlock(
                    block_id="t0r0c0",
                    block_type="table_cell",
                    text="Header",
                    table_index=0,
                    row_index=0,
                    cell_index=0,
                )
            ],
        ),
        rules={
            "easy_max_placeholders": 12,
            "easy_max_tables": 1,
            "hard_if_percent_tokens": True,
            "hard_if_duplicate_placeholders": True,
            "hard_if_header_only_tables": True,
        },
    )
    assert hard == "hard"
    assert "percent_tokens" in hard_signals["reasons"]
    assert "header_only_tables" in hard_signals["reasons"]


def test_pick_mapper_upgrades_on_retry() -> None:
    cfg = load_optimization_config()
    easy = pick_mapper_model_id(cfg, "easy", 0)
    hard = pick_mapper_model_id(cfg, "hard", 0)
    retry = pick_mapper_model_id(cfg, "easy", 1)
    final = pick_mapper_model_id(cfg, "easy", 2)
    assert easy == cfg.models["mapper_easy"]
    assert hard == cfg.models["mapper_hard"]
    assert retry == cfg.models["mapper_retry"]
    assert final == cfg.models["mapper_final"]
    assert pick_validator_model_id(cfg, 0) == cfg.models["validator"]
    assert pick_validator_model_id(cfg, 1) == cfg.models["validator"]


def test_extract_styles_off_does_not_set_optimization_fields(tmp_path: Path) -> None:
    template = build_sample_template(tmp_path / "template.docx")
    result = extract_styles_node(
        {
            "template_path": str(template),
            "errors": [],
            "status": "data_loaded",
            "optimized_flow": False,
        }
    )
    assert result["status"] == "styles_extracted"
    assert "complexity" not in result or result.get("complexity") is None
    assert not result.get("mapper_model_id")


def test_extract_styles_on_picks_mapper_from_json(tmp_path: Path) -> None:
    template = build_sample_template(tmp_path / "template.docx")
    result = extract_styles_node(
        {
            "template_path": str(template),
            "errors": [],
            "status": "data_loaded",
            "optimized_flow": True,
        }
    )
    cfg = load_optimization_config()
    assert result["status"] == "styles_extracted"
    assert result["complexity"] in {"easy", "hard"}
    expected = (
        cfg.models["mapper_easy"]
        if result["complexity"] == "easy"
        else cfg.models["mapper_hard"]
    )
    assert result["mapper_model_id"] == expected
    assert result["max_retries"] == cfg.retries["max_retries"]


def test_bump_retry_upgrades_only_when_optimized() -> None:
    cfg = load_optimization_config()
    off = _bump_retry(
        {
            "optimized_flow": False,
            "retry_count": 0,
            "mapper_model_id": "openai:stay-put",
        }
    )
    assert off["retry_count"] == 1
    assert off["mapper_model_id"] == "openai:stay-put"

    on = _bump_retry(
        {
            "optimized_flow": True,
            "complexity": "easy",
            "retry_count": 0,
            "mapper_model_id": cfg.models["mapper_easy"],
        }
    )
    assert on["retry_count"] == 1
    assert on["mapper_model_id"] == cfg.models["mapper_retry"]
