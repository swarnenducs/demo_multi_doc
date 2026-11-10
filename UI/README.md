# Gradio UI (local MCP client)

Talks to `document_process_mcp` at `http://127.0.0.1:8001/mcp`.

## Run with MCP

From the repo root (recommended):

```bash
./run_local.sh
```

Or separately:

```bash
# terminal 1 — MCP
./run.sh

# terminal 2 — UI
./UI/run.sh
```

Open http://127.0.0.1:7860

## Notes

- Requires root `.env` with `OPENAI_API_KEY` (or Azure OpenAI vars) for generation.
- Local mode uses SQLite + `FILE_STORAGE_BACKEND=local` (see root `.env.example`).
