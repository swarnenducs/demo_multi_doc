"""Extraction XML critic prompts — YAML + LCEL."""

from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from document_processing_mcp.services.prompts.loader import (
    chat_prompt_from_yaml,
    load_prompt_yaml,
)

_EXTRACTION_YAML = "extraction_validator.yml"


def get_extraction_validator_system_prompt() -> str:
    return load_prompt_yaml(_EXTRACTION_YAML)["system"]


def build_extraction_validator_prompt() -> ChatPromptTemplate:
    return chat_prompt_from_yaml(_EXTRACTION_YAML)


def build_extraction_validator_chain(llm: Any) -> Any:
    """LCEL: ChatPromptTemplate | structured extraction critic LLM."""
    return build_extraction_validator_prompt() | llm
