#!/usr/bin/env bash
set -euo pipefail
# Removes the operator and the test sessions. The cluster, the GPU layer, and the backend stay.
HERE="$(dirname "$0")"
kubectl delete -f "${HERE}/sample-sessions.yaml" --ignore-not-found --timeout=60s || true
kubectl delete -f "${HERE}/operator.yaml" --ignore-not-found || true
echo "[op] the CRD is kept; remove it with: kubectl delete crd gsharesessions.gshare.io"
