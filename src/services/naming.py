"""Helpers for generated contract/document filenames."""

from __future__ import annotations

import re
from pathlib import Path


def job_id_short_suffix(job_id: str) -> str:
    """
    Last UUID block for naming, e.g.
    ``a1b2c3d4-e5f6-7890-abcd-50642e6a035d`` → ``50642e6a035d``.
    """
    cleaned = (job_id or "").strip()
    if not cleaned:
        return "unknown"
    if "-" in cleaned:
        return cleaned.rsplit("-", 1)[-1].lower()
    alnum = re.sub(r"[^0-9a-zA-Z]", "", cleaned)
    if len(alnum) >= 12:
        return alnum[-12:].lower()
    return alnum.lower() or "unknown"


def sanitize_template_stem(template_name: str) -> str:
    """Safe stem from an uploaded template filename."""
    stem = Path(template_name or "template").stem
    stem = re.sub(r"[^\w\-]+", "_", stem, flags=re.UNICODE).strip("_")
    return stem or "template"


def build_contract_output_filename(job_id: str, template_name: str) -> str:
    """
    ``{job_id_last_block}_{template_stem}.docx``

    Example: ``50642e6a035d_complete_contract_template_GPO.docx``
    """
    return f"{job_id_short_suffix(job_id)}_{sanitize_template_stem(template_name)}.docx"
