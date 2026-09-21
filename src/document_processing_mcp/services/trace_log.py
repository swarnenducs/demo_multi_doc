"""Persist and emit xid-correlated logs for HTTP, tools, and LLM responses."""

from __future__ import annotations

import json
import os
import time
import traceback
from functools import wraps
from typing import Any, Callable, TypeVar

from document_processing_mcp.core.request_context import get_job_id, require_xid

F = TypeVar("F", bound=Callable[..., Any])


def _max_chars() -> int:
    try:
        return max(500, int(os.getenv("TRACE_LOG_MAX_CHARS", "16000")))
    except ValueError:
        return 16000


def _enabled() -> bool:
    return os.getenv("TRACE_LOGGING_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _safe_json(value: Any, *, max_chars: int | None = None) -> str:
    limit = max_chars if max_chars is not None else _max_chars()

    def _default(obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump()
            except Exception:  # noqa: BLE001
                return str(obj)
        if isinstance(obj, (set, tuple)):
            return list(obj)
        return str(obj)

    try:
        text = json.dumps(value, default=_default, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        text = json.dumps({"repr": repr(value)}, ensure_ascii=False)
    if len(text) > limit:
        return text[: limit - 20] + f"...<truncated:{len(text)}>"
    return text


def _store():
    from document_processing_mcp.core.dependencies import get_call_log_store

    return get_call_log_store()


def log_event(
    *,
    kind: str,
    name: str,
    request_payload: Any = None,
    response_payload: Any = None,
    status: str = "ok",
    error: str | None = None,
    latency_ms: float | None = None,
    provider: str | None = None,
    model: str | None = None,
    meta: dict[str, Any] | None = None,
    xid: str | None = None,
    job_id: str | None = None,
) -> str | None:
    """Write one call/request log row keyed by xid. Returns log id or None if disabled."""
    if not _enabled():
        return None
    corr = (xid or require_xid()).strip()
    try:
        return _store().insert_call_log(
            xid=corr,
            job_id=job_id if job_id is not None else get_job_id(),
            kind=kind,
            name=name,
            status=status,
            provider=provider,
            model=model,
            request_json=_safe_json(request_payload) if request_payload is not None else None,
            response_json=_safe_json(response_payload) if response_payload is not None else None,
            error_message=error,
            latency_ms=latency_ms,
            meta_json=_safe_json(meta) if meta else None,
        )
    except Exception:  # noqa: BLE001 — never break business flow for logging
        return None


def traced_invoke(
    chain: Any,
    payload: dict[str, Any],
    *,
    role: str,
    provider: str | None = None,
    model: str | None = None,
) -> Any:
    """Wrap LangChain ``chain.invoke`` and persist request + response under current xid."""
    xid = require_xid()
    started = time.perf_counter()
    try:
        result = chain.invoke(payload)
        latency = (time.perf_counter() - started) * 1000.0
        response_body: Any = result
        if hasattr(result, "model_dump"):
            response_body = result.model_dump()
        log_event(
            kind="llm",
            name=role,
            request_payload=payload,
            response_payload=response_body,
            status="ok",
            latency_ms=latency,
            provider=provider,
            model=model,
            xid=xid,
        )
        return result
    except Exception as exc:
        latency = (time.perf_counter() - started) * 1000.0
        log_event(
            kind="llm",
            name=role,
            request_payload=payload,
            response_payload=None,
            status="error",
            error=f"{exc}\n{traceback.format_exc()[-2000:]}",
            latency_ms=latency,
            provider=provider,
            model=model,
            xid=xid,
        )
        raise


def traced_tool(name: str) -> Callable[[F], F]:
    """Decorator: log tool args + return value under current xid."""

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            xid = require_xid()
            started = time.perf_counter()
            req = {"args": args, "kwargs": kwargs}
            try:
                result = fn(*args, **kwargs)
                latency = (time.perf_counter() - started) * 1000.0
                log_event(
                    kind="tool",
                    name=name,
                    request_payload=req,
                    response_payload=result,
                    status="ok",
                    latency_ms=latency,
                    xid=xid,
                )
                return result
            except Exception as exc:
                latency = (time.perf_counter() - started) * 1000.0
                log_event(
                    kind="tool",
                    name=name,
                    request_payload=req,
                    response_payload=None,
                    status="error",
                    error=f"{exc}",
                    latency_ms=latency,
                    xid=xid,
                )
                raise

        return wrapper  # type: ignore[return-value]

    return decorator
