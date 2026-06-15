#!/usr/bin/env bash
set -euo pipefail
# Removes the lightweight backend stack, PVCs included. The cluster and GPU layers are left alone.
kubectl delete -f "$(dirname "$0")/stack.yaml" --ignore-not-found
echo "[be] removed. The gshare-pg-data PVC went with it, so the data is gone."
