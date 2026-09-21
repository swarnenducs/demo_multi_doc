"""Optional agent mode: an LLM chooses which document tools to call."""

from __future__ import annotations

from typing import Any

from document_processing_mcp.services.prompts.agent_prompt import format_agent_system_prompt
from document_processing_mcp.tools import get_document_tools


def build_agent(model_name: str | None = None):
    """
    Build a tool-calling agent (orchestrator uses the agent/mapper provider).
    System text comes from ChatPromptTemplate (prompts/agent.yml).
    Requires credentials for AGENT_PROVIDER / MAPPER_PROVIDER. Returns a compiled LangGraph agent.
    """
    from langchain.agents import create_agent

    from document_processing_mcp.services.llm_factory import AgentLLM, is_agent_available

    if not is_agent_available():
        raise RuntimeError(
            "Agent mode needs credentials for AGENT_PROVIDER/MAPPER_PROVIDER "
            "(e.g. OPENAI_API_KEY, or AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT, or GROQ_API_KEY)."
        )

    llm, config = AgentLLM(model_name).as_tuple()
    tools = get_document_tools()
    system_prompt = format_agent_system_prompt(model_label=config.label)
    return create_agent(llm, tools, system_prompt=system_prompt)


def run_agent(user_message: str, model_name: str | None = None) -> Any:
    """Invoke the tool-calling agent with a natural-language instruction."""
    agent = build_agent(model_name=model_name)
    return agent.invoke({"messages": [{"role": "user", "content": user_message}]})
