"""Gradio UI for local document_process_mcp (HTTP :8001)."""

from __future__ import annotations

import html
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import gradio as gr
from dotenv import load_dotenv

from mcp_client import call_mcp_tool_sync, default_mcp_url, health_sync

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
UPLOAD_ROOT = ROOT / "data" / "ui_uploads"
OUTPUT_ROOT = ROOT / "data" / "storage" / "jobs"

SAMPLE_TEMPLATES = {
    "complete_contract_template_GPO.docx": (
        SAMPLES / "templates" / "complete_contract_template_GPO.docx"
    ),
    "invoice_template.docx": SAMPLES / "templates" / "invoice_template.docx",
    "contract_template.docx": SAMPLES / "templates" / "contract_template.docx",
}

SAMPLE_DATA = {
    "gpo_agreement.json": SAMPLES / "data" / "gpo_agreement.json",
    "invoice.json": SAMPLES / "data" / "invoice.json",
    "contract_full.json": SAMPLES / "data" / "contract_full.json",
    "contract_products.json": SAMPLES / "data" / "contract_products.json",
}


def _resolve_gradio_path(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list) and value:
        return _resolve_gradio_path(value[0])
    if isinstance(value, dict):
        path = value.get("path") or value.get("name")
        return str(path) if path else None
    path_attr = getattr(value, "path", None) or getattr(value, "name", None)
    if path_attr:
        return str(path_attr)
    return str(value)


