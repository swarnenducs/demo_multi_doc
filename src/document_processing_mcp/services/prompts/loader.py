"""Load ChatPromptTemplate content from YAML (document-processing-mcp/prompts)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from langchain_core.prompts import ChatPromptTemplate

from document_processing_mcp.services.prompts.prompt_versions import (
    assert_file_version,
    catalog_as_dicts,
    load_prompt_versions,
    resolve_versioned_path,
    versions_file,
)


def _component_root() -> Path:
    """document-processing-mcp/ (contains prompts/ + src/)."""
    return Path(__file__).resolve().parents[4]


_COMPONENT_ROOT = _component_root()
_PACKAGE_DEFAULTS = Path(__file__).resolve().parent / "yml"


def prompts_dir() -> Path:
    """Directory containing versioned mapper / validator YAML."""
    override = (
        os.getenv("DOCUMENT_PROMPTS_DIR", "").strip()
        or os.getenv("PROMPTS_DIR", "").strip()
    )
    if override:
        return Path(override).expanduser().resolve()
    return (_COMPONENT_ROOT / "prompts").resolve()


def prompt_versions_path() -> Path:
    return versions_file(
        env_names=("DOCUMENT_PROMPT_VERSIONS_FILE", "PROMPT_VERSIONS_FILE"),
        default=_COMPONENT_ROOT / "config" / "prompt_versions.json",
    )


def prompt_version_catalog():
    return load_prompt_versions(prompt_versions_path())


def resolve_prompt_path(filename: str) -> Path:
    """Resolve the file for the version required in prompt_versions.json."""
    path, _required = resolve_versioned_path(
        name=filename,
        prompts_dir=prompts_dir(),
        catalog=prompt_version_catalog(),
        extra_dirs=(_PACKAGE_DEFAULTS,),
    )
    return path


def load_prompt_yaml(filename: str, *, reload: bool = True) -> dict[str, Any]:
    """Load a prompt YAML. Reloads from disk each call so edits apply without restart."""
    catalog = prompt_version_catalog()
    path, required = resolve_versioned_path(
        name=filename,
        prompts_dir=prompts_dir(),
        catalog=catalog,
        extra_dirs=(_PACKAGE_DEFAULTS,),
    )
    if reload:
        _load_yaml_cached.cache_clear()
    payload = _load_yaml_cached(str(path), path.stat().st_mtime_ns)
    assert_file_version(str(payload["version"]), required, path=path)
    payload["required_version"] = required
    return payload


@lru_cache(maxsize=16)
def _load_yaml_cached(path_str: str, _mtime_ns: int) -> dict[str, Any]:
    path = Path(path_str)
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Prompt YAML root must be a mapping: {path}")
    system = data.get("system")
    human = data.get("human")
    version = str(data.get("version") or "").strip()
    if not version:
        raise ValueError(f"Prompt YAML missing 'version': {path}")
    if not isinstance(system, str) or not system.strip():
        raise ValueError(f"Prompt YAML missing non-empty 'system' string: {path}")
    if not isinstance(human, str) or not human.strip():
        raise ValueError(f"Prompt YAML missing non-empty 'human' string: {path}")
    return {
        "name": str(data.get("name") or path.stem),
        "version": version,
        "system": system.strip(),
        "human": human.strip(),
        "path": str(path),
    }


def chat_prompt_from_yaml(filename: str) -> ChatPromptTemplate:
    payload = load_prompt_yaml(filename)
    return ChatPromptTemplate.from_messages(
        [
            ("system", payload["system"]),
            ("human", payload["human"]),
        ]
    )


def catalog_prompt_files() -> list[dict[str, Any]]:
    return catalog_as_dicts(
        prompt_version_catalog(),
        prompts_dir=prompts_dir(),
        extra_dirs=(_PACKAGE_DEFAULTS,),
    )
