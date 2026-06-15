#!/usr/bin/env bash
set -euo pipefail
# Builds and deploys the console: docker build and push, serving the SPA from nginx with /api proxied.
# Requires docker logged in to a registry, kubectl, ingress-nginx, the backend stack, and seed.sql.
# Override the image coordinates with REGISTRY, ORG, and TAG.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NS=gshare-system
FE="${REPO_ROOT}/frontend"
REGISTRY="${REGISTRY:-docker.io}"; ORG="${ORG:-boanlab}"; TAG="${TAG:-latest}"
IMG="${REGISTRY}/${ORG}/gshare-frontend:${TAG}"

# Generate the lockfile in a node container when it is missing.
if [[ ! -f "${FE}/package-lock.json" ]]; then
  echo "[fe] generating package-lock.json in a node container"
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "${FE}:/app" -w /app node:20-alpine \
    npm install --package-lock-only --no-audit --no-fund
fi

echo "[fe] build and push ${IMG}. Build-time variables come from frontend/.env; the login method is the backend's GSHARE_AUTH_ALLOW_LOCAL_PASSWORD"
make -C "${REPO_ROOT}" image-frontend REGISTRY="${REGISTRY}" ORG="${ORG}" TAG="${TAG}"
docker push "${IMG}"

echo "[fe] applying the Deployment, Service, and Ingress"
sed "s#docker.io/boanlab/gshare-frontend:latest#${IMG}#g" "$(dirname "$0")/frontend.yaml" | kubectl apply -f -
kubectl -n "${NS}" rollout restart deploy/gshare-frontend 2>/dev/null || true
kubectl -n "${NS}" rollout status deploy/gshare-frontend --timeout=120s

NP=$(kubectl -n ingress-nginx get svc ingress-nginx-controller -o jsonpath='{.spec.ports[?(@.name=="http")].nodePort}')
echo "[fe] verifying (ingress NodePort ${NP}):"
curl -s -o /dev/null -w "  console /console/ -> HTTP %{http_code}\n" "http://localhost:${NP}/console/"
echo "[fe] done. Open http://<node-ip>:${NP}/console/ and sign in as admin@example.com"
