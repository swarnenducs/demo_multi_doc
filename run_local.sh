#!/usr/bin/env bash
# Start document_process_mcp (:8001) + Gradio UI (:7860) for local SQLite/file mode.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — set OPENAI_API_KEY before generating docs."
fi

# Force local storage even if Azure vars are present in the shell.
export IPP_FORCE_SQLITE=1
export FILE_STORAGE_BACKEND=local
export DOCUMENT_MCP_HOST="${DOCUMENT_MCP_HOST:-127.0.0.1}"
export DOCUMENT_MCP_PORT="${DOCUMENT_MCP_PORT:-8001}"
export DOCUMENT_MCP_TRANSPORT="${DOCUMENT_MCP_TRANSPORT:-http}"
export DOCUMENT_MCP_URL="${DOCUMENT_MCP_URL:-http://127.0.0.1:8001/mcp}"
export GRADIO_HOST="${GRADIO_HOST:-127.0.0.1}"
export GRADIO_PORT="${GRADIO_PORT:-7860}"

PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Missing .venv — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt -r UI/requirements.txt"
  exit 1
fi

mkdir -p data/storage/jobs data/ui_uploads

cleanup() {
  if [[ -n "${MCP_PID:-}" ]] && kill -0 "$MCP_PID" 2>/dev/null; then
    kill "$MCP_PID" 2>/dev/null || true
    wait "$MCP_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "Starting MCP on http://${DOCUMENT_MCP_HOST}:${DOCUMENT_MCP_PORT}/mcp ..."
"$PY" run.py --transport http --host "$DOCUMENT_MCP_HOST" --port "$DOCUMENT_MCP_PORT" &
MCP_PID=$!

# Wait until MCP accepts connections (or fail after ~30s).
for _ in $(seq 1 60); do
  if ! kill -0 "$MCP_PID" 2>/dev/null; then
    echo "MCP process exited early."
    exit 1
  fi
  if "$PY" - <<'PY' 2>/dev/null; then
import os, socket
host = os.environ.get("DOCUMENT_MCP_HOST", "127.0.0.1")
port = int(os.environ.get("DOCUMENT_MCP_PORT", "8001"))
s = socket.socket()
s.settimeout(0.3)
try:
    s.connect((host, port))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
PY
    break
  fi
  sleep 0.5
else
  echo "Timed out waiting for MCP on ${DOCUMENT_MCP_HOST}:${DOCUMENT_MCP_PORT}"
  exit 1
fi

echo "MCP ready. Starting Gradio UI on http://${GRADIO_HOST}:${GRADIO_PORT} ..."
echo "Press Ctrl+C to stop both."
"$PY" UI/run.py
