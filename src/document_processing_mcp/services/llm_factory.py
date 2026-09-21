"""Central factory for injectable LLMs — all chat models via LangChain ``init_chat_model``.

Roles
-----
- **mapper** (LLM #1): JSON → Word field / table mapping
- **validator** (LLM #2): independent critic
- **agent**: optional tool-calling orchestrator (defaults to mapper provider)

Switch any provider / model
---------------------------
Preferred (LangChain ``init_chat_model("provider:model")``)::

    MAPPER_MODEL_ID=openai:gpt-5-mini
    VALIDATOR_MODEL_ID=openai:gpt-4.1-mini

    # Same as:
    #   from langchain.chat_models import init_chat_model
    #   llm = init_chat_model(
    #       "openai:gpt-5-mini",
    #       configurable_fields="any",
    #       config_prefix="mapper",  # validator uses config_prefix="judge"
    #       temperature=0,
    #   )
    # Classes: MapperLLM(), LLMAsJudge(), AgentLLM()
    # Any init_chat_model provider works, e.g.:
    # MAPPER_MODEL_ID=anthropic:claude-sonnet-4-20250514
    # MAPPER_MODEL_ID=openai:gpt-4o
    # MAPPER_MODEL_ID=google_genai:gemini-2.0-flash

Or split vars still supported::

    MAPPER_PROVIDER=azure_openai
    MAPPER_MODEL=gpt-5-mini

Runtime override (code / LangGraph configurable)::

    get_mapper_llm(model_id="openai:gpt-4o-mini")
    init_role_chat_model("validator", model_id="anthropic:claude-sonnet-4-20250514")

    # LangGraph invoke:
    graph.invoke(state, config={"configurable": {
        "mapper_model_id": "openai:gpt-4o-mini",
        "validator_model_id": "groq:openai/gpt-oss-120b",
    }})

Custom / exotic SDKs::

    register_llm_provider("my_vendor", build_fn)
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

T = TypeVar("T")

# builder(config, temperature) -> LangChain chat model
LLMProviderBuilder = Callable[["LLMRoleConfig", float], Any]

_PROVIDER_REGISTRY: dict[str, LLMProviderBuilder] = {}

# Optional per-role runtime overrides (provider:model) — set by LangGraph config / callers
_mapper_model_id_override: ContextVar[str | None] = ContextVar("mapper_model_id", default=None)
_validator_model_id_override: ContextVar[str | None] = ContextVar(
    "validator_model_id", default=None
)
_agent_model_id_override: ContextVar[str | None] = ContextVar("agent_model_id", default=None)


@dataclass(frozen=True)
class LLMRoleConfig:
    """Resolved provider/model settings for one LLM role."""

    role: str  # mapper | validator | agent
    provider: str  # openai | azure_openai | groq | anthropic | … | custom
    model: str
    label: str  # human-readable, e.g. "azure_openai/gpt-4o"
    api_key: str | None = None
    base_url: str | None = None
    api_version: str | None = None
    temperature: float = 0.0
    extra: dict[str, str] | None = None


def register_llm_provider(name: str, builder: LLMProviderBuilder) -> None:
    """Register or replace a provider builder (injection point for other SDKs)."""
    key = name.strip().lower()
    if not key:
        raise ValueError("Provider name must be non-empty")
    _PROVIDER_REGISTRY[key] = builder


def unregister_llm_provider(name: str) -> None:
    """Remove a previously registered provider (mainly for tests)."""
    _PROVIDER_REGISTRY.pop(name.strip().lower(), None)


def list_llm_providers() -> list[str]:
    """Return documented + registered provider names (init_chat_model accepts more)."""
    built_in = {
        "openai",
        "azure_openai",
        "azure",
        "groq",
        "openai_compatible",
        "compatible",
        "anthropic",
        "google_genai",
        "google_vertexai",
        "mistralai",
        "fireworks",
        "together",
        "cohere",
        "bedrock",
        "huggingface",
        "ollama",
    }
    return sorted(built_in | set(_PROVIDER_REGISTRY))


def _env(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _is_placeholder_value(value: str | None) -> bool:
    """True for empty or template placeholders left in .env."""
    if not value or not value.strip():
        return True
    lowered = value.strip().lower()
    markers = (
        "your_resource",
        "your-resource",
        "your_key",
        "your-key",
        "paste_your",
        "changeme",
        "placeholder",
        "example.openai.azure.com",
        "<",
    )
    return any(m in lowered for m in markers)


def _role_prefix(role: str) -> str:
    return role.upper()  # MAPPER / VALIDATOR / AGENT


def _normalize_provider(provider: str) -> str:
    p = provider.strip().lower()
    aliases = {
        "azure": "azure_openai",
        "aoai": "azure_openai",
        "azure-openai": "azure_openai",
        "compatible": "openai_compatible",
        "openai-compatible": "openai_compatible",
        "google": "google_genai",
        "gemini": "google_genai",
        "vertex": "google_vertexai",
        "claude": "anthropic",
    }
    return aliases.get(p, p)


def _default_provider(role: str) -> str:
    if role == "validator":
        return "groq"
    return "openai"


def _default_model(role: str, provider: str) -> str:
    if provider == "groq":
        return "openai/gpt-oss-120b"
    if provider == "azure_openai":
        return _env("AZURE_OPENAI_DEPLOYMENT") or _env("OPENAI_MODEL") or "gpt-4o"
    if provider == "anthropic":
        return "claude-sonnet-4-20250514"
    if provider in {"google_genai", "google_vertexai"}:
        return "gemini-2.0-flash"
    return "gpt-5"


def _resolve_model(role: str, provider: str) -> str:
    prefix = _role_prefix(role)
    role_model = _env(f"{prefix}_MODEL")
    if role_model:
        return role_model

    if role == "mapper":
        if provider == "azure_openai":
            return (
                _env("AZURE_OPENAI_DEPLOYMENT")
                or _env("OPENAI_MODEL")
                or _default_model(role, provider)
            )
        if provider == "groq":
            return _env("GROQ_MODEL") or _env("OPENAI_MODEL") or _default_model(role, provider)
        return _env("OPENAI_MODEL") or _default_model(role, provider)

    if role == "validator":
        if provider == "groq":
            return (
                _env("GROQ_VALIDATOR_MODEL")
                or _env("GROQ_MODEL")
                or _default_model(role, provider)
            )
        if provider == "azure_openai":
            return (
                _env("AZURE_OPENAI_VALIDATOR_DEPLOYMENT")
                or _env("AZURE_OPENAI_DEPLOYMENT")
                or _env("OPENAI_VALIDATOR_MODEL")
                or _env("OPENAI_MODEL")
                or _default_model(role, provider)
            )
        return (
            _env("OPENAI_VALIDATOR_MODEL")
            or _env("OPENAI_MODEL")
            or _default_model(role, provider)
        )

    # agent — inherit mapper model resolution unless AGENT_MODEL is set
    if _env("AGENT_MODEL"):
        return _env("AGENT_MODEL")  # type: ignore[return-value]
    return _resolve_model("mapper", provider)


# Default env var names for init_chat_model providers (role-scoped KEY wins first).
_PROVIDER_API_KEY_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "azure_openai": ("AZURE_OPENAI_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "openai_compatible": ("OPENAI_API_KEY", "COMPATIBLE_API_KEY"),
    "ollama": ("OPENAI_API_KEY", "COMPATIBLE_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google_genai": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "google_vertexai": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "mistralai": ("MISTRAL_API_KEY",),
    "fireworks": ("FIREWORKS_API_KEY",),
    "together": ("TOGETHER_API_KEY",),
    "cohere": ("COHERE_API_KEY",),
}

_PROVIDER_BASE_URL_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_BASE_URL",),
    "azure_openai": ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_BASE_URL"),
    "openai_compatible": ("OPENAI_BASE_URL", "COMPATIBLE_BASE_URL"),
    "groq": ("GROQ_BASE_URL",),
    "ollama": ("OLLAMA_BASE_URL",),
}


def _first_env(*keys: str) -> str | None:
    for key in keys:
        value = _env(key)
        if value and not _is_placeholder_value(value):
            return value
    return None


def _foundry_v1_base() -> str | None:
    """Azure AI Foundry OpenAI-v1 base (``.../openai/v1``) when configured."""
    endpoint = _first_env("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_BASE_URL")
    if not endpoint:
        return None
    style, base = _normalize_azure_endpoint(endpoint)
    return base if style == "foundry_v1" else None


def _resolve_api_key(role: str, provider: str) -> str | None:
    prefix = _role_prefix(role)
    role_key = _env(f"{prefix}_API_KEY")
    if role_key:
        return role_key
    if role == "agent":
        mapper_key = _env("MAPPER_API_KEY")
        if mapper_key:
            return mapper_key
    key = _first_env(*_PROVIDER_API_KEY_ENV.get(provider, ()))
    if provider == "openai" and _foundry_v1_base():
        return _first_env("AZURE_OPENAI_API_KEY") or key
    return key


def _resolve_base_url(role: str, provider: str) -> str | None:
    prefix = _role_prefix(role)
    role_url = _env(f"{prefix}_BASE_URL")
    if not role_url and role == "agent":
        role_url = _env("MAPPER_BASE_URL")

    candidates: list[str | None] = []
    if role_url:
        candidates.append(role_url)
    candidates.extend(_env(k) for k in _PROVIDER_BASE_URL_ENV.get(provider, ()))
    if provider == "ollama":
        candidates.append("http://127.0.0.1:11434")

    for raw in candidates:
        if raw and not _is_placeholder_value(raw):
            return raw.rstrip("/")
    if provider == "openai":
        return _foundry_v1_base()
    return None


def _resolve_max_tokens(role: str) -> int | None:
    """Optional completion cap via ``{ROLE}_MAX_TOKENS`` or ``LLM_MAX_TOKENS``.

    Validator defaults to 1024 so TPM-limited hosts (e.g. Groq free tier) do not
    reserve huge completion budgets (HTTP 413).
    """
    raw = _env(f"{_role_prefix(role)}_MAX_TOKENS") or _env("LLM_MAX_TOKENS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            return None
    if role == "validator":
        return 1024
    return None


def _resolve_api_version(role: str, provider: str) -> str | None:
    if provider != "azure_openai":
        return _env(f"{_role_prefix(role)}_API_VERSION")
    return (
        _env(f"{_role_prefix(role)}_API_VERSION")
        or _env("AZURE_OPENAI_API_VERSION")
        or "2024-12-01-preview"
    )


def _resolve_temperature(role: str) -> float:
    raw = _env(f"{_role_prefix(role)}_TEMPERATURE") or _env("LLM_TEMPERATURE") or "0"
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _parse_model_id(model_id: str) -> tuple[str, str]:
    """Parse ``provider:model`` (model may contain ``/``, e.g. groq ids)."""
    raw = model_id.strip()
    if ":" not in raw:
        raise ValueError(
            f"MODEL_ID must look like 'provider:model', got {model_id!r}. "
            "Example: azure_openai:gpt-5-mini or groq:openai/gpt-oss-120b "
            "or anthropic:claude-sonnet-4-20250514"
        )
    provider_raw, _, model = raw.partition(":")
    provider = _normalize_provider(provider_raw)
    model = model.strip()
    if not provider or not model:
        raise ValueError(f"Invalid MODEL_ID {model_id!r}: empty provider or model")
    return provider, model


def _context_model_id(role: str) -> str | None:
    if role == "mapper":
        return _mapper_model_id_override.get()
    if role == "validator":
        return _validator_model_id_override.get()
    if role == "agent":
        return _agent_model_id_override.get()
    return None


def set_role_model_overrides(
    *,
    mapper_model_id: str | None = None,
    validator_model_id: str | None = None,
    agent_model_id: str | None = None,
) -> tuple[Token, Token, Token]:
    """Bind runtime ``provider:model`` overrides (e.g. from LangGraph configurable)."""
    return (
        _mapper_model_id_override.set(mapper_model_id),
        _validator_model_id_override.set(validator_model_id),
        _agent_model_id_override.set(agent_model_id),
    )


def reset_role_model_overrides(tokens: tuple[Token, Token, Token]) -> None:
    _mapper_model_id_override.reset(tokens[0])
    _validator_model_id_override.reset(tokens[1])
    _agent_model_id_override.reset(tokens[2])


def bind_model_overrides_from_config(config: dict[str, Any] | None) -> tuple[Token, Token, Token]:
    """Extract LangGraph ``configurable`` model ids and bind them."""
    configurable: dict[str, Any] = {}
    if isinstance(config, dict):
        configurable = config.get("configurable") or {}
        if not isinstance(configurable, dict):
            configurable = {}
    return set_role_model_overrides(
        mapper_model_id=configurable.get("mapper_model_id")
        or configurable.get("mapper_model"),
        validator_model_id=configurable.get("validator_model_id")
        or configurable.get("validator_model"),
        agent_model_id=configurable.get("agent_model_id")
        or configurable.get("agent_model"),
    )


def resolve_role_config(
    role: str,
    *,
    model_override: str | None = None,
    model_id: str | None = None,
) -> LLMRoleConfig:
    """Resolve env → typed config for a role (mapper | validator | agent).

    Precedence for provider/model:
    1. Explicit ``model_id`` arg (``provider:model``)
    2. ContextVar override (LangGraph configurable)
    3. ``{ROLE}_MODEL_ID`` env
    4. Split ``{ROLE}_PROVIDER`` / ``{ROLE}_MODEL`` (+ legacy fallbacks)
    """
    role = role.strip().lower()
    if role not in {"mapper", "validator", "agent"}:
        raise ValueError(f"Unknown LLM role: {role}")

    prefix = _role_prefix(role)

    effective_model_id = (
        (model_id or "").strip()
        or (_context_model_id(role) or "").strip()
        or _env(f"{prefix}_MODEL_ID")
        or (_env("MAPPER_MODEL_ID") if role == "agent" else None)
    )

    if effective_model_id:
        provider, model_from_id = _parse_model_id(effective_model_id)
        model = model_override or model_from_id
    else:
        if role == "agent":
            provider_raw = (
                _env("AGENT_PROVIDER") or _env("MAPPER_PROVIDER") or _default_provider("mapper")
            )
        else:
            provider_raw = _env(f"{prefix}_PROVIDER") or _default_provider(role)
        provider = _normalize_provider(provider_raw)
        model = model_override or _resolve_model(role, provider)

    api_key = _resolve_api_key(role, provider)
    base_url = _resolve_base_url(role, provider)
    api_version = _resolve_api_version(role, provider)
    temperature = _resolve_temperature(role)

    return LLMRoleConfig(
        role=role,
        provider=provider,
        model=model,
        label=f"{provider}/{model}",
        api_key=api_key,
        base_url=base_url,
        api_version=api_version,
        temperature=temperature,
    )


def mapper_config(*, model_id: str | None = None) -> LLMRoleConfig:
    return resolve_role_config("mapper", model_id=model_id)


def validator_config(*, model_id: str | None = None) -> LLMRoleConfig:
    return resolve_role_config("validator", model_id=model_id)


def agent_config(*, model_name: str | None = None, model_id: str | None = None) -> LLMRoleConfig:
    return resolve_role_config("agent", model_override=model_name, model_id=model_id)


def config_model_id(config: LLMRoleConfig) -> str:
    """Return LangChain-style ``provider:model`` id for this role config."""
    return f"{_normalize_provider(config.provider)}:{config.model}"


# ---------------------------------------------------------------------------
# Builders — ALL built-ins go through LangChain ``init_chat_model``
# ---------------------------------------------------------------------------


def _normalize_azure_endpoint(endpoint: str) -> tuple[str, str]:
    """Return (style, base_url).

    - foundry_v1: Azure AI Foundry / ``*.services.ai.azure.com/.../openai/v1``
      → OpenAI-compatible ``init_chat_model("openai:…")``
    - classic: ``*.openai.azure.com`` → ``azure_openai`` provider
    """
    url = endpoint.strip().rstrip("/")
    for suffix in ("/responses", "/chat/completions", "/completions"):
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")

    if "services.ai.azure.com" in url.lower() or "/openai/v1" in url.lower():
        if not url.lower().endswith("/openai/v1"):
            url = f"{url}/openai/v1"
        return "foundry_v1", url
    return "classic", url


def _init_chat_model(*args: Any, **kwargs: Any) -> Any:
    """Lazy import — every chat LLM in this project goes through this helper."""
    from langchain.chat_models import init_chat_model

    return init_chat_model(*args, **kwargs)


def _common_init_kwargs(config: LLMRoleConfig, temperature: float) -> dict[str, Any]:
    """Shared kwargs for any ``init_chat_model`` provider."""
    kwargs: dict[str, Any] = {"temperature": temperature}
    if config.api_key:
        kwargs["api_key"] = config.api_key
    if config.base_url:
        kwargs["base_url"] = config.base_url
    max_tokens = _resolve_max_tokens(config.role)
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return kwargs


def _resolve_init_target(
    config: LLMRoleConfig, temperature: float
) -> tuple[str, dict[str, Any]]:
    """Map role config → ``(provider:model, kwargs)`` for ``init_chat_model``.

    Most providers (openai, groq, anthropic, google_genai, …) pass through as-is.
    Only remaps that LangChain cannot express via ``provider:model`` alone:
    Azure Foundry v1 and generic OpenAI-compatible / Ollama endpoints.
    """
    provider = _normalize_provider(config.provider)
    kwargs = _common_init_kwargs(config, temperature)

    if provider == "azure_openai":
        endpoint = config.base_url or _env("AZURE_OPENAI_ENDPOINT")
        if not endpoint or _is_placeholder_value(endpoint):
            raise RuntimeError(
                "Azure OpenAI requires a real AZURE_OPENAI_ENDPOINT "
                "(or MAPPER_BASE_URL / VALIDATOR_BASE_URL)."
            )
        if not config.api_key:
            raise RuntimeError(
                f"{config.role} LLM (azure_openai) needs an API key. "
                f"Set {config.role.upper()}_API_KEY or AZURE_OPENAI_API_KEY."
            )
        style, base = _normalize_azure_endpoint(endpoint)
        if style == "foundry_v1":
            # Foundry OpenAI v1 speaks the OpenAI wire format.
            foundry_kwargs = {"api_key": config.api_key, "base_url": base}
            if temperature not in (None, 0, 0.0):
                foundry_kwargs["temperature"] = temperature
            max_tokens = kwargs.get("max_tokens")
            if max_tokens is not None:
                foundry_kwargs["max_tokens"] = max_tokens
            return f"openai:{config.model}", foundry_kwargs

        return config_model_id(config), {
            "api_key": config.api_key,
            "azure_endpoint": base,
            "api_version": config.api_version or "2024-12-01-preview",
            "temperature": temperature,
            **({"max_tokens": kwargs["max_tokens"]} if "max_tokens" in kwargs else {}),
        }

    if provider in {"openai_compatible", "ollama"}:
        base = config.base_url or (
            "http://127.0.0.1:11434" if provider == "ollama" else None
        )
        if not base:
            raise RuntimeError(
                "openai_compatible / ollama requires a base URL "
                "(MAPPER_BASE_URL / OPENAI_BASE_URL / COMPATIBLE_BASE_URL / OLLAMA_BASE_URL)."
            )
        return f"openai:{config.model}", {
            **kwargs,
            "api_key": config.api_key
            or _first_env("OPENAI_API_KEY", "COMPATIBLE_API_KEY")
            or "EMPTY",
            "base_url": base,
        }

    # Generic path — openai, groq, anthropic, google_genai, mistralai, …
    return config_model_id(config), kwargs


def _config_prefix_for_role(role: str) -> str:
    """RunnableConfig key prefix: validator is LLM-as-judge → ``judge_``."""
    if role == "validator":
        return "judge"
    return role


def _build_via_init_chat_model(
    config: LLMRoleConfig,
    temperature: float = 0,
    *,
    configurable_fields: Literal["any"] | list[str] | tuple[str, ...] = "any",
    config_prefix: str | None = None,
) -> Any:
    """Construct a chat model using LangChain ``init_chat_model`` only.

    Equivalent to::

        from langchain.chat_models import init_chat_model
        llm = init_chat_model(
            "openai:gpt-5-mini",
            configurable_fields="any",
            config_prefix="judge",
            temperature=0,
        )
    """
    model_id, kwargs = _resolve_init_target(config, temperature)
    kwargs.setdefault("temperature", temperature)
    kwargs.setdefault("configurable_fields", configurable_fields)
    kwargs.setdefault("config_prefix", config_prefix or _config_prefix_for_role(config.role))
    try:
        return _init_chat_model(model_id, **kwargs)
    except Exception as exc:
        known = ", ".join(list_llm_providers())
        raise ValueError(
            f"Failed to init_chat_model({model_id!r}) for role={config.role}. "
            f"Install the matching langchain integration package if needed. "
            f"Known/documented providers: {known}. "
            f"Or use register_llm_provider(...). Underlying error: {exc}"
        ) from exc


def _build_llm(
    config: LLMRoleConfig,
    temperature: float | None = None,
    *,
    configurable_fields: Literal["any"] | list[str] | tuple[str, ...] = "any",
    config_prefix: str | None = None,
) -> Any:
    temp = config.temperature if temperature is None else temperature
    provider = _normalize_provider(config.provider)

    if provider in _PROVIDER_REGISTRY:
        return _PROVIDER_REGISTRY[provider](config, temp)

    return _build_via_init_chat_model(
        config,
        temp,
        configurable_fields=configurable_fields,
        config_prefix=config_prefix,
    )


def _with_optional_structured(llm: Any, structured_schema: type[T] | None) -> Any:
    if structured_schema is None:
        return llm
    return llm.with_structured_output(structured_schema)


class ConfigurableChatLLM:
    """Role-scoped wrapper around ``init_chat_model(..., configurable_fields='any')``.

    Runtime overrides (temperature, max_tokens, model, …) go through LangChain config::

        llm.invoke(messages, config={"configurable": {
            "judge_model": "openai:gpt-4.1-mini",
            "judge_temperature": 0,
        }})
    """

    def __init__(
        self,
        role: str,
        *,
        model_id: str | None = None,
        model_override: str | None = None,
        temperature: float | None = None,
        structured_schema: type[T] | None = None,
        config_prefix: str | None = None,
        configurable_fields: Literal["any"] | list[str] | tuple[str, ...] = "any",
    ) -> None:
        self.role = role.strip().lower()
        self.config = resolve_role_config(
            self.role, model_override=model_override, model_id=model_id
        )
        self.config_prefix = config_prefix or _config_prefix_for_role(self.role)
        self.model = _with_optional_structured(
            _build_llm(
                self.config,
                temperature,
                configurable_fields=configurable_fields,
                config_prefix=self.config_prefix,
            ),
            structured_schema,
        )

    def as_tuple(self) -> tuple[Any, LLMRoleConfig]:
        return self.model, self.config

    def invoke(self, input: Any, config: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.model.invoke(input, config=config, **kwargs)

    def with_structured_output(self, schema: type[T], **kwargs: Any) -> Any:
        return self.model.with_structured_output(schema, **kwargs)

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self.model.bind_tools(tools, **kwargs)


class MapperLLM(ConfigurableChatLLM):
    """LLM #1 — JSON → Word field / table mapping."""

    def __init__(self, *, model_id: str | None = None, **kwargs: Any) -> None:
        super().__init__("mapper", model_id=model_id, **kwargs)


