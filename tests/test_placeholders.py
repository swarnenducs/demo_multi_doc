"""Tests for placeholder token detection (including bare XX%)."""

from __future__ import annotations

from document_processing_mcp.services.placeholders import find_placeholders


def test_angle_xx_percent_phrase() -> None:
    keys = find_placeholders("approximately <XX>% of total annual Advanced Wound Care")
    assert "XX" in keys


def test_bare_xx_percent_detected() -> None:
    keys = find_placeholders("commit to purchase XX% of its aggregate requirements")
    assert "XX%" in keys


def test_admin_fee_placeholders() -> None:
    keys = find_placeholders(
        'Administrative Fee") equal to <X Percent> (<X>%) of the aggregate'
    )
    assert "X Percent" in keys
    assert "X" in keys
