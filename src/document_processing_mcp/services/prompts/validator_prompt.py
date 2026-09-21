"""Validator LLM prompts — loaded from YAML, run via LCEL."""

from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from document_processing_mcp.services.prompts.loader import (
    chat_prompt_from_yaml,
    load_prompt_yaml,
)

_VALIDATOR_YAML = "validator.yml"


def get_validator_system_prompt() -> str:
    return load_prompt_yaml(_VALIDATOR_YAML)["system"]


def get_validator_human_template() -> str:
    return load_prompt_yaml(_VALIDATOR_YAML)["human"]


def build_validator_prompt() -> ChatPromptTemplate:
    """Build ChatPromptTemplate from prompts/validator.yml (or PROMPTS_DIR)."""
    return chat_prompt_from_yaml(_VALIDATOR_YAML)


def build_validator_chain(llm: Any) -> Any:
    """LCEL: ChatPromptTemplate | structured validator LLM."""
    return build_validator_prompt() | llm
