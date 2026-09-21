"""Mapper LLM prompts — loaded from YAML, run via LCEL."""

from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from document_processing_mcp.services.prompts.loader import (
    chat_prompt_from_yaml,
    load_prompt_yaml,
)

_MAPPER_YAML = "mapper.yml"


def get_mapper_system_prompt() -> str:
    return load_prompt_yaml(_MAPPER_YAML)["system"]


def get_mapper_human_template() -> str:
    return load_prompt_yaml(_MAPPER_YAML)["human"]


def build_mapper_prompt() -> ChatPromptTemplate:
    """Build ChatPromptTemplate from prompts/mapper.yml (or PROMPTS_DIR)."""
    return chat_prompt_from_yaml(_MAPPER_YAML)


def build_mapper_chain(llm: Any) -> Any:
    """LCEL: ChatPromptTemplate | structured mapper LLM."""
    return build_mapper_prompt() | llm
