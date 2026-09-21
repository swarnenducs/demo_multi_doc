"""YAML prompt loading for mapper / validator LCEL chains."""

from __future__ import annotations

from document_processing_mcp.services.prompts.loader import (
    chat_prompt_from_yaml,
    load_prompt_yaml,
    prompts_dir,
    resolve_prompt_path,
)


def test_prompts_dir_contains_mapper_and_validator() -> None:
    assert (prompts_dir() / "mapper.yml").is_file() or resolve_prompt_path("mapper.yml").is_file()
    assert resolve_prompt_path("validator.yml").is_file()


def test_load_mapper_yaml_has_system_and_human() -> None:
    payload = load_prompt_yaml("mapper.yml")
    assert payload["version"] == "1.0.0"
    assert payload["required_version"] == "1.0.0"
    assert "document field mapper" in payload["system"].lower()
    assert "{placeholders_json}" in payload["human"]
    assert "{data_json}" in payload["human"]


def test_mapper_chat_prompt_input_variables() -> None:
    prompt = chat_prompt_from_yaml("mapper.yml")
    for var in (
        "placeholders_json",
        "occurrences_json",
        "tables_json",
        "blocks_json",
        "data_json",
    ):
        assert var in prompt.input_variables


def test_validator_chat_prompt_input_variables() -> None:
    prompt = chat_prompt_from_yaml("validator.yml")
    for var in (
        "template_text",
        "generated_text",
        "key_snippets",
        "data_json",
        "mappings_json",
        "unmapped_placeholders_json",
        "leftovers_json",
    ):
        assert var in prompt.input_variables


def test_extraction_validator_yaml_loads() -> None:
    payload = load_prompt_yaml("extraction_validator.yml")
    assert "extraction" in payload["system"].lower()
    assert "{placeholders_json}" in payload["human"]
    prompt = chat_prompt_from_yaml("extraction_validator.yml")
    for var in ("template_path", "placeholders_json", "blocks_json", "tables_json"):
        assert var in prompt.input_variables


def test_marker_synthesizer_yaml_loads() -> None:
    payload = load_prompt_yaml("marker_synthesizer.yml")
    assert payload["version"] == "1.0.0"
    prompt = chat_prompt_from_yaml("marker_synthesizer.yml")
    for var in (
        "document_text",
        "blocks_json",
        "tables_json",
        "reference_name",
        "reference_placeholders_json",
        "reference_preview",
        "json_keys_json",
    ):
        assert var in prompt.input_variables


def test_agent_yaml_is_chat_prompt_template() -> None:
    from langchain_core.prompts import ChatPromptTemplate

    from document_processing_mcp.services.prompts import build_agent_prompt, format_agent_system_prompt

    prompt = build_agent_prompt()
    assert isinstance(prompt, ChatPromptTemplate)
    assert "model_label" in prompt.input_variables
    assert "input" in prompt.input_variables
    system = format_agent_system_prompt(model_label="openai/gpt-5-mini")
    assert "openai/gpt-5-mini" in system
    assert "map_json_to_template" in system