class LLMAsJudge(ConfigurableChatLLM):
    """LLM #2 — independent critic (LLM-as-judge) for extraction and generated docs.

    Default::

        from langchain.chat_models import init_chat_model
        init_chat_model(
            "openai:gpt-4.1-mini",
            configurable_fields="any",
            config_prefix="judge",
            temperature=0,
        )
    """

    def __init__(self, *, model_id: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("config_prefix", "judge")
        super().__init__("validator", model_id=model_id, **kwargs)


LLM_AS_judge = LLMAsJudge


class AgentLLM(ConfigurableChatLLM):
    """Optional tool-calling orchestrator (defaults to mapper provider)."""

    def __init__(
        self,
        model_name: str | None = None,
        *,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__("agent", model_id=model_id, model_override=model_name, **kwargs)


def init_role_chat_model(
    role: str,
    *,
    model_id: str | None = None,
    model_override: str | None = None,
    temperature: float | None = None,
    structured_schema: type[T] | None = None,
) -> tuple[Any, LLMRoleConfig]:
    """Public entry: build any role's chat model via ``init_chat_model``."""
    return ConfigurableChatLLM(
        role,
        model_id=model_id,
        model_override=model_override,
        temperature=temperature,
        structured_schema=structured_schema,
    ).as_tuple()


def get_mapper_llm(
    *,
    model_id: str | None = None,
    structured_schema: type[T] | None = None,
) -> tuple[Any, LLMRoleConfig]:
    """LLM #1 — mapping. Switch with ``MAPPER_MODEL_ID`` or ``model_id='provider:model'``."""
    return MapperLLM(model_id=model_id, structured_schema=structured_schema).as_tuple()


def get_validator_llm(
    *,
    model_id: str | None = None,
    structured_schema: type[T] | None = None,
) -> tuple[Any, LLMRoleConfig]:
    """LLM #2 — critic. Prefer ``LLMAsJudge``; this is the tuple wrapper."""
    return LLMAsJudge(model_id=model_id, structured_schema=structured_schema).as_tuple()


def get_agent_llm(
    model_name: str | None = None,
    *,
    model_id: str | None = None,
) -> tuple[Any, LLMRoleConfig]:
    """Orchestrator agent LLM (defaults to mapper provider/settings)."""
    return AgentLLM(model_name, model_id=model_id).as_tuple()


def build_configurable_chat_model(
    *,
    default_model_id: str | None = None,
    temperature: float = 0,
    config_prefix: str = "",
    configurable_fields: Literal["any"] | list[str] | tuple[str, ...] = "any",
) -> Any:
    """Return an ``init_chat_model`` instance with ``configurable_fields='any'``.

    Switch at invoke time via LangGraph / Runnable config::

        model = build_configurable_chat_model(
            default_model_id="openai:gpt-5-mini",
            config_prefix="foo",
        )
        model.invoke("what's your name")
        model.invoke(
            "what's your name",
            config={"configurable": {
                "foo_model": "openai:gpt-4.1-mini",
                "foo_temperature": 0,
            }},
        )
    """
    kwargs: dict[str, Any] = {
        "temperature": temperature,
        "configurable_fields": configurable_fields,
        "config_prefix": config_prefix,
    }
    if default_model_id:
        return _init_chat_model(default_model_id, **kwargs)
    return _init_chat_model(**kwargs)


def provider_credentials_available(config: LLMRoleConfig) -> bool:
    """Whether enough credentials exist to construct this role's LLM."""
    provider = _normalize_provider(config.provider)

    if provider in _PROVIDER_REGISTRY:
        return True

    if provider == "azure_openai":
        key = config.api_key or _first_env(*_PROVIDER_API_KEY_ENV["azure_openai"])
        endpoint = config.base_url or _first_env(*_PROVIDER_BASE_URL_ENV["azure_openai"])
        return bool(key) and bool(endpoint)

    if provider == "openai":
        if config.api_key or _first_env("OPENAI_API_KEY"):
            return True
        return bool(_foundry_v1_base() and _first_env("AZURE_OPENAI_API_KEY"))

    if provider in {"openai_compatible", "ollama"}:
        return bool(
            config.base_url
            or _first_env(*_PROVIDER_BASE_URL_ENV.get(provider, ()))
            or provider == "ollama"
        )

    # Generic providers: require an API key when we know the env name(s).
    key_envs = _PROVIDER_API_KEY_ENV.get(provider)
    if key_envs:
        return bool(config.api_key or _first_env(*key_envs))

    # Unknown init_chat_model providers may use ADC / local auth.
    return True


def is_mapper_available() -> bool:
    return provider_credentials_available(resolve_role_config("mapper"))


def is_validator_available() -> bool:
    return provider_credentials_available(resolve_role_config("validator"))


def is_agent_available() -> bool:
    return provider_credentials_available(resolve_role_config("agent"))
