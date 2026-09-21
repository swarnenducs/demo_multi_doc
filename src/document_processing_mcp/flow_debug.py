"""Optional flow logger + debugger hops (default off).

Enable file + method prints for this process:

  export DEBUG_FLOW=1

Log only the named hops (no full call trace):

  export DEBUG_FLOW=hops
  export DEBUG_FLOW_POINTS=create_document_job,map_fields_node,ask_maf

Pause in the debugger at matching hops:

  export DEBUG_FLOW_BREAK=1
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from types import FrameType
from typing import Any

_LOG = logging.getLogger("flow_debug")
_SKIP_FILES = {"flow_debug.py"}
_SKIP_NAMES = {"<module>", "<lambda>", "<listcomp>", "<genexpr>", "<setcomp>", "<dictcomp>"}
_PROJECT_PARTS = {
    "document_processing_mcp",
    "document-processing-mcp",
    "voice_enable_mcp",
    "central_agentic_flow",
    "central-agentic-flow",
    "ip_api",
    "ui_app",
    "UI",
}
_installed = False


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def flow_mode() -> str:
    """``off`` | ``trace`` | ``hops`` | ``break``."""
    raw = (os.getenv("DEBUG_FLOW") or "").strip().lower()
    if raw in {"trace", "all", "1", "true", "yes", "on"}:
        return "trace"
    if raw in {"hops", "hop"}:
        return "hops"
    if raw in {"break", "breakpoint", "debug"}:
        return "break"
    return "off"


def flow_points() -> set[str]:
    raw = os.getenv("DEBUG_FLOW_POINTS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def flow_enabled() -> bool:
    if flow_mode() != "off":
        return True
    return bool(flow_points())


def flow_trace_enabled() -> bool:
    return flow_mode() in {"trace", "break"}


def flow_break_enabled() -> bool:
    if _truthy(os.getenv("DEBUG_FLOW_BREAK")):
        return True
    return flow_mode() == "break"


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "run_all_components.py").is_file():
            return parent
    return None


def _is_project_file(path: str) -> bool:
    if not path or path.startswith("<"):
        return False
    parts = Path(path).parts
    if "site-packages" in parts or ".venv" in parts or "dist-packages" in parts:
        return False
    name = Path(path).name
    if name in _SKIP_FILES:
        return False
    return any(part in _PROJECT_PARTS for part in parts)


def _display_path(path: str) -> str:
    resolved = Path(path).resolve()
    root = _repo_root()
    if root is not None:
        try:
            return str(resolved.relative_to(root))
        except ValueError:
            pass
    return resolved.name


def _project_depth(frame: FrameType) -> int:
    depth = 0
    current: FrameType | None = frame
    while current is not None:
        if _is_project_file(current.f_code.co_filename):
            depth += 1
        current = current.f_back
    return max(0, depth - 1)


def _format_line(
    filename: str,
    lineno: int,
    func: str,
    *,
    hop: str | None = None,
    watch: dict[str, Any] | None = None,
    depth: int = 0,
) -> str:
    indent = "  " * min(depth, 16)
    loc = f"{_display_path(filename)}:{lineno} {func}"
    extra = ""
    if hop:
        extra += f"  [{hop}]"
    if watch:
        keys = ", ".join(f"{key}={type(value).__name__}" for key, value in watch.items())
        extra += f"  {keys}"
    return f"[FLOW] {indent}{loc}{extra}"


def _emit(message: str) -> None:
    print(message, flush=True)
    if _LOG.handlers:
        _LOG.info("%s", message)


def _caller_frame(skip: int = 2) -> FrameType | None:
    frame: FrameType | None = sys._getframe(skip)
    while frame is not None:
        if Path(frame.f_code.co_filename).name not in _SKIP_FILES:
            return frame
        frame = frame.f_back
    return None


def flow_log(name: str | None = None, **watch: object) -> None:
    """Print caller file + method when flow debug is on. No-op when off."""
    if not flow_enabled():
        return
    points = flow_points()
    if points and name and name not in points and "*" not in points and flow_mode() == "off":
        return
    if points and name and name not in points and "*" not in points and flow_mode() == "hops":
        return
    frame = _caller_frame(2)
    if frame is None:
        return
    _emit(
        _format_line(
            frame.f_code.co_filename,
            frame.f_lineno,
            frame.f_code.co_name,
            hop=name,
            watch=watch or None,
            depth=_project_depth(frame),
        )
    )


def flow_breakpoint(name: str, **watch: object) -> None:
    """Log file + method at a named hop. Pause only when DEBUG_FLOW_BREAK=1."""
    points = flow_points()
    mode = flow_mode()
    matched = mode != "off" or name in points or "*" in points
    if not matched:
        return
    if points and "*" not in points and name not in points and mode == "hops":
        return
    frame = _caller_frame(2)
    if frame is not None:
        _emit(
            _format_line(
                frame.f_code.co_filename,
                frame.f_lineno,
                frame.f_code.co_name,
                hop=name,
                watch=watch or None,
                depth=_project_depth(frame),
            )
        )
    else:
        _emit(f"[FLOW] [{name}]")
    if flow_break_enabled() or (points and name in points and mode == "off"):
        breakpoint()


def _tracer(frame: FrameType, event: str, _arg: object) -> Any:
    if event != "call":
        return _tracer
    code = frame.f_code
    if code.co_name in _SKIP_NAMES or code.co_name.startswith("__"):
        return _tracer
    filename = code.co_filename
    if not _is_project_file(filename):
        return _tracer
    points = flow_points()
    if points and "*" not in points and code.co_name not in points:
        return _tracer
    _emit(
        _format_line(
            filename,
            code.co_firstlineno,
            code.co_name,
            depth=_project_depth(frame),
        )
    )
    return _tracer


def install_flow_logger() -> bool:
    """Start process-wide file+method logging. Safe to call more than once."""
    global _installed
    if not flow_enabled():
        return False
    if flow_trace_enabled() and not _installed:
        sys.settrace(_tracer)
        threading.settrace(_tracer)
        _installed = True
        _emit(f"[FLOW] enabled mode={flow_mode()} (file + method)")
    return True


def reset_flow_logger() -> None:
    """Tests / disable tracing in this process."""
    global _installed
    sys.settrace(None)
    threading.settrace(None)
    _installed = False
