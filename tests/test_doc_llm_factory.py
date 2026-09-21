"""Tests for injectable multi-provider LLM factory (init_chat_model)."""

from __future__ import annotations

from document_processing_mcp.services.llm_factory import (
    agent_config,
    config_model_id,
    list_llm_providers,
    mapper_config,
    register_llm_provider,
    unregister_llm_provider,
    validator_config,
)


def _clear_model_ids(monkeypatch) -> None:
    for key in (
        "MAPPER_MODEL_ID",
        "VALIDATOR_MODEL_ID",
        "AGENT_MODEL_ID",
    ):
        monkeypatch.delenv(key, raising=False)


def test_mapper_config_defaults_to_openai_gpt5(monkeypatch) -> None:
    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5")
    monkeypatch.delenv("MAPPER_MODEL", raising=False)
    cfg = mapper_config()
    assert cfg.role == "mapper"
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-5"
    assert cfg.label == "openai/gpt-5"
    assert config_model_id(cfg) == "openai:gpt-5"


def test_mapper_model_id_overrides_provider_model(monkeypatch) -> None:
    monkeypatch.setenv("MAPPER_MODEL_ID", "azure_openai:gpt-5-mini")
    monkeypatch.setenv("MAPPER_PROVIDER", "openai")  # should be ignored
    monkeypatch.setenv("MAPPER_MODEL", "ignored")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://contoso.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    cfg = mapper_config()
    assert cfg.provider == "azure_openai"
    assert cfg.model == "gpt-5-mini"
    assert config_model_id(cfg) == "azure_openai:gpt-5-mini"


def test_validator_model_id_with_slashed_model_name(monkeypatch) -> None:
    monkeypatch.setenv("VALIDATOR_MODEL_ID", "groq:openai/gpt-oss-120b")
    monkeypatch.setenv("VALIDATOR_PROVIDER", "openai")
    cfg = validator_config()
    assert cfg.provider == "groq"
    assert cfg.model == "openai/gpt-oss-120b"


def test_mapper_accepts_azure_openai(monkeypatch) -> None:
    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-deploy")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://contoso.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.delenv("MAPPER_MODEL", raising=False)
    cfg = mapper_config()
    assert cfg.provider == "azure_openai"
    assert cfg.model == "gpt-4o-deploy"
    assert cfg.base_url == "https://contoso.openai.azure.com"
    assert cfg.api_key == "azure-key"
    assert cfg.api_version  # default or env


def test_mapper_azure_alias(monkeypatch) -> None:
    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_PROVIDER", "azure")
    monkeypatch.setenv("MAPPER_MODEL", "my-deployment")
    cfg = mapper_config()
    assert cfg.provider == "azure_openai"
    assert cfg.model == "my-deployment"


def test_validator_config_defaults_to_groq(monkeypatch) -> None:
    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("VALIDATOR_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_VALIDATOR_MODEL", "openai/gpt-oss-120b")
    monkeypatch.delenv("VALIDATOR_MODEL", raising=False)
    cfg = validator_config()
    assert cfg.role == "validator"
    assert cfg.provider == "groq"
    assert cfg.model == "openai/gpt-oss-120b"
    assert cfg.label == "groq/openai/gpt-oss-120b"


def test_validator_can_use_openai(monkeypatch) -> None:
    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("VALIDATOR_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_VALIDATOR_MODEL", "gpt-5")
    monkeypatch.delenv("VALIDATOR_MODEL", raising=False)
    cfg = validator_config()
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-5"


def test_validator_can_use_azure(monkeypatch) -> None:
    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("VALIDATOR_PROVIDER", "azure")
    monkeypatch.setenv("VALIDATOR_MODEL", "critic-deploy")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://contoso.openai.azure.com")
    cfg = validator_config()
    assert cfg.provider == "azure_openai"
    assert cfg.model == "critic-deploy"