def _copy_into_work(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest.resolve()


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _as_pct(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    # Accept 0-1 or already 0-100
    if 0.0 <= num <= 1.0:
        return num * 100.0
    return num


def _score_badge(pct: float | None, *, strong: bool = False) -> str:
    if pct is None:
        return "<code>-</code>"
    if pct < 80:
        color = "#b91c1c"
    elif pct < 90:
        color = "#b45309"
    else:
        color = "#15803d"
    weight = "700" if strong else "500"
    return (
        f'<span style="color:{color};font-weight:{weight}">{pct:.1f}%</span>'
    )


def _html_table(title: str, headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(
        f'<th style="padding:0.45rem 0.6rem;text-align:left;border-bottom:1px solid #d4d4d4">'
        f"{_esc(h)}</th>"
        for h in headers
    )
    body = []
    for i, row in enumerate(rows):
        border = "border-bottom:1px solid #ececec" if i < len(rows) - 1 else ""
        cells = "".join(
            f'<td style="padding:0.45rem 0.6rem;vertical-align:top;{border}">{c}</td>'
            for c in row
        )
        body.append(f"<tr>{cells}</tr>")
    return f"""
<section style="margin:0 0 1.1rem 0">
  <h3 style="margin:0 0 0.45rem 0;font-size:1.05rem">{_esc(title)}</h3>
  <div style="overflow:auto;max-width:100%;border:1px solid #e5e5e5;border-radius:8px">
  <table style="width:100%;border-collapse:collapse;font-size:0.92rem;background:#fff">
    <thead style="background:#fafafa"><tr>{head}</tr></thead>
    <tbody>{''.join(body)}</tbody>
  </table>
  </div>
</section>
"""


def _empty_ui(msg: str) -> tuple[str, str, str | None, str | None, str]:
    return (
        f"<div style='padding:0.75rem;border:1px solid #fecaca;background:#fef2f2;"
        f"border-radius:8px;color:#991b1b'>{_esc(msg)}</div>",
        msg,
        None,
        None,
        "{}",
    )


def build_accuracy_report_html(result: dict[str, Any]) -> str:
    """Render MCP confidence + validation into a full accuracy report panel."""
    conf = result.get("confidence") if isinstance(result.get("confidence"), dict) else {}
    pct = conf.get("scores_pct") if isinstance(conf.get("scores_pct"), dict) else {}
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    extraction = (
        result.get("extraction_validation")
        if isinstance(result.get("extraction_validation"), dict)
        else {}
    )

    def p(key: str, *, strong: bool = False) -> str:
        return _score_badge(_as_pct(pct.get(key)), strong=strong)

    ok = bool(result.get("ok"))
    status = _esc(result.get("status") or ("ok" if ok else "failed"))
    status_color = "#15803d" if ok else "#b91c1c"
    output_path = result.get("output_path") or ""
    download_link = ""
    if output_path and Path(str(output_path)).exists():
        download_link = (
            f"<p style='margin:0.4rem 0 0 0'>Generated file: "
            f"<code>{_esc(output_path)}</code><br/>"
            f"Use the <strong>Generated .docx</strong> download below.</p>"
        )

    banner = f"""
<div style="margin:0 0 1rem 0;padding:0.85rem 1rem;border:1px solid #e5e5e5;border-radius:10px;background:#f8fafc">
  <div style="display:flex;gap:1rem;flex-wrap:wrap;align-items:baseline">
    <div>
      <div style="opacity:0.7;font-size:0.8rem">Status</div>
      <div style="font-size:1.25rem;font-weight:700;color:{status_color}">{status}</div>
    </div>
    <div>
      <div style="opacity:0.7;font-size:0.8rem">Overall time</div>
      <div style="font-size:1.25rem;font-weight:700">{_esc(result.get('elapsed') or result.get('elapsed_ms') or 'n/a')}</div>
    </div>
    <div>
      <div style="opacity:0.7;font-size:0.8rem">Overall confidence</div>
      <div style="font-size:1.25rem">{p('overall_confidence_pct', strong=True)}</div>
    </div>
  </div>
  {download_link}
</div>
"""

    errors = list(result.get("errors") or [])
    if result.get("error"):
        errors = [result["error"], *errors]
    error_block = ""
    if errors:
        items = "".join(f"<li>{_esc(e)}</li>" for e in errors)
        error_block = (
            "<div style='margin:0 0 1rem 0;padding:0.75rem;border:1px solid #fecaca;"
            "background:#fef2f2;border-radius:8px;color:#991b1b'>"
            f"<strong>Errors</strong><ul style='margin:0.35rem 0 0 1.1rem'>{items}</ul></div>"
        )

    sections = [
        banner,
        error_block,
        _html_table(
            "Job",
            ["Field", "Value"],
            [
                ["Job ID", f"<code>{_esc(result.get('job_id'))}</code>"],
                ["xid", f"<code>{_esc(result.get('xid'))}</code>"],
                ["MCP", f"<code>{_esc(result.get('mcp') or 'document_process_mcp')}</code>"],
                ["Mapper LLM", f"<code>{_esc(result.get('mapper_llm') or conf.get('mapper_llm') or '-')}</code>"],
                [
                    "Validator LLM",
                    f"<code>{_esc(result.get('validator_llm') or conf.get('validator_llm') or '-')}</code>",
                ],
                ["Output path", f"<code>{_esc(output_path or '-')}</code>"],
                ["DB updated", f"<code>{_esc(result.get('db_updated'))}</code>"],
            ],
        ),
        _html_table(
            "Scores (all in %)",
            ["Metric", "Score"],
            [
                ["Overall confidence", p("overall_confidence_pct", strong=True)],
                ["Extraction confidence", p("extraction_confidence_pct")],
                ["Extraction placeholder detection", p("extraction_placeholder_detection_pct")],
                ["Extraction structure", p("extraction_structure_pct")],
                ["Placeholder mapping (LLM #1)", p("placeholder_mapping_confidence_pct")],
                ["Placeholder coverage", p("placeholder_coverage_pct")],
                ["Table mapping (LLM #1)", p("table_mapping_confidence_pct")],
                ["Generation integrity", p("generation_integrity_pct")],
                ["Generation confidence", p("generation_confidence_pct")],
                ["Document validation (LLM #2)", p("validation_score_pct", strong=True)],
            ],
        ),
        "<p style='margin:0 0 1rem 0;font-size:0.85rem;opacity:0.85'>"
        "Score colors: <span style='color:#b91c1c'>red &lt; 80%</span> · "
        "amber 80–89.9% · <span style='color:#15803d'>green ≥ 90%</span></p>",
    ]

    if validation:
        sections.append(
            _html_table(
                "Validation detail (LLM #2)",
                ["Field", "Value"],
                [
                    ["Passed", f"<code>{_esc(validation.get('passed'))}</code>"],
                    ["Score", p("validation_score_pct")],
                    ["Summary", _esc(validation.get("summary") or "-")],
                ],
            )
        )
        issues = validation.get("issues") or []
        if issues:
            issue_rows = [
                [
                    _esc(issue.get("severity") or "-"),
                    f"<code>{_esc(issue.get('field') or '')}</code>",
                    _esc(issue.get("message") or ""),
                ]
                for issue in issues
                if isinstance(issue, dict)
            ]
            sections.append(
                _html_table(
                    "Validation issues (LLM #2)",
                    ["Severity", "Field", "Message"],
                    issue_rows,
                )
            )

    if extraction:
        missed = extraction.get("missed_placeholder_suspects") or []
        missed_txt = ", ".join(str(x) for x in missed) if missed else "-"
        sections.append(
            _html_table(
                "Extraction critic",
                ["Field", "Value"],
                [
                    ["Passed", f"<code>{_esc(extraction.get('passed'))}</code>"],
                    ["Summary", _esc(extraction.get("summary") or "-")],
                    ["Missed placeholders", _esc(missed_txt)],
                ],
            )
        )

    per_ph = pct.get("per_placeholder") or []
    if per_ph:
        rows = [
            [
                f"<code>{_esc(item.get('placeholder'))}</code>",
                f"<code>{_esc(item.get('json_path'))}</code>",
                _score_badge(_as_pct(item.get("confidence_pct"))),
            ]
            for item in per_ph[:40]
            if isinstance(item, dict)
        ]
        sections.append(
            _html_table(
                "Per-placeholder mapping confidence",
                ["Placeholder", "JSON path", "Confidence"],
                rows,
            )
        )

    per_col = pct.get("per_table_column") or []
    if per_col:
        rows = [
            [
                f"<code>{_esc(item.get('array_json_path') or '')}</code>",
                _esc(item.get("header") or ""),
                f"<code>{_esc(item.get('json_field') or '')}</code>",
                _score_badge(_as_pct(item.get("confidence_pct"))),
            ]
            for item in per_col[:60]
            if isinstance(item, dict)
        ]
        sections.append(
            _html_table(
                "Per-table-column mapping confidence",
                ["Array", "Header", "JSON field", "Confidence"],
                rows,
            )
        )

    if conf.get("notes"):
        sections.append(
            f"<section style='margin:0 0 1rem 0'><h3 style='margin:0 0 0.4rem 0;"
            f"font-size:1.05rem'>Notes</h3><p>{_esc(conf.get('notes'))}</p></section>"
        )

    return (
        "<div style='font-family:ui-sans-serif,system-ui,sans-serif;line-height:1.4'>"
        + "".join(sections)
        + "</div>"
    )


def ui_check_health(mcp_url: str) -> str:
    payload = health_sync(url=(mcp_url or "").strip() or None)
    return json.dumps(payload, indent=2)


def ui_load_sample_json(sample_name: str) -> str:
    path = SAMPLE_DATA.get((sample_name or "").strip())
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def ui_generate(
    mcp_url: str,
    template_source: str,
    sample_template: str,
    template_file: object | None,
    json_file: object | None,
    json_text: str,
    skip_validation: bool,
    skip_extraction_validation: bool,
    optimized_flow: bool,
):
    url = (mcp_url or "").strip() or default_mcp_url()
    work_id = uuid.uuid4().hex[:12]
    work_dir = UPLOAD_ROOT / work_id
    work_dir.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    source = (template_source or "sample").strip().lower()
    if source.startswith("upload"):
        uploaded = _resolve_gradio_path(template_file)
        if not uploaded:
            return _empty_ui("Upload a .docx template, or switch to Sample template.")
        tpl_src = Path(uploaded)
        if not tpl_src.exists() or tpl_src.suffix.lower() != ".docx":
            return _empty_ui("Template must be an existing .docx file.")
        template_path = _copy_into_work(tpl_src, work_dir / tpl_src.name)
    else:
        sample = SAMPLE_TEMPLATES.get((sample_template or "").strip())
        if not sample or not sample.exists():
            return _empty_ui("Pick a sample template from the list.")
        template_path = _copy_into_work(sample, work_dir / sample.name)

    data_path: Path | None = None
    data_json: str | None = None
    uploaded_json = _resolve_gradio_path(json_file)
    text = (json_text or "").strip()
    if uploaded_json:
        jp = Path(uploaded_json)
        if not jp.exists():
            return _empty_ui(f"JSON file not found: {jp}")
        data_path = _copy_into_work(jp, work_dir / jp.name)
    elif text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            return _empty_ui(f"Invalid JSON text: {exc}")
        if not isinstance(parsed, dict):
            return _empty_ui("JSON root must be an object.")
        data_json = json.dumps(parsed)
        data_path = work_dir / "data.json"
        data_path.write_text(data_json, encoding="utf-8")
    else:
        return _empty_ui("Provide JSON via upload or paste text.")

    out_path = (OUTPUT_ROOT / work_id / f"generated_{template_path.stem}.docx").resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    args: dict[str, Any] = {
        "template_path": str(template_path),
        "output_path": str(out_path),
        "job_id": work_id,
        "skip_validation": bool(skip_validation),
        "skip_extraction_validation": bool(skip_extraction_validation),
        "optimized_flow": bool(optimized_flow),
    }
    if data_json is not None:
        args["data_json"] = data_json
    else:
        args["data_path"] = str(data_path)

    try:
        result = call_mcp_tool_sync("generate_document", args, url=url, timeout=600.0)
    except Exception as exc:  # noqa: BLE001
        return _empty_ui(f"MCP call failed: {exc}")

    if not isinstance(result, dict):
        return _empty_ui(f"Unexpected MCP payload: {result}")

    # Persist full accuracy report next to the generated doc for download.
    report_path = out_path.parent / f"accuracy_report_{work_id}.json"
    report_payload = {
        "job_id": result.get("job_id") or work_id,
        "xid": result.get("xid"),
        "status": result.get("status"),
        "ok": result.get("ok"),
        "elapsed": result.get("elapsed") or result.get("elapsed_ms"),
        "mapper_llm": result.get("mapper_llm"),
        "validator_llm": result.get("validator_llm"),
        "output_path": result.get("output_path"),
        "confidence": result.get("confidence"),
        "extraction_validation": result.get("extraction_validation"),
        "validation": result.get("validation"),
        "errors": result.get("errors") or [],
        "error": result.get("error"),
    }
    report_path.write_text(json.dumps(report_payload, indent=2, default=str), encoding="utf-8")

    download = None
    generated = result.get("output_path") or str(out_path)
    gp = Path(str(generated))
    if gp.exists() and gp.suffix.lower() == ".docx":
        download = str(gp)

    status_lines = [
        f"ok: {result.get('ok')}",
        f"status: {result.get('status')}",
        f"job_id: {result.get('job_id') or work_id}",
        f"xid: {result.get('xid')}",
        f"elapsed: {result.get('elapsed') or result.get('elapsed_ms')}",
        f"mapper_llm: {result.get('mapper_llm')}",
        f"validator_llm: {result.get('validator_llm')}",
        f"output_path: {result.get('output_path')}",
        f"accuracy_report: {report_path}",
    ]
    if download:
        status_lines.append(f"download: {download}")

    return (
        build_accuracy_report_html(result),
        "\n".join(status_lines),
        download,
        str(report_path) if report_path.exists() else None,
        json.dumps(result, indent=2, default=str),
    )


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Document MCP — Local") as demo:
        gr.Markdown(
            "# Document Processing MCP (local)\n"
            "Talks to `document_process_mcp` over HTTP. "
            "After generate, this page shows the **full accuracy report** and "
            "download links for the filled `.docx` + accuracy JSON."
        )
        with gr.Row():
            mcp_url = gr.Textbox(
                label="MCP URL",
                value=default_mcp_url(),
                scale=3,
            )
            health_btn = gr.Button("Check MCP health", scale=1)
        health_out = gr.Code(label="Health response", language="json")
        health_btn.click(ui_check_health, inputs=[mcp_url], outputs=[health_out])

        gr.Markdown("## Generate document")
        with gr.Row():
            template_source = gr.Radio(
                choices=["Sample template", "Upload template"],
                value="Sample template",
                label="Template source",
            )
            sample_template = gr.Dropdown(
                choices=list(SAMPLE_TEMPLATES.keys()),
                value="complete_contract_template_GPO.docx",
                label="Sample template",
            )
            template_file = gr.File(label="Upload .docx", file_types=[".docx"])

        with gr.Row():
            sample_json = gr.Dropdown(
                choices=list(SAMPLE_DATA.keys()),
                value="gpo_agreement.json",
                label="Load sample JSON into editor",
            )
            load_json_btn = gr.Button("Load sample JSON")
            json_file = gr.File(label="Or upload JSON", file_types=[".json"])

        json_text = gr.Code(label="JSON data", language="json", lines=16)
        load_json_btn.click(ui_load_sample_json, inputs=[sample_json], outputs=[json_text])

        with gr.Row():
            skip_validation = gr.Checkbox(
                label="Skip document validation (LLM #2)",
                value=False,
            )
            skip_extraction = gr.Checkbox(
                label="Skip extraction validation",
                value=False,
            )
            optimized_flow = gr.Checkbox(label="Optimized LLM flow", value=False)

        run_btn = gr.Button("Generate via MCP", variant="primary")

        gr.Markdown("## Accuracy report")
        report_html = gr.HTML(label="Accuracy report")
        status_out = gr.Textbox(label="Status / paths", lines=8)

        with gr.Row():
            download_out = gr.File(label="Download generated .docx")
            accuracy_out = gr.File(label="Download accuracy report (.json)")

        raw_out = gr.Code(label="Raw MCP response", language="json")

        run_btn.click(
            ui_generate,
            inputs=[
                mcp_url,
                template_source,
                sample_template,
                template_file,
                json_file,
                json_text,
                skip_validation,
                skip_extraction,
                optimized_flow,
            ],
            outputs=[report_html, status_out, download_out, accuracy_out, raw_out],
        )

        demo.load(ui_load_sample_json, inputs=[sample_json], outputs=[json_text])
        demo.load(ui_check_health, inputs=[mcp_url], outputs=[health_out])

    return demo


def main() -> None:
    load_dotenv(ROOT / ".env")
    # Direct OpenAI must not inherit Azure Foundry base URL.
    for key in (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_VALIDATOR_DEPLOYMENT",
    ):
        # Only clear if .env has them commented / unset after load_dotenv already ran.
        # Keep whatever dotenv loaded; commenting in .env is enough for fresh loads.
        pass
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    host = os.getenv("GRADIO_HOST", "127.0.0.1")
    port = int(os.getenv("GRADIO_PORT", "7860"))
    demo = build_ui()
    print(f"Gradio UI → http://{host}:{port}")
    print(f"MCP target → {default_mcp_url()}")
    demo.launch(server_name=host, server_port=port, share=False)


if __name__ == "__main__":
    main()
