#!/usr/bin/env bash
set -euo pipefail
# Builds and deploys the lightweight backend stack (api, worker, Postgres, Redis): docker build, push,
# then apply stack.yaml.
# Requires docker logged in to a registry, kubectl, and a default StorageClass. Override the image
# coordinates with REGISTRY, ORG, and TAG.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NS=gshare-system
REGISTRY="${REGISTRY:-docker.io}"; ORG="${ORG:-boanlab}"; TAG="${TAG:-latest}"
IMG="${REGISTRY}/${ORG}/gshare-backend:${TAG}"

echo "[be] build and push ${IMG}, shared by api and worker"
make -C "${REPO_ROOT}" image-backend REGISTRY="${REGISTRY}" ORG="${ORG}" TAG="${TAG}"
docker push "${IMG}"

echo "[be] applying the stack; the api initContainer runs alembic upgrade head"
sed "s#docker.io/boanlab/gshare-backend:latest#${IMG}#g" "$(dirname "$0")/stack.yaml" | kubectl apply -f -
kubectl -n "${NS}" rollout status deploy/gshare-pg --timeout=120s
kubectl -n "${NS}" rollout status deploy/gshare-redis --timeout=60s
kubectl -n "${NS}" rollout restart deploy/gshare-api deploy/gshare-worker
# The api needs the gshare-jwt-signing secret to start; provision-internal-jwt.sh creates it, and
# until then the pod waits.
kubectl -n "${NS}" rollout status deploy/gshare-api --timeout=180s
kubectl -n "${NS}" rollout status deploy/gshare-worker --timeout=60s

echo "[be] verifying"
kubectl -n "${NS}" exec deploy/gshare-api -c api -- \
  python -c "import urllib.request;print('healthz=',urllib.request.urlopen('http://localhost:8000/healthz',timeout=5).read().decode())"
echo "[be] done. Reach the API with: kubectl -n ${NS} port-forward svc/gshare-api 8080:80"
