"""Optional mapper/validator optimisation: complexity bucket + retry cascade.

Used only when ``optimized_flow`` is on. Off path never reads the JSON file.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from document_processing_mcp.models.schemas import ExtractedTemplate, GenerationResult

logger = logging.getLogger(__name__)

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _PACKAGE_ROOT / "config" / "llm_optimization.json"

_cache: tuple[str, float, "OptimizationConfig"] | None = None


def resolve_optimized_flow(
    request: bool | None,
    *,
    env_enabled: bool,
) -> bool:
    """Request wins; otherwise the env default. JSON cannot turn the feature on."""
    if request is True:
        return True
    if request is False:
        return False
    return bool(env_enabled)


def _clean_model_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none"}:
        return None
    return text


def _first_model(*ids: str | None) -> str | None:
    for model_id in ids:
        cleaned = _clean_model_id(model_id)
        if cleaned:
            return cleaned
    return None


@dataclass(frozen=True)
class OptimizationConfig:
    path: Path
    enabled_by_default: bool
    complexity: dict[str, Any]
    models: dict[str, str | None]
    retries: dict[str, Any]
    extras: dict[str, Any]


def default_config_path() -> Path:
    from document_processing_mcp.core.settings import settings

    return settings().document_llm_optimization_config


def reset_optimization_config_cache() -> None:
    global _cache
    _cache = None


def load_optimization_config(path: Path | str | None = None) -> OptimizationConfig:
    """Load and cache ``llm_optimization.json``."""
    global _cache
    resolved = Path(path).expanduser() if path else default_config_path()
    if not resolved.is_absolute():
        resolved = (_PACKAGE_ROOT / resolved).resolve()
    else:
        resolved = resolved.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"LLM optimisation config not found: {resolved}. "
            "Expected document-processing-mcp/config/llm_optimization.json"
        )
    mtime = resolved.stat().st_mtime
    if _cache is not None and _cache[0] == str(resolved) and _cache[1] == mtime:
        return _cache[2]

    raw = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"LLM optimisation config must be a JSON object: {resolved}")
    models_raw = raw.get("models") or {}
    if not isinstance(models_raw, dict):
        raise ValueError("llm_optimization.json 'models' must be an object")
    models = {str(key): _clean_model_id(value) for key, value in models_raw.items()}
    complexity = raw.get("complexity") if isinstance(raw.get("complexity"), dict) else {}
    retries = raw.get("retries") if isinstance(raw.get("retries"), dict) else {}
    extras = raw.get("extras") if isinstance(raw.get("extras"), dict) else {}
    cfg = OptimizationConfig(
        path=resolved,
        enabled_by_default=bool(raw.get("enabled_by_default")),
        complexity=complexity,
        models=models,
        retries=retries,
        extras=extras,
    )
    _cache = (str(resolved), mtime, cfg)
    return cfg


def score_complexity(
    extracted: ExtractedTemplate,
    *,
    rules: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return ``easy`` / ``hard`` plus the signals that produced it. No LLM."""
    cfg_rules = rules or {}
    easy_max_placeholders = int(cfg_rules.get("easy_max_placeholders", 12))
    easy_max_tables = int(cfg_rules.get("easy_max_tables", 1))
    placeholders = list(extracted.placeholders or [])
    placeholder_count = len(placeholders)
    table_indexes = sorted(
        {
            block.table_index
            for block in extracted.blocks
            if block.table_index is not None
        }
    )
    table_count = len(table_indexes)

    duplicate_placeholders = len(placeholders) != len(set(placeholders))
    if not duplicate_placeholders:
        seen: dict[str, int] = {}
        for block in extracted.blocks:
            for key in block.placeholder_keys or []:
                seen[key] = seen.get(key, 0) + 1
        duplicate_placeholders = any(count > 1 for count in seen.values())

    percent_tokens = any(
        "%" in name or "percent" in name.lower() for name in placeholders
    )
    if not percent_tokens:
        percent_tokens = any("%" in (block.text or "") for block in extracted.blocks)

    header_only_tables = False
    for index in table_indexes:
        rows = {
            block.row_index
            for block in extracted.blocks
            if block.table_index == index and block.row_index is not None
        }
        if rows and max(rows) == 0:
            header_only_tables = True
            break

    reasons: list[str] = []
    if placeholder_count > easy_max_placeholders:
        reasons.append("placeholder_count")
    if table_count > easy_max_tables:
        reasons.append("table_count")
    if cfg_rules.get("hard_if_percent_tokens", True) and percent_tokens:
        reasons.append("percent_tokens")
    if cfg_rules.get("hard_if_duplicate_placeholders", True) and duplicate_placeholders:
        reasons.append("duplicate_placeholders")
    if cfg_rules.get("hard_if_header_only_tables", True) and header_only_tables:
        reasons.append("header_only_tables")

    complexity = "hard" if reasons else "easy"
    return complexity, {
        "placeholder_count": placeholder_count,
        "table_count": table_count,
        "percent_tokens": percent_tokens,
        "duplicate_placeholders": duplicate_placeholders,
        "header_only_tables": header_only_tables,
        "reasons": reasons,
    }