def test_role_scoped_overrides(monkeypatch) -> None:
    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MAPPER_MODEL", "llama3.1")
    monkeypatch.setenv("MAPPER_API_KEY", "local-key")
    monkeypatch.setenv("MAPPER_BASE_URL", "http://127.0.0.1:11434/v1")
    cfg = mapper_config()
    assert cfg.provider == "openai_compatible"
    assert cfg.model == "llama3.1"
    assert cfg.api_key == "local-key"
    assert cfg.base_url == "http://127.0.0.1:11434/v1"


def test_is_mapper_available_openai(monkeypatch) -> None:
    from document_processing_mcp.services.llm_factory import is_mapper_available

    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_PROVIDER", "openai")
    monkeypatch.delenv("MAPPER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert is_mapper_available() is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert is_mapper_available() is True


def test_is_mapper_available_azure(monkeypatch) -> None:
    from document_processing_mcp.services.llm_factory import is_mapper_available

    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_PROVIDER", "azure_openai")
    monkeypatch.delenv("MAPPER_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("MAPPER_BASE_URL", raising=False)
    assert is_mapper_available() is False
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-real-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://contoso.openai.azure.com")
    assert is_mapper_available() is True


def test_azure_placeholder_endpoint_not_available(monkeypatch) -> None:
    from document_processing_mcp.services.llm_factory import is_mapper_available

    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-real-key")
    monkeypatch.setenv(
        "AZURE_OPENAI_ENDPOINT", "https://YOUR_RESOURCE.openai.azure.com/"
    )
    assert is_mapper_available() is False


def test_is_validator_available_groq(monkeypatch) -> None:
    from document_processing_mcp.services.llm_factory import is_validator_available

    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("VALIDATOR_PROVIDER", "groq")
    monkeypatch.delenv("VALIDATOR_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert is_validator_available() is False
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    assert is_validator_available() is True


def test_register_custom_provider(monkeypatch) -> None:
    from document_processing_mcp.services.llm_factory import (
        get_mapper_llm,
        is_mapper_available,
    )

    _clear_model_ids(monkeypatch)
    calls: list[str] = []

    class _FakeLLM:
        def with_structured_output(self, _schema):
            return self

    def builder(config, temperature=0):
        calls.append(f"{config.provider}:{config.model}:{temperature}")
        return _FakeLLM()

    register_llm_provider("fake_vendor", builder)
    try:
        monkeypatch.setenv("MAPPER_PROVIDER", "fake_vendor")
        monkeypatch.setenv("MAPPER_MODEL", "ultra-1")
        assert "fake_vendor" in list_llm_providers()
        assert is_mapper_available() is True
        llm, cfg = get_mapper_llm()
        assert cfg.provider == "fake_vendor"
        assert cfg.model == "ultra-1"
        assert isinstance(llm, _FakeLLM)
        assert calls == ["fake_vendor:ultra-1:0.0"]
    finally:
        unregister_llm_provider("fake_vendor")


def test_agent_follows_mapper_provider(monkeypatch) -> None:
    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_PROVIDER", "groq")
    monkeypatch.setenv("MAPPER_MODEL", "llama-3.3-70b")
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    cfg = agent_config()
    assert cfg.provider == "groq"
    assert cfg.model == "llama-3.3-70b"


def _inner_chat_model(llm):
    """Unwrap LangChain ``_ConfigurableModel`` to the default concrete chat model."""
    inner = getattr(llm, "_model", None)
    return inner() if callable(inner) else llm


def test_get_mapper_llm_uses_init_chat_model(monkeypatch) -> None:
    """Built-in providers are constructed through LangChain init_chat_model."""
    from langchain_openai import ChatOpenAI

    from document_processing_mcp.services.llm_factory import get_mapper_llm

    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_PROVIDER", "openai")
    monkeypatch.setenv("MAPPER_MODEL", "gpt-4o")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm, cfg = get_mapper_llm()
    assert cfg.provider == "openai"
    assert llm._config_prefix == "mapper_"
    assert llm._configurable_fields == "any"
    assert isinstance(_inner_chat_model(llm), ChatOpenAI)


def test_get_mapper_llm_azure_classic_via_init(monkeypatch) -> None:
    from langchain_openai import AzureChatOpenAI

    from document_processing_mcp.services.llm_factory import get_mapper_llm

    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_PROVIDER", "azure_openai")
    monkeypatch.setenv("MAPPER_MODEL", "gpt-4o")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://contoso.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    llm, cfg = get_mapper_llm()
    assert cfg.provider == "azure_openai"
    assert isinstance(_inner_chat_model(llm), AzureChatOpenAI)


def test_get_mapper_llm_foundry_uses_openai_compatible_init(monkeypatch) -> None:
    from langchain_openai import ChatOpenAI

    from document_processing_mcp.services.llm_factory import get_mapper_llm

    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_PROVIDER", "azure_openai")
    monkeypatch.setenv("MAPPER_MODEL", "gpt-5-mini")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv(
        "AZURE_OPENAI_ENDPOINT",
        "https://demo.services.ai.azure.com/openai/v1/",
    )
    llm, cfg = get_mapper_llm()
    assert cfg.provider == "azure_openai"
    assert isinstance(_inner_chat_model(llm), ChatOpenAI)


def test_openai_model_id_uses_foundry_v1_as_init_chat_model(monkeypatch) -> None:
    from langchain_openai import ChatOpenAI

    from document_processing_mcp.services.llm_factory import get_mapper_llm, mapper_config

    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_MODEL_ID", "openai:gpt-5-mini")
    monkeypatch.delenv("MAPPER_PROVIDER", raising=False)
    monkeypatch.delenv("MAPPER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv(
        "AZURE_OPENAI_ENDPOINT",
        "https://demo.services.ai.azure.com/openai/v1/responses",
    )
    cfg = mapper_config()
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-5-mini"
    assert config_model_id(cfg) == "openai:gpt-5-mini"
    assert cfg.base_url == "https://demo.services.ai.azure.com/openai/v1"
    assert cfg.api_key == "azure-key"
    llm, _ = get_mapper_llm()
    assert isinstance(_inner_chat_model(llm), ChatOpenAI)


def test_normalize_azure_strips_responses_path() -> None:
    from document_processing_mcp.services.llm_factory import _normalize_azure_endpoint

    style, base = _normalize_azure_endpoint(
        "https://demo.services.ai.azure.com/openai/v1/responses"
    )
    assert style == "foundry_v1"
    assert base == "https://demo.services.ai.azure.com/openai/v1"


def test_llm_as_judge_is_configurable_init_chat_model(monkeypatch) -> None:
    from langchain_openai import ChatOpenAI

    from document_processing_mcp.services.llm_factory import LLM_AS_judge, LLMAsJudge

    monkeypatch.setenv("VALIDATOR_MODEL_ID", "openai:gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    judge = LLMAsJudge()
    assert LLM_AS_judge is LLMAsJudge
    assert judge.role == "validator"
    assert judge.config_prefix == "judge"
    assert judge.config.model == "gpt-4.1-mini"
    assert judge.model._config_prefix == "judge_"
    assert judge.model._configurable_fields == "any"
    assert isinstance(_inner_chat_model(judge.model), ChatOpenAI)


def test_mapper_llm_class_uses_mapper_config_prefix(monkeypatch) -> None:
    from document_processing_mcp.services.llm_factory import MapperLLM

    _clear_model_ids(monkeypatch)
    monkeypatch.setenv("MAPPER_MODEL_ID", "openai:gpt-5-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mapper = MapperLLM()
    assert mapper.role == "mapper"
    assert mapper.config_prefix == "mapper"
    assert mapper.model._config_prefix == "mapper_"
    assert mapper.model._configurable_fields == "any"
