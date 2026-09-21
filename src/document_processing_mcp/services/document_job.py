"""Download blob (or local) inputs, run LangGraph, upload output, optionally update SQL."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from document_processing_mcp.core.settings import settings
from document_processing_mcp.graph import invoke_document_graph
from document_processing_mcp.models.mcp_responses import GenerateDocumentResponse
from document_processing_mcp.services.naming import build_contract_output_filename
from document_processing_mcp.storage.blob_store import get_blob_store, is_blob_ref
from document_processing_mcp.storage.job_sink import try_job_sink

logger = logging.getLogger(__name__)


def format_elapsed_ms(elapsed_ms: float) -> str:
    """Human-readable wall time for the document generation report."""
    total_s = max(0.0, float(elapsed_ms) / 1000.0)
    if total_s < 60:
        return f"{total_s:.1f} s"
    minutes = int(total_s // 60)
    seconds = total_s - minutes * 60
    if minutes < 60:
        return f"{minutes}m {seconds:.1f}s"
    hours = minutes // 60
    minutes_rem = minutes % 60
    return f"{hours}h {minutes_rem}m {seconds:.0f}s"


def _elapsed_fields(started: float) -> dict[str, Any]:
    ms = round((time.perf_counter() - started) * 1000.0, 1)
    return {"elapsed_ms": ms, "elapsed": format_elapsed_ms(ms)}


def _dump(model: Any) -> dict[str, Any] | None:
    if model is None:
        return None
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if isinstance(model, dict):
        return model
    return None


def materialize_input(ref: str, dest: Path) -> Path:
    """Resolve a local path or Azure Blob ref onto disk for LangGraph."""
    raw = (ref or "").strip()
    if not raw:
        raise FileNotFoundError("empty path")
    blobs = get_blob_store()
    if is_blob_ref(raw):
        if not blobs.enabled:
            raise RuntimeError(
                f"Blob reference passed but Azure Blob is not configured: {raw}"
            )
        return blobs.download_file(raw, dest)
    path = Path(raw).expanduser()
    if path.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.resolve() != dest.resolve():
            dest.write_bytes(path.read_bytes())
            return dest
        return path
    if dest.is_file():
        return dest
    raise FileNotFoundError(f"file not found: {raw}")


def run_generate_document(
    *,
    template_path: str,
    data_path: str | None = None,
    data_json: str | None = None,
    output_path: str | None = None,
    job_id: str | None = None,
    xid: str | None = None,
    skip_validation: bool = False,
    skip_extraction_validation: bool = False,
    max_retries: int | None = None,
    validation_threshold: float | None = None,
    optimized_flow: bool = False,
) -> GenerateDocumentResponse:
    """
    MCP document pipeline entry: blob-or-local inputs → LangGraph → blob output.

    When ``job_id`` is set, updates ``document_jobs`` + ``document_accuracy_reports``.
    """
    from document_processing_mcp.flow_debug import flow_breakpoint

    flow_breakpoint("run_generate_document", job_id=job_id, template_path=template_path)
    cfg = settings()
    jid = (job_id or "").strip() or uuid.uuid4().hex
    work_dir = cfg.job_dir(jid)
    work_dir.mkdir(parents=True, exist_ok=True)
    blobs = get_blob_store()
    sink = try_job_sink(job_id)
    db_updated = False
    started = time.perf_counter()

    if sink is not None:
        try:
            sink.ensure_job(
                job_id,  # type: ignore[arg-type]
                template_path=template_path,
                data_path=data_path or "",
                output_path=output_path,
                xid=xid,
            )
            sink.update_status(job_id, "processing")  # type: ignore[arg-type]
            db_updated = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Document MCP could not mark job processing: %s", exc)
            sink = None
            db_updated = False

    try:
        local_tpl = materialize_input(template_path, work_dir / "template.docx")

        if data_path:
            local_data = materialize_input(data_path, work_dir / "data.json")
        elif data_json:
            payload = json.loads(data_json)
            if not isinstance(payload, dict):
                raise ValueError("data_json root must be an object")
            local_data = work_dir / "data.json"
            local_data.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        else:
            raise ValueError("Provide data_path or data_json")

        intended_out_ref = output_path
        if output_path and is_blob_ref(output_path):
            local_out = work_dir / Path(blobs.parse_ref(output_path)).name
        elif output_path:
            local_out = Path(output_path).expanduser()
            if not local_out.is_absolute():
                local_out = (work_dir / local_out.name).resolve()
        else:
            local_out = work_dir / build_contract_output_filename(jid, local_tpl.name)
        local_out.parent.mkdir(parents=True, exist_ok=True)

        result = invoke_document_graph(
            {
                "template_path": str(local_tpl),
                "data_path": str(local_data),
                "output_path": str(local_out),
                "errors": [],
                "status": "started",
                "retry_count": 0,
                "max_retries": max_retries,
                "validation_threshold": validation_threshold,
                "skip_validation": skip_validation,
                "skip_extraction_validation": skip_extraction_validation,
                "optimized_flow": optimized_flow,
            }
        )
        if result is None:
            raise RuntimeError("Document graph produced no state")

        status = result.get("status")
        errors = result.get("errors") or []
        confidence = _dump(result.get("confidence"))
        extraction = _dump(result.get("extraction_validation"))
        validation = _dump(result.get("validation"))
        generation = result.get("generation")
        mapping = result.get("mapping")

        generated = None
        if generation and getattr(generation, "output_path", None):
            generated = Path(generation.output_path)
        elif local_out.exists():
            generated = local_out

        stored_output = str(generated) if generated else str(local_out)
        if generated and generated.exists() and blobs.enabled:
            stored_output = blobs.persist_job_output(
                jid, generated, dest_ref=intended_out_ref
            )
        elif generated and generated.exists() and intended_out_ref and is_blob_ref(
            intended_out_ref
        ):
            raise RuntimeError(
                "Output blob ref was provided but Azure Blob is not configured"
            )

        mapper_llm = (confidence or {}).get("mapper_llm") if confidence else None
        validator_llm = (confidence or {}).get("validator_llm") if confidence else None
        if not mapper_llm and mapping is not None:
            provider = getattr(mapping, "mapper_provider", None)
            model = getattr(mapping, "mapper_model", None)
            if provider and model:
                mapper_llm = f"{provider}/{model}"

        ok = status == "completed" and not errors
        error_msg = None if ok else (
            "; ".join(str(e) for e in errors) if errors else f"Pipeline ended with status={status}"
        )
        timing = _elapsed_fields(started)
        result_snapshot = {
            "job_id": job_id,
            "xid": xid,
            "status": "completed" if ok else "failed",
            "output_path": stored_output,
            "mapper_llm": mapper_llm,
            "validator_llm": validator_llm,
            "scores_pct": (confidence or {}).get("scores_pct") if confidence else None,
            "confidence": confidence,
            "validation": validation,
            "extraction_validation": extraction,
            "marker_detection": result.get("marker_detection"),
            **timing,
        }

        if sink is not None:
            try:
                sink.complete_job(
                    job_id,  # type: ignore[arg-type]
                    output_path=stored_output,
                    confidence=confidence,
                    validation=validation,
                    extraction_validation=extraction,
                    result=result_snapshot,
                    mapper_llm=mapper_llm,
                    validator_llm=validator_llm,
                    error=error_msg,
                    xid=xid,
                )
                db_updated = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Document MCP could not complete job in SQL: %s", exc)
                db_updated = False

        return GenerateDocumentResponse(
            ok=ok,
            mcp="document_process_mcp",
            xid=xid,
            job_id=job_id,
            status="completed" if ok else "failed",
            errors=[str(e) for e in errors],
            error=error_msg,
            output_path=stored_output,
            confidence=confidence,
            extraction_validation=extraction,
            validation=validation,
            mapper_llm=mapper_llm,
            validator_llm=validator_llm,
            db_updated=db_updated,
            elapsed_ms=timing.get("elapsed_ms"),
            elapsed=timing.get("elapsed"),
        )
    except Exception as exc:
        timing = _elapsed_fields(started)
        fail_result = {
            "job_id": job_id,
            "xid": xid,
            "status": "failed",
            "error": str(exc),
            **timing,
        }
        if sink is not None:
            try:
                sink.complete_job(
                    job_id,  # type: ignore[arg-type]
                    error=str(exc),
                    xid=xid,
                    result=fail_result,
                )
                db_updated = True
            except Exception as sink_exc:  # noqa: BLE001
                logger.warning("Document MCP could not fail job in SQL: %s", sink_exc)
        if isinstance(exc, json.JSONDecodeError):
            return GenerateDocumentResponse(
                ok=False,
                mcp="document_process_mcp",
                xid=xid,
                job_id=job_id,
                status="failed",
                errors=[f"invalid data_json: {exc}"],
                error=f"invalid data_json: {exc}",
                output_path=output_path,
                db_updated=db_updated,
                elapsed_ms=timing.get("elapsed_ms"),
                elapsed=timing.get("elapsed"),
            )
        return GenerateDocumentResponse(
            ok=False,
            mcp="document_process_mcp",
            xid=xid,
            job_id=job_id,
            status="failed",
            errors=[str(exc)],
            error=str(exc),
            output_path=output_path,
            db_updated=db_updated,
            elapsed_ms=timing.get("elapsed_ms"),
            elapsed=timing.get("elapsed"),
        )
