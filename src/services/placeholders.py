"""Shared Word placeholder token patterns (syntax only — no domain field names)."""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

# Token syntax forms only — NOT business field aliases
_FIELD = r"([a-zA-Z_][a-zA-Z0-9_]*(?:\[[0-9]+\])?(?:\.[a-zA-Z_][a-zA-Z0-9_]*(?:\[[0-9]+\])?)*)"
_ANGLE_FIELD = r"([A-Za-z][A-Za-z0-9 _/\-]{0,80}?)"
# Bare contract fill markers: XX% / X% (no angle brackets).
# Do not use \b after % — % is non-word so trailing \b never matches.
_BARE_PCT = r"(?<![A-Za-z0-9_])(XX%|X%)(?![A-Za-z0-9_])"

PLACEHOLDER_REGEXES = [
    re.compile(r"\{\{\s*" + _FIELD + r"\s*\}\}"),
    re.compile(r"\$\{\s*" + _FIELD + r"\s*\}"),
    re.compile(r"«\s*" + _FIELD + r"\s*»"),
    re.compile(r"<\s*" + _ANGLE_FIELD + r"\s*>"),
    re.compile(_BARE_PCT, re.IGNORECASE),
]

PLACEHOLDER_PATTERNS = [
    (re.compile(r"\{\{\s*" + _FIELD + r"\s*\}\}"), "{{", "}}"),
    (re.compile(r"\$\{\s*" + _FIELD + r"\s*\}"), "${", "}"),
    (re.compile(r"«\s*" + _FIELD + r"\s*»"), "«", "»"),
    (re.compile(r"<\s*" + _ANGLE_FIELD + r"\s*>"), "<", ">"),
    # Replace whole bare token (e.g. XX% → 93%)
    (re.compile(_BARE_PCT, re.IGNORECASE), "", ""),
]


def normalize_placeholder_key(raw: str) -> str:
    key = raw.strip()
    # Normalize bare percent tokens to canonical upper form
    if re.fullmatch(r"XX%|X%", key, flags=re.IGNORECASE):
        return key.upper()
    return re.sub(r"\s+", " ", key)


def template_has_markers(
    extracted: object | None = None,
    *,
    template_path: str | None = None,
) -> tuple[bool, list[str]]:
    """Return whether the template already has fill tokens, and the keys found.

    Check order:
    1. Keys already extracted from body blocks (``ExtractedTemplate.placeholders``).
    2. Regex scan of ``word/*.xml`` in the .docx (headers, footers, footnotes).
    """
    keys: list[str] = []
    seen: set[str] = set()

    placeholders = getattr(extracted, "placeholders", None)
    if isinstance(placeholders, list):
        for raw in placeholders:
            key = normalize_placeholder_key(str(raw))
            if key and key not in seen:
                seen.add(key)
                keys.append(key)

    path_raw = template_path or getattr(extracted, "template_path", None)
    if path_raw:
        path = Path(str(path_raw))
        if path.is_file() and path.suffix.lower() == ".docx":
            try:
                with zipfile.ZipFile(path, "r") as zf:
                    for name in zf.namelist():
                        if not name.startswith("word/") or not name.endswith(".xml"):
                            continue
                        xml = zf.read(name).decode("utf-8", errors="ignore")
                        visible = html.unescape(re.sub(r"<[^>]+>", " ", xml))
                        for key in find_placeholders(visible):
                            if key not in seen:
                                seen.add(key)
                                keys.append(key)
            except (OSError, zipfile.BadZipFile):
                pass

    return bool(keys), keys


def find_placeholders(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in PLACEHOLDER_REGEXES:
        for match in pattern.findall(text):
            key = normalize_placeholder_key(match)
            if key not in seen:
                seen.add(key)
                found.append(key)
    return found
