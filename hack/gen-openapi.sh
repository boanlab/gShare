#!/usr/bin/env bash
set -euo pipefail
# Dumps the backend's FastAPI OpenAPI document into frontend/openapi.json, then regenerates the
# frontend's API types at frontend/src/api/schema.d.ts with openapi-typescript.
#
# Re-run this whenever a backend route or schema changes, to keep the frontend types in step.
# Everything happens inside containers, so no .venv or node_modules is left on the host.
#
# Usage: make gen-openapi, or run hack/gen-openapi.sh directly.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCKER="${DOCKER:-docker}"
BACKEND_IMG="gshare-openapi-backend:tmp"
FE_GEN_IMG="gshare-openapi-fe:tmp"
OPENAPI_JSON="${ROOT}/frontend/openapi.json"
SCHEMA_DTS="${ROOT}/frontend/src/api/schema.d.ts"

cleanup() { "$DOCKER" rmi -f "$BACKEND_IMG" "$FE_GEN_IMG" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "[gen-openapi] 1/3 building the backend image"
"$DOCKER" build -q -t "$BACKEND_IMG" "${ROOT}/backend" >/dev/null

echo "[gen-openapi] 2/3 dumping app.openapi() into frontend/openapi.json"
# create_app() only registers the routers; the database connection is opened in the lifespan, so the
# OpenAPI dump needs no database.
"$DOCKER" run --rm "$BACKEND_IMG" \
  python -c "import json, app.main as m; print(json.dumps(m.create_app().openapi()))" \
  > "$OPENAPI_JSON"
echo "    paths: $(python3 -c "import json;print(len(json.load(open('${OPENAPI_JSON}'))['paths']))")"

echo "[gen-openapi] 3/3 openapi-typescript → frontend/src/api/schema.d.ts"
# The frontend's deps stage runs 'npm run gen:api' when openapi.json is present; see its Dockerfile.
"$DOCKER" build -q --target deps -t "$FE_GEN_IMG" "${ROOT}/frontend" >/dev/null
cid="$("$DOCKER" create "$FE_GEN_IMG")"
"$DOCKER" cp "$cid:/app/src/api/schema.d.ts" "$SCHEMA_DTS"   # docker cp writes it as the host user
"$DOCKER" rm -f "$cid" >/dev/null

echo "[gen-openapi] done: openapi.json and schema.d.ts are up to date."
echo "  Domain type aliases live in frontend/src/api/types.ts, layered over components.schemas."
