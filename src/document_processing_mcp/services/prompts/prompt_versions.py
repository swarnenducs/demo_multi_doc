"""Load required prompt versions from ``config/prompt_versions.json``."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_VERSIONED_STEM = re.compile(
    r"^(?P<name>.+)\.(?P<version>\d+\.\d+\.\d+)$"
)


@dataclass(frozen=True)
class PromptVersionSpec:
    name: str
    required_version: str
    relative_path: str


def logical_prompt_name(filename: str) -> str:
    stem = Path(filename).stem
    match = _VERSIONED_STEM.match(stem)
    if match:
        return match.group("name")
    return stem


def versions_file(*, env_names: tuple[str, ...], default: Path) -> Path:
    for key in env_names:
        raw = (os.getenv(key) or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
    return default.resolve()


def load_prompt_versions(path: Path) -> dict[str, PromptVersionSpec]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing prompt versions JSON: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"prompt_versions.json must be an object: {path}")
    raw = payload.get("prompts")
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"prompt_versions.json missing prompts: {path}")
    out: dict[str, PromptVersionSpec] = {}
    for key, body in raw.items():
        name = logical_prompt_name(str(key))
        if isinstance(body, str):
            version = body.strip()
            rel = ""
        elif isinstance(body, dict):
            version = str(body.get("required_version") or body.get("version") or "").strip()
            rel = str(body.get("path") or "").strip()
        else:
            continue
        if not version:
            raise ValueError(f"Prompt {name!r} missing required_version in {path}")
        out[name] = PromptVersionSpec(name=name, required_version=version, relative_path=rel)
    return out


def resolve_versioned_path(
    *,
    name: str,
    prompts_dir: Path,
    catalog: dict[str, PromptVersionSpec],
    extra_dirs: tuple[Path, ...] = (),
) -> tuple[Path, str]:
    key = logical_prompt_name(name)
    spec = catalog.get(key)
    if spec is None:
        raise KeyError(
            f"Prompt {key!r} is not listed in prompt_versions.json "
            f"(requested {name!r})"
        )
    relative = spec.relative_path or f"{key}.{spec.required_version}"
    relative = relative.replace("{version}", spec.required_version)
    candidates = [prompts_dir / relative, *(folder / relative for folder in extra_dirs)]
    for path in candidates:
        if path.is_file():
            return path, spec.required_version
    raise FileNotFoundError(
        f"Required prompt {key!r} version {spec.required_version} not found "
        f"(looked for {relative} under {prompts_dir})"
    )


def assert_file_version(file_version: str, required_version: str, *, path: Path) -> None:
    if str(file_version).strip() != str(required_version).strip():
        raise ValueError(
            f"Prompt version mismatch for {path}: file={file_version!r} "
            f"required={required_version!r} (from prompt_versions.json)"
        )


def catalog_as_dicts(
    catalog: dict[str, PromptVersionSpec],
    *,
    prompts_dir: Path,
    extra_dirs: tuple[Path, ...] = (),
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for spec in catalog.values():
        path, version = resolve_versioned_path(
            name=spec.name,
            prompts_dir=prompts_dir,
            catalog=catalog,
            extra_dirs=extra_dirs,
        )
        items.append(
            {
                "name": spec.name,
                "required_version": version,
                "path": str(path),
            }
        )
    return items
