# document-processing-mcp

Document LangGraph pipeline as FastMCP (`:8001`). Local mode = SQLite + files; Azure mode = SQL + Blob (env only).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt -r UI/requirements.txt
cp .env.example .env   # set OPENAI_API_KEY; keep FILE_STORAGE_BACKEND=local
./run_local.sh         # MCP :8001 + Gradio UI :7860

# MCP only:  ./run.sh
# UI only:   ./UI/run.sh
# tests:     .venv/bin/pytest
```
