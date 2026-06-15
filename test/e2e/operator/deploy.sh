#!/usr/bin/env bash
set -euo pipefail
# Builds and deploys the operator: docker build and push, install the CRD, then apply the operator
# with its RBAC.
# Requires docker logged in to a registry, kubectl, and the gshare-sessions namespace. Override the
# image coordinates with REGISTRY, ORG, and TAG.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NS=gshare-system
REGISTRY="${REGISTRY:-docker.io}"; ORG="${ORG:-boanlab}"; TAG="${TAG:-latest}"
IMG="${REGISTRY}/${ORG}/gshare-operator:${TAG}"

if [[ ! -f "${REPO_ROOT}/operator/go.sum" ]]; then
  echo "[op] no go.sum; running go mod tidy in a golang container"
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e GOCACHE=/tmp/.cache -e GOPATH=/tmp/go \
    -v "${REPO_ROOT}/operator:/src" -w /src golang:1.22 go mod tidy
fi

echo "[op] build+push ${IMG}"
make -C "${REPO_ROOT}" image-operator REGISTRY="${REGISTRY}" ORG="${ORG}" TAG="${TAG}"
docker push "${IMG}"

echo "[op] installing the CRD"
kubectl apply -f "${REPO_ROOT}/operator/config/crd/bases/gshare.io_gsharesessions.yaml"

echo "[op] applying the operator"
sed "s#docker.io/boanlab/gshare-operator:latest#${IMG}#g" "$(dirname "$0")/operator.yaml" | kubectl apply -f -
kubectl -n "${NS}" rollout restart deploy/gshare-operator 2>/dev/null || true
kubectl -n "${NS}" rollout status deploy/gshare-operator --timeout=120s

echo "[op] done. Try a sample session: kubectl apply -f $(dirname "$0")/sample-sessions.yaml"
echo "[op]   then check: kubectl -n gshare-sessions get gsharesession,pod -o wide"
