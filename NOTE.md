# Change record — local Gradio UI, currency table fill, standalone fixes

**Date:** 2026-11-10  
**Repo:** https://github.com/swarnenducs/demo_multi_doc  
**Commit:** `1a044c6` — *Add local Gradio UI, currency table fill, and standalone path fixes*

This note lists **what changed and why**, for change-control / handover.

---

## Summary

1. Run the document MCP **locally** (SQLite + local files) with a single launcher.
2. Add a **Gradio UI** that calls MCP over HTTP and shows a **full accuracy report** plus downloads.
3. Format product **prices with `$`** in filled Word tables using a per-product `currency` field.
4. Fix **package root paths** so prompts/config resolve correctly in this flat standalone layout.
5. Ship sample GPO/invoice templates + JSON (including currency on GPO products).

---

## New files / folders

| Path | Purpose |
|------|---------|
| `UI/` | Gradio client for local MCP |
| `UI/gradio_app.py` | UI: generate via MCP, accuracy report HTML, `.docx` + accuracy JSON downloads |
| `UI/mcp_client.py` | Thin FastMCP HTTP client (`health`, `generate_document`) |
| `UI/run.py` / `UI/run.sh` | Start Gradio on `:7860` |
| `UI/requirements.txt` | UI-only dep (`gradio`) |
| `UI/README.md` | How to run UI + MCP |
| `run_local.sh` | Start MCP (`:8001`) and Gradio UI together in local mode |
| `.gitignore` | Ignore `.env`, `/data/`, `samples/output/`, `.venv`, caches |
| `samples/data/*.json` | Sample payloads (GPO products include `"currency": "$"`) |
| `samples/templates/*.docx` | Sample Word templates (incl. GPO contract) |
| `tests/test_tiered_tables.py` | Tiered product-table generation tests |
| `uv.lock` | Locked dependency set |
| `NOTE.md` | This change record |

---

## Modified functionality (by area)

### Local run / config

| File | Change |
|------|--------|
| `README.md` | Local setup: venv, `.env`, `./run_local.sh`, MCP/UI ports |
| `.env.example` | Notes for `IPP_FORCE_SQLITE`, `DOCUMENT_MCP_URL`, `GRADIO_*` |
| `src/core/settings.py` | Package root `parents[3]` → `parents[2]` (flat `src/` layout) |
| `src/core/dynaconf_loader.py` | Same root-path fix for `.env` loading |
| `src/services/llm_optimization.py` | Same root-path fix for optimization config |
| `src/services/prompts/loader.py` | Component root `parents[4]` → `parents[3]` so `prompts/` + `config/prompt_versions.json` resolve |
| `src/services/library_match.py` | Repo root fix for `samples/templates` library matching |

### Currency in product tables

| File | Change |
|------|--------|
| `samples/data/gpo_agreement.json` | Each product has `"currency": "$"` |
| `samples/data/gpo_agreement_5_tiers_600_medical_products.json` | Same for all tier products |
| `src/services/document_generator.py` | `_currency_symbol`, `_format_table_cell_value` — price cells render as `$1.25` (USD/`$` → `$`) |
| `src/services/table_fill_infer.py` | Header alias for `currency`; stronger product/price table inference helpers used with generator |
| `src/services/field_mapper.py` | Minor mapper / table-fill merge alignment with inferred fills |
| `prompts/mapper.1.0.0.yml` / `src/services/prompts/yml/mapper.1.0.0.yml` | Prompt tweaks for table_fills / unmarked price tables |
| `tests/test_table_fill_infer.py` | Assert `$` prices in generated table; fix sample path to repo root |

### Gradio UI (accuracy + download)

| File | Change |
|------|--------|
| `UI/gradio_app.py` | Full accuracy report (scores %, validation, per-placeholder/column); download generated `.docx` and `accuracy_report_*.json` |

**Runtime ports**

- MCP: `http://127.0.0.1:8001/mcp`
- UI: `http://127.0.0.1:7860`

---

## Cleanup

- Removed tracked `**/__pycache__/*.pyc` from the tree (should not be versioned).
- Local secrets stay in `.env` (gitignored); never committed.

---

## How to verify locally

```bash
cp .env.example .env   # set OPENAI_API_KEY; keep FILE_STORAGE_BACKEND=local
./run_local.sh
# UI → Generate with GPO template + gpo_agreement.json
# Check PRICE/EA columns show $… and download accuracy report JSON
```

Or MCP-only / UI-only:

```bash
./run.sh
./UI/run.sh
```

---

## Out of scope / not changed

- Azure Web App packaging scripts still present; cloud mode remains env-driven (`FILE_STORAGE_BACKEND=azure_blob`, Azure SQL).
- No permanent change to require Azure OpenAI; local demo uses direct OpenAI via `.env`.
