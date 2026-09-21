"""Marker synthesizer prompts — YAML + LCEL (no-marker Word files)."""

from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from document_processing_mcp.services.prompts.loader import (
    chat_prompt_from_yaml,
    load_prompt_yaml,
)

_YAML = "marker_synthesizer.yml"


def get_marker_synthesizer_system_prompt() -> str:
    return load_prompt_yaml(_YAML)["system"]


def build_marker_synthesizer_prompt() -> ChatPromptTemplate:
    return chat_prompt_from_yaml(_YAML)


def build_marker_synthesizer_chain(llm: Any) -> Any:
    return build_marker_synthesizer_prompt() | llm
