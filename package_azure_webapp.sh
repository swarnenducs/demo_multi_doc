#!/usr/bin/env bash
# Zip THIS folder for Azure Web App zip-deploy.
# Works if this component is later split into its own repo.
#
#   ./package_azure_webapp.sh
#   ./package_azure_webapp.sh /tmp/ip-api.zip
#
# Deploy:
#   az webapp deploy -g <rg> -n <app> --src-path dist/azure-webapp.zip --type zip
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPONENT_LABEL="document-processing-mcp"
WEBSITES_PORT="8001"
STARTUP="python run.py"
STARTUP_ALT="python run.py --transport http --host 0.0.0.0 --port 8001"
OUT="${1:-$ROOT/dist/azure-webapp.zip}"
if [[ "$OUT" != /* ]]; then
  OUT="$PWD/$OUT"
fi

STAGE="$ROOT/dist/.webapp-stage"
rm -rf "$STAGE"
mkdir -p "$STAGE" "$(dirname "$OUT")"
trap 'rm -rf "$STAGE"' EXIT

for required_path in .env.example Dockerfile pyproject.toml README.md requirements.txt run.py src prompts config; do
  if [[ ! -e "$ROOT/$required_path" ]]; then
    echo "Missing required deployment input: $required_path" >&2
    exit 1
  fi
done

if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude '.mypy_cache/' \
    --exclude '.ruff_cache/' \
    --exclude 'htmlcov/' \
    --exclude 'dist/' \
    --exclude 'build/' \
    --exclude 'tests/' \
    --exclude 'data/' \
    --exclude '.env' \
    --exclude '*.egg-info/' \
    --exclude '.DS_Store' \
    "$ROOT/" "$STAGE/"
else
  tar -C "$ROOT" \
    --exclude '.git' --exclude '.venv' --exclude 'venv' --exclude '__pycache__' \
    --exclude '.pytest_cache' --exclude 'dist' --exclude 'build' --exclude 'tests' \
    --exclude 'data' --exclude '.env' --exclude '*.egg-info' \
    -cf - . | tar -C "$STAGE" -xf -
fi

cat > "$STAGE/.deployment" <<'EOF'
[config]
SCM_DO_BUILD_DURING_DEPLOYMENT=true
EOF

cat > "$STAGE/STARTUP.txt" <<EOF
Azure Web App zip — ${COMPONENT_LABEL}
This zip is only this component (safe to move to its own repo).

WEBSITES_PORT=${WEBSITES_PORT}
SCM_DO_BUILD_DURING_DEPLOYMENT=true  (also in .deployment)

Startup command:
  ${STARTUP}

Alternate:
  ${STARTUP_ALT}

requirements.txt contains the pinned runtime dependencies for the flat src layout.
Do not put .env in the zip. Use App settings + Key Vault references.
EOF

rm -f "$OUT"
if command -v zip >/dev/null 2>&1; then
  (cd "$STAGE" && zip -r -q "$OUT" .)
else
  python3 - "$STAGE" "$OUT" <<'PY'
import shutil, sys
from pathlib import Path
stage, out = Path(sys.argv[1]), Path(sys.argv[2])
base = out.with_suffix("")
shutil.make_archive(str(base), "zip", root_dir=stage)
PY
fi
echo "Wrote $OUT"
echo "Deploy: az webapp deploy -g <rg> -n <app> --src-path $OUT --type zip"
echo "Startup: $STARTUP   WEBSITES_PORT=$WEBSITES_PORT"
