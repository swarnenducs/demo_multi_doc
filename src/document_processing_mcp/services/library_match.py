"""Pick the closest library .docx so the marker LLM can reuse known placeholder names.

The uploaded file keeps its own layout. The match is context only.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from document_processing_mcp.services.style_extractor import extract_word_styles

_TOKEN = re.compile(r"[a-z]{3,}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def library_template_dirs() -> list[Path]:
    dirs: list[Path] = []
    raw = (os.getenv("DOCUMENT_LIBRARY_TEMPLATES_DIR") or "").strip()
    if raw:
        dirs.append(Path(raw).expanduser().resolve())
    dirs.append((_repo_root() / "samples" / "templates").resolve())
    extra = (os.getenv("DOCUMENT_DEFAULT_TEMPLATE_DIR") or "").strip()
    if extra:
        dirs.append(Path(extra).expanduser().resolve())
    seen: set[Path] = set()
    out: list[Path] = []
    for folder in dirs:
        if folder in seen or not folder.is_dir():
            continue
        seen.add(folder)
        out.append(folder)
    return out


def list_library_docx() -> list[Path]:
    files: list[Path] = []
    for folder in library_template_dirs():
        for path in sorted(folder.glob("*.docx")):
            if path.name.startswith("~$") or path.name.startswith("."):
                continue
            files.append(path)
    return files


def _block_text(extracted) -> str:
    parts = [str(b.text or "") for b in (extracted.blocks or [])]
    return "\n".join(parts)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def similarity_score(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_closest_library_template(
    uploaded_extracted,
    *,
    exclude_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Jaccard overlap of body words vs default library templates."""
    source = _block_text(uploaded_extracted)
    exclude = Path(exclude_path).resolve() if exclude_path else None
    best: dict[str, Any] | None = None
    for path in list_library_docx():
        if exclude and path.resolve() == exclude:
            continue
        try:
            other = extract_word_styles(path)
        except Exception:  # noqa: BLE001
            continue
        score = similarity_score(source, _block_text(other))
        if best is None or score > float(best["score"]):
            best = {
                "path": str(path),
                "name": path.name,
                "score": round(score, 4),
                "placeholders": list(other.placeholders or []),
                "preview": _block_text(other)[:1200],
            }
    if best is None or float(best["score"]) <= 0:
        return None
    return best


def json_key_hints(data: dict[str, Any] | None, *, limit: int = 80) -> list[str]:
    if not isinstance(data, dict):
        return []
    keys: list[str] = []

    def walk(node: Any, prefix: str) -> None:
        if len(keys) >= limit:
            return
        if isinstance(node, dict):
            for name, value in node.items():
                path = f"{prefix}.{name}" if prefix else str(name)
                keys.append(path)
                walk(value, path)
        elif isinstance(node, list) and node and isinstance(node[0], dict):
            walk(node[0], f"{prefix}[]" if prefix else "[]")

    walk(data, "")
    return keys[:limit]
