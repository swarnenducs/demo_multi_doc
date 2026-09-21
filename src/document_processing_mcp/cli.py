"""CLI entrypoint for the Word template → JSON map → styled document pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from document_processing_mcp.graph import invoke_document_graph


def _print_confidence(result: dict) -> None:
    confidence = result.get("confidence")
    mapping = result.get("mapping")
    generation = result.get("generation")
    validation = result.get("validation")
    extraction_validation = result.get("extraction_validation")

    print("\nScores (all in %)")
    if confidence:
        pct = confidence.scores_pct or {}
        if confidence.mapper_llm:
            print(f"  LLM #1 (mapper)                 : {confidence.mapper_llm}")
        if confidence.validator_llm:
            print(f"  LLM #2 (validator)              : {confidence.validator_llm}")
        print(f"  Overall confidence              : {pct.get('overall_confidence_pct', confidence.overall_confidence * 100):.1f}%")
        print(f"  Extraction XML confidence       : {pct.get('extraction_confidence_pct', confidence.extraction_confidence * 100):.1f}%")
        if confidence.extraction_passed is not None:
            print(f"  Extraction validation passed    : {confidence.extraction_passed}")
        if pct.get("extraction_placeholder_detection_pct") is not None:
            print(
                f"  Extraction placeholder detect.  : "
                f"{pct.get('extraction_placeholder_detection_pct', 0):.1f}%"
            )
        if pct.get("extraction_structure_pct") is not None:
            print(f"  Extraction structure            : {pct.get('extraction_structure_pct', 0):.1f}%")
        print(f"  Placeholder mapping (LLM #1)    : {pct.get('placeholder_mapping_confidence_pct', confidence.mapping_confidence * 100):.1f}%")
        print(f"  Placeholder coverage            : {pct.get('placeholder_coverage_pct', confidence.coverage_score * 100):.1f}%")
        print(f"  Table mapping (LLM #1)          : {pct.get('table_mapping_confidence_pct', confidence.table_mapping_confidence * 100):.1f}%")
        print(f"  Generation integrity            : {pct.get('generation_integrity_pct', confidence.generation_integrity * 100):.1f}%")
        print(f"  Document validation (LLM #2)    : {pct.get('validation_score_pct', confidence.validation_score * 100):.1f}%")
        if confidence.validation_passed is not None:
            print(f"  Validation passed               : {confidence.validation_passed}")
        if confidence.notes:
            print(f"  notes                           : {confidence.notes}")

        per_ph = pct.get("per_placeholder") or []
        if per_ph:
            print("  Per-placeholder mapping confidence:")
            for item in per_ph[:15]:
                print(
                    f"    - {item.get('placeholder')}: {item.get('confidence_pct', 0):.1f}% "
                    f"← {item.get('json_path')}"
                )
            if len(per_ph) > 15:
                print(f"    … {len(per_ph) - 15} more")

        per_col = pct.get("per_table_column") or []
        if per_col:
            print("  Per-table-column mapping confidence:")
            for item in per_col[:15]:
                print(
                    f"    - [{item.get('array_json_path')}] {item.get('header')} → "
                    f"{item.get('json_field')}: {item.get('confidence_pct', 0):.1f}%"
                )
            if len(per_col) > 15:
                print(f"    … {len(per_col) - 15} more")
    else:
        if mapping:
            print(f"  Placeholder mapping : {mapping.mapping_confidence * 100:.1f}%")
            print(f"  Placeholder coverage: {mapping.coverage_score * 100:.1f}%")
        if generation:
            print(f"  Generation confidence: {generation.generation_confidence * 100:.1f}%")

    if extraction_validation:
        print("\nExtraction XML validation (LLM critic)")
        print(f"  passed             : {extraction_validation.passed}")
        print(f"  extraction conf.   : {extraction_validation.extraction_confidence * 100:.1f}%")
        print(
            f"  placeholder detect.: "
            f"{extraction_validation.placeholder_detection_confidence * 100:.1f}%"
        )
        print(f"  structure conf.    : {extraction_validation.structure_confidence * 100:.1f}%")
        if extraction_validation.summary:
            print(f"  summary            : {extraction_validation.summary}")
        if extraction_validation.missed_placeholder_suspects:
            print(f"  missed suspects    : {extraction_validation.missed_placeholder_suspects}")
        if extraction_validation.issues:
            print("  issues:")
            for issue in extraction_validation.issues:
                field = f" [{issue.field}]" if issue.field else ""
                print(f"    - ({issue.severity}){field} {issue.message}")

    if validation:
        print("\nValidation detail (LLM #2)")
        print(f"  passed             : {validation.passed}")
        print(f"  score              : {validation.validation_score * 100:.1f}%")
        if validation.validator_provider and validation.validator_model:
            print(f"  provider / model   : {validation.validator_provider} / {validation.validator_model}")
        else:
            print(f"  source             : {validation.validator_source}")
        if validation.summary:
            print(f"  summary            : {validation.summary}")
        if validation.issues:
            print("  issues:")
            for issue in validation.issues:
                field = f" [{issue.field}]" if issue.field else ""
                print(f"    - ({issue.severity}){field} {issue.message}")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    from document_processing_mcp.flow_debug import install_flow_logger

    install_flow_logger()

    parser = argparse.ArgumentParser(
        description=(
            "LangGraph agentic flow: extract Word XML styles, map JSON data, "
            "generate a styled .docx, validate with a critic LLM, and score confidence"
        )
    )
    parser.add_argument("--template", required=True, help="Path to the Word .docx template")
    parser.add_argument("--data", required=True, help="Path to JSON data file")
    parser.add_argument(
        "--output",
        required=True,
        help="Path for the generated .docx output",
    )
    parser.add_argument(
        "--dump-extraction",
        default=None,
        help="Optional path to write extracted styles/placeholders as JSON",
    )
    parser.add_argument(
        "--dump-mapping",
        default=None,
        help="Optional path to write field mapping result as JSON",
    )
    parser.add_argument(
        "--dump-confidence",
        default=None,
        help="Optional path to write confidence + validation report as JSON",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip the document validator step (still computes mapping/generation confidence)",
    )
    parser.add_argument(
        "--skip-extraction-validation",
        action="store_true",
        help="Skip the LLM critic on extracted Word XML / placeholders",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Retries of map→generate when validation fails (default: DOCUMENT_MAX_RETRIES)",
    )
    parser.add_argument(
        "--validation-threshold",
        type=float,
        default=None,
        help="Minimum validation_score to accept without retry (default: DOCUMENT_VALIDATION_THRESHOLD)",
    )
    parser.add_argument(
        "--optimized-flow",
        action="store_true",
        help="Use llm_optimization.json mapper cascade (off by default)",
    )
    parser.add_argument(
        "--fail-on-validation",
        action="store_true",
        help="Exit non-zero if validation did not pass",
    )
    parser.add_argument(
        "--mapper-model-id",
        default=None,
        help="Override mapper via LangChain init_chat_model id, e.g. openai:gpt-4o-mini",
    )
    parser.add_argument(
        "--validator-model-id",
        default=None,
        help="Override validator via LangChain init_chat_model id, e.g. groq:openai/gpt-oss-120b",
    )
    args = parser.parse_args(argv)

    from document_processing_mcp.core.settings import settings as mcp_settings

    quality = mcp_settings()
    use_opt = args.optimized_flow or quality.document_llm_optimization_enabled
    result = invoke_document_graph(
        {
            "template_path": str(Path(args.template).resolve()),
            "data_path": str(Path(args.data).resolve()),
            "output_path": str(Path(args.output).resolve()),
            "errors": [],
            "status": "started",
            "retry_count": 0,
            "max_retries": (
                args.max_retries
                if use_opt
                else (
                    quality.document_max_retries
                    if args.max_retries is None
                    else args.max_retries
                )
            ),
            "validation_threshold": (
                args.validation_threshold
                if use_opt
                else (
                    quality.document_validation_threshold
                    if args.validation_threshold is None
                    else args.validation_threshold
                )
            ),
            "skip_validation": args.skip_validation,
            "skip_extraction_validation": args.skip_extraction_validation,
            "optimized_flow": use_opt,
        },
        mapper_model_id=args.mapper_model_id,
        validator_model_id=args.validator_model_id,
    )

    if args.dump_extraction and result.get("extracted"):
        dump_path = Path(args.dump_extraction)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        payload = result["extracted"].model_dump()
        payload.pop("styles_xml", None)
        payload.pop("document_xml", None)
        payload.pop("numbering_xml", None)
        dump_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote extraction dump → {dump_path}")

    if args.dump_mapping and result.get("mapping"):
        dump_path = Path(args.dump_mapping)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(
            json.dumps(result["mapping"].model_dump(), indent=2),
            encoding="utf-8",
        )
        print(f"Wrote mapping dump → {dump_path}")

    if args.dump_confidence:
        dump_path = Path(args.dump_confidence)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "confidence": result["confidence"].model_dump() if result.get("confidence") else None,
            "extraction_validation": (
                result["extraction_validation"].model_dump()
                if result.get("extraction_validation")
                else None
            ),
            "validation": result["validation"].model_dump() if result.get("validation") else None,
            "generation": {
                "generation_confidence": result["generation"].generation_confidence,
                "integrity_score": result["generation"].integrity_score,
                "leftover_placeholders": result["generation"].leftover_placeholders,
            }
            if result.get("generation")
            else None,
        }
        dump_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote confidence dump → {dump_path}")

    status = result.get("status")
    errors = result.get("errors") or []
    if status != "completed" or errors:
        print("Pipeline failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    generation = result.get("generation")
    mapping = result.get("mapping")
    extracted = result.get("extracted")

    print("Pipeline completed successfully")
    if extracted:
        print(f"  styles found      : {len(extracted.styles)}")
        print(f"  content blocks    : {len(extracted.blocks)}")
        print(f"  placeholders      : {extracted.placeholders}")
    if mapping:
        print(f"  mappings applied  : {len(mapping.mappings)}")
        if mapping.mapper_provider and mapping.mapper_model:
            print(f"  LLM #1 (mapper)   : {mapping.mapper_provider} / {mapping.mapper_model}")
        else:
            print(f"  mapper source     : {mapping.mapper_source}")
        if mapping.unmapped_placeholders:
            print(f"  unmapped fields   : {mapping.unmapped_placeholders}")
    if generation:
        print(f"  output            : {generation.output_path}")
        print(f"  replacements      : {generation.applied_mappings}")
        print(f"  {generation.message}")
    print(f"  retries used      : {result.get('retry_count', 0)}")

    _print_confidence(result)

    validation = result.get("validation")
    if args.fail_on_validation and validation is not None and not validation.passed:
        print("Validation did not pass (--fail-on-validation).", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