def pick_mapper_model_id(
    cfg: OptimizationConfig,
    complexity: str,
    retry_count: int,
) -> str | None:
    models = cfg.models
    easy = models.get("mapper_easy")
    hard = models.get("mapper_hard")
    retry = models.get("mapper_retry")
    final = models.get("mapper_final")
    if retry_count >= 2:
        return _first_model(final, retry, hard, easy)
    if retry_count >= 1:
        return _first_model(retry, hard, easy)
    if complexity == "hard":
        return _first_model(hard, easy)
    return _first_model(easy, hard)


def pick_validator_model_id(cfg: OptimizationConfig, retry_count: int) -> str | None:
    models = cfg.models
    validator = models.get("validator")
    retry = models.get("validator_retry")
    if retry_count >= 1:
        return _first_model(retry, validator)
    return _first_model(validator)


def json_retry_defaults(cfg: OptimizationConfig) -> tuple[int, float]:
    retries = cfg.retries
    max_retries = retries.get("max_retries", 2)
    threshold = retries.get("validation_threshold", 0.7)
    try:
        max_retries_i = max(0, min(3, int(max_retries)))
    except (TypeError, ValueError):
        max_retries_i = 2
    try:
        threshold_f = max(0.0, min(1.0, float(threshold)))
    except (TypeError, ValueError):
        threshold_f = 0.7
    return max_retries_i, threshold_f


def apply_optimization(
    state: dict[str, Any],
    extracted: ExtractedTemplate,
    *,
    retry_count: int | None = None,
) -> dict[str, Any]:
    """Score complexity and set mapper/validator ids + JSON retry defaults."""
    cfg = load_optimization_config()
    complexity, signals = score_complexity(extracted, rules=cfg.complexity)
    attempt = int(state.get("retry_count") or 0) if retry_count is None else retry_count
    mapper_id = pick_mapper_model_id(cfg, complexity, attempt)
    validator_id = pick_validator_model_id(cfg, attempt)
    json_retries, json_threshold = json_retry_defaults(cfg)

    updates: dict[str, Any] = {
        "complexity": complexity,
        "complexity_signals": signals,
    }
    if mapper_id:
        updates["mapper_model_id"] = mapper_id
    if validator_id:
        updates["validator_model_id"] = validator_id
    if state.get("max_retries") is None:
        updates["max_retries"] = json_retries
    if state.get("validation_threshold") is None:
        updates["validation_threshold"] = json_threshold
    if cfg.extras.get("skip_extraction_critic_when_easy") and complexity == "easy":
        updates["skip_extraction_validation"] = True

    logger.info(
        "LLM optimisation: complexity=%s mapper=%s validator=%s retry=%s signals=%s",
        complexity,
        mapper_id,
        validator_id,
        attempt,
        signals,
    )
    return updates


def upgrade_models_for_retry(state: dict[str, Any], retry_count: int) -> dict[str, Any]:
    cfg = load_optimization_config()
    complexity = str(state.get("complexity") or "hard")
    mapper_id = pick_mapper_model_id(cfg, complexity, retry_count)
    validator_id = pick_validator_model_id(cfg, retry_count)
    updates: dict[str, Any] = {}
    if mapper_id:
        updates["mapper_model_id"] = mapper_id
    if validator_id:
        updates["validator_model_id"] = validator_id
    logger.info(
        "LLM optimisation retry %s: mapper=%s validator=%s",
        retry_count,
        mapper_id,
        validator_id,
    )
    return updates


def should_skip_document_critic(state: dict[str, Any]) -> bool:
    """Skip the judge only when extras explicitly allow it for easy leftover-free jobs."""
    if not state.get("optimized_flow"):
        return False
    if state.get("skip_validation"):
        return False
    try:
        cfg = load_optimization_config()
    except (OSError, ValueError):
        return False
    extras = cfg.extras
    if not extras.get("skip_document_critic_when_easy_and_no_leftovers"):
        return False
    if str(state.get("complexity") or "") != "easy":
        return False
    generation = state.get("generation")
    leftovers: list[str] = []
    if isinstance(generation, GenerationResult):
        leftovers = list(generation.leftover_placeholders or [])
    elif isinstance(generation, dict):
        leftovers = list(generation.get("leftover_placeholders") or [])
    if extras.get("regex_leftover_check_before_judge", True) and leftovers:
        return False
    return True
