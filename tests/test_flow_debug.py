"""Flow logger is off by default and prints file + method when enabled."""

from __future__ import annotations

from document_processing_mcp.flow_debug import (
    flow_breakpoint,
    flow_enabled,
    install_flow_logger,
    reset_flow_logger,
)


def test_flow_debug_off_by_default(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DEBUG_FLOW", raising=False)
    monkeypatch.delenv("DEBUG_FLOW_POINTS", raising=False)
    monkeypatch.delenv("DEBUG_FLOW_BREAK", raising=False)
    reset_flow_logger()
    assert flow_enabled() is False
    flow_breakpoint("should_not_print")
    assert capsys.readouterr().out == ""
    assert install_flow_logger() is False


def test_flow_hops_print_file_and_method(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DEBUG_FLOW", "hops")
    monkeypatch.delenv("DEBUG_FLOW_POINTS", raising=False)
    monkeypatch.delenv("DEBUG_FLOW_BREAK", raising=False)
    reset_flow_logger()

    def sample_method() -> None:
        flow_breakpoint("unit_hop", job_id="abc")

    sample_method()
    out = capsys.readouterr().out
    assert "[FLOW]" in out
    assert "sample_method" in out
    assert "test_flow_debug.py" in out
    assert "unit_hop" in out


def test_flow_trace_logs_project_function(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DEBUG_FLOW", "1")
    monkeypatch.delenv("DEBUG_FLOW_POINTS", raising=False)
    reset_flow_logger()
    install_flow_logger()

    def inner_flow_step() -> int:
        return 1

    assert inner_flow_step() == 1
    reset_flow_logger()
    out = capsys.readouterr().out
    assert "inner_flow_step" in out
    assert "test_flow_debug.py" in out
