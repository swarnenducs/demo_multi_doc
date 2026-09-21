"""Load local ``.env`` files through Dynaconf; never override process env.

Azure Web App Application settings and the laptop launcher (``IPP_FORCE_SQLITE``,
blank ``AZURE_SQL_*``) are already in ``os.environ`` and must win. Dynaconf only
fills keys that are missing, using the same names as today (no ``DYNACONF_`` prefix).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dynaconf import Dynaconf

# core/ → package → src → document-processing-mcp folder
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_MONOREPO_ROOT = _PACKAGE_ROOT.parent


def _dotenv_paths() -> list[Path]:
    """Component ``.env`` first (wins among files), then repo-root ``.env``."""
    paths: list[Path] = []
    pkg = _PACKAGE_ROOT / ".env"
    if pkg.is_file():
        paths.append(pkg)
    root = _MONOREPO_ROOT / ".env"
    if (_MONOREPO_ROOT / "run_all_components.py").is_file() and root.is_file():
        if not paths or root.resolve() != paths[0].resolve():
            paths.append(root)
    return paths


def _export_scalar(name: str, value: Any, *, override: bool) -> None:
    if not name or name.startswith("_") or name.endswith("_FOR_DYNACONF"):
        return
    if name in {
        "SETTINGS_FILES_FOR_DYNACONF",
        "ENVVAR_PREFIX_FOR_DYNACONF",
        "ENVIRONMENTS_FOR_DYNACONF",
        "LOAD_DOTENV_FOR_DYNACONF",
        "DOTENV_PATH_FOR_DYNACONF",
        "REDIS_ENABLED_FOR_DYNACONF",
    }:
        return
    if isinstance(value, (dict, list, tuple, set)):
        return
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    if not override and name in os.environ:
        return
    os.environ[name] = str(value)


def _export_box(box: Dynaconf, *, override: bool) -> None:
    try:
        data = box.as_dict()
    except Exception:  # noqa: BLE001
        data = dict(box)
    for key, value in data.items():
        _export_scalar(str(key).upper(), value, override=override)


def apply_dynaconf_from_env_files() -> Dynaconf:
    """Read ``.env`` via Dynaconf and copy into ``os.environ`` if the key is unset.

    Process environment (Azure App Settings, ``run_all_components.py``) is never
    overwritten. Call once at import; tests use ``monkeypatch`` + ``reload_settings``.
    """
    paths = _dotenv_paths()
    last: Dynaconf | None = None
    ordered = list(reversed(paths))
    if not ordered:
        last = Dynaconf(
            envvar_prefix=False,
            load_dotenv=False,
            environments=False,
            settings_files=[],
        )
        return last
    for path in ordered:
        last = Dynaconf(
            envvar_prefix=False,
            load_dotenv=True,
            dotenv_path=str(path),
            environments=False,
            settings_files=[],
        )
        _export_box(last, override=False)
    assert last is not None
    return last
