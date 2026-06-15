#!/usr/bin/env bash
# E1/E2 GShare condition (bucket ①) — runs on the LIVE GShare system, reusing the
# FV1-validated yield→borrow→reclaim path. Resident runs the workload with an idle
# window; on idle the session yields in-place (VRAM→host RAM, Pod alive); a spot
# session borrows the freed card; resident reclaims losslessly. Emits gshare_R.log /
# gshare_S.log / gshare_resume.txt in the parser's format.
#
# Requires: kubectl context for the cluster; gshare-e2e-workload image available to
#   gshare-sessions pods (push to node/registry first); the resident/spot Offerings
#   configured for the target exclusive card (gpu2-1). See README.md.
# Usage: ./run_gshare.sh <MEM_GB> <BURST_SEC> <IDLE_SEC> <OUTDIR>
set -euo pipefail
MEM="${1:-6}"; BURST="${2:-60}"; IDLE="${3:-60}"; OUT="${4:-./e1_out}"
NS=gshare-sessions; CARD_NODE="${CARD_NODE:-gpu2-1}"
mkdir -p "$OUT"

# The workload runs INSIDE the session Pod. We exec it and tee logs out. Session
# creation/yield/borrow/reclaim is driven through the GShare API exactly as FV1;
# this script assumes the resident session Pod is already Running on $CARD_NODE and
# its name is in $R_POD (set by the API-driven launcher, or pass via env).
: "${R_POD:?set R_POD to the resident session Pod name}"

echo "== resident burst 1 =="
kubectl -n "$NS" exec "$R_POD" -- gpu_sgemm --mem-gb "$MEM" --secs "$BURST" --tag gshare_R \
  | tee "$OUT/gshare_R.log"

echo "== trigger in-place yield (idle → evict, Pod alive) =="
# API: let credit lapse / idle reaper fire, OR explicit pause. Operator runs lossless-agent.
gshare-cli session pause "$R_SESSION"            # replace with your API call
# wait for CR phase Yielded
kubectl -n gshare-system wait --for=jsonpath='{.status.phase}'=Yielded \
  "session/$R_SESSION" --timeout=60s || true

echo "== spot borrows freed card for the idle window =="
# API: create a preemptible (spot) session targeting the yielded card → borrows full card.
# Its Pod runs the workload for $IDLE seconds; capture throughput.
: "${S_POD:?set S_POD to the spot session Pod name created via the API}"
kubectl -n "$NS" exec "$S_POD" -- gpu_sgemm --mem-gb "$MEM" --secs "$IDLE" --tag gshare_S \
  | tee "$OUT/gshare_S.log"

echo "== resident reclaim (lossless restore) — measure restore latency =="
T0=$(date +%s.%N)
gshare-cli session resume "$R_SESSION"           # replace with your API call
kubectl -n gshare-system wait --for=jsonpath='{.status.phase}'=Running \
  "session/$R_SESSION" --timeout=60s
T1=$(date +%s.%N)
echo "lossless_restore_s=$(echo "$T1-$T0" | bc)" | tee "$OUT/gshare_resume.txt"

echo "== resident burst 2 (continues from preserved state, no progress lost) =="
kubectl -n "$NS" exec "$R_POD" -- gpu_sgemm --mem-gb "$MEM" --secs "$BURST" --tag gshare_R \
  | tee -a "$OUT/gshare_R.log"
echo "== done. host-RAM held during borrow = evicted VRAM (~${MEM}GB) =="
