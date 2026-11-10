#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" run.py "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 run.py "$@"
fi
exec python run.py "$@"
