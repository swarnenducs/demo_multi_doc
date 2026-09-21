"""Optional document tool-calling agent — ChatPromptTemplate from YAML."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from document_processing_mcp.services.prompts.loader import chat_prompt_from_yaml

_AGENT_YAML = "agent.yml"


def build_agent_prompt() -> ChatPromptTemplate:
    """system + human ChatPromptTemplate from prompts/agent.yml."""
    return chat_prompt_from_yaml(_AGENT_YAML)


def format_agent_system_prompt(*, model_label: str) -> str:
    """Render the system message (create_agent still takes a system string)."""
    messages = build_agent_prompt().format_messages(
        model_label=model_label,
        input="(user message follows in the agent thread)",
    )
    return str(messages[0].content)
