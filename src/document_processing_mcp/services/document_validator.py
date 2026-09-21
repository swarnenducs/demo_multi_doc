"""Validate generated Word docs against template + JSON using the validator LLM only."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field

from document_processing_mcp.models.schemas import (
    ExtractedTemplate,
    MappingResult,
    ValidationIssue,
    ValidationResult,
)
from document_processing_mcp.services.placeholders import PLACEHOLDER_REGEXES as LEFTOVER_PATTERNS
from document_processing_mcp.services.trace_log import traced_invoke

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _qn(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _docx_plain_text(path: str | Path) -> str:
    path = Path(path)
    with zipfile.ZipFile(path, "r") as zf:
        document_xml = zf.read("word/document.xml").decode("utf-8")
    root = ET.fromstring(document_xml)
    parts: list[str] = []
    for node in root.iter():
        if node.tag == _qn("t") and node.text:
            parts.append(node.text)
        elif node.tag == _qn("tab"):
            parts.append("\t")
        elif node.tag == _qn("br"):
            parts.append("\n")
    return "".join(parts)


def _find_leftovers(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in LEFTOVER_PATTERNS:
        for match in pattern.finditer(text):
            key = match.group(1)
            if key not in seen:
                seen.add(key)
                found.append(key)
    return found


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 24)] + f"\n...<truncated:{len(text)}>"


def _dumps_compact(obj: Any, limit: int) -> str:
    """Minify JSON so Groq TPM budgets are not wasted on whitespace."""
    return _clip(json.dumps(obj, separators=(",", ":"), default=str), limit)


def _key_value_snippets(generated_text: str, mapping: MappingResult, *, limit: int = 1500) -> str:
    """Pull short windows around mapped values so truncation does not hide fills."""
    snippets: list[str] = []
    seen: set[str] = set()
    for item in mapping.mappings:
        value = "" if item.value is None else str(item.value).strip()
        if not value or value in seen:
            continue
        idx = generated_text.find(value)
        if idx < 0:
            snippets.append(f"[MISSING {item.placeholder!r}: {value!r}]")
            seen.add(value)
            continue
        start = max(0, idx - 40)
        end = min(len(generated_text), idx + len(value) + 60)
        window = generated_text[start:end].replace("\n", " ")
        snippets.append(f"- {item.placeholder}={value!r}: …{window}…")
        seen.add(value)
        if sum(len(s) for s in snippets) >= limit:
            break
    # Always include known contract sections when present
    for marker in ("SOLE COMMITMENT", "ADMINISTRATIVE FEE", "Committed Level", "EXHIBIT A"):
        idx = generated_text.find(marker)
        if idx < 0:
            continue
        window = generated_text[idx : idx + 160].replace("\n", " ")
        snippets.append(f"- section {marker}: …{window}…")
    return "\n".join(snippets)[:limit] if snippets else "(no snippets)"


class _LLMValidationIssue(BaseModel):
    severity: str = Field(default="medium", description="high | medium | low")
    field: str = Field(default="", description="Placeholder or field name if known")
    message: str = Field(description="What is wrong")
    expected: str = Field(default="", description="Expected value or state")
    actual: str = Field(default="", description="Actual value or state")


class _LLMValidationPayload(BaseModel):
    passed: bool = False
    validation_score: float = Field(default=0.0, ge=0.0, le=1.0)
    issues: list[_LLMValidationIssue] = Field(default_factory=list)
    summary: str = Field(default="")


def _llm_validation(
    template: ExtractedTemplate,
    generated_path: str | Path,
    json_data: dict[str, Any],
    mapping: MappingResult,
    *,
    model_id: str | None = None,
) -> ValidationResult:
    from document_processing_mcp.services.llm_factory import (
        LLMAsJudge,
        is_validator_available,
    )

    if not is_validator_available() and not model_id:
        raise RuntimeError(
            "Validator LLM is required. Check VALIDATOR_PROVIDER credentials "
            "(e.g. GROQ_API_KEY or Azure OpenAI settings)."
        )

    from document_processing_mcp.services.prompts import build_validator_chain

    try:
        judge = LLMAsJudge(model_id=model_id, structured_schema=_LLMValidationPayload)
        llm, config = judge.as_tuple()
    except (ImportError, RuntimeError, ValueError) as exc:
        raise RuntimeError(f"Failed to build validator LLM: {exc}") from exc

    # Prefer placeholder-bearing blocks; keep total prompt small for Groq TPM (~8k).
    placeholder_blocks = [
        b.text.strip()
        for b in template.blocks
        if b.text.strip() and (b.placeholder_keys or "<" in b.text or "XX%" in b.text or "X%" in b.text)
    ]
    template_text = "\n".join(placeholder_blocks) or "\n".join(
        b.text for b in template.blocks if b.text.strip()
    )
    generated_text = _docx_plain_text(generated_path)
    leftovers = _find_leftovers(generated_text)
    mapping_summary = [
        {
            "p": m.placeholder,
            "path": m.json_path,
            "v": m.value,
            "c": round(float(m.confidence), 3),
        }
        for m in mapping.mappings
    ]

    chain = build_validator_chain(llm)
    try:
        result: _LLMValidationPayload = traced_invoke(
            chain,
            {
                "template_text": _clip(template_text, 700),
                "generated_text": _clip(generated_text, 700),
                "key_snippets": _key_value_snippets(generated_text, mapping, limit=900),
                "data_json": _dumps_compact(json_data, 900),
                "mappings_json": _dumps_compact(mapping_summary, 1200),
                "unmapped_placeholders_json": _dumps_compact(
                    mapping.unmapped_placeholders, 300
                ),
                "leftovers_json": _dumps_compact(leftovers, 300),
            },
            role="validator",
            provider=config.provider,
            model=config.model,
        )  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Validator LLM invoke failed ({config.label}): {exc}") from exc

    issues = [
        ValidationIssue(
            severity=i.severity if i.severity in {"high", "medium", "low"} else "medium",  # type: ignore[arg-type]
            field=i.field or None,
            message=i.message,
            expected=i.expected or None,
            actual=i.actual or None,
        )
        for i in result.issues
    ]

    return ValidationResult(
        passed=result.passed,
        validation_score=round(min(max(result.validation_score, 0.0), 1.0), 4),
        issues=issues,
        summary=result.summary or f"Produced by LLM #2 validator ({config.label})",
        validator_source="llm",
        validator_provider=config.provider,
        validator_model=config.model,
    )


def validate_documents(
    template: ExtractedTemplate,
    generated_path: str | Path,
    json_data: dict[str, Any],
    mapping: MappingResult,
    *,
    model_id: str | None = None,
) -> ValidationResult:
    """Step 4: LLM-as-judge validation via ``LLMAsJudge`` / init_chat_model."""
    return _llm_validation(
        template, generated_path, json_data, mapping, model_id=model_id
    )
