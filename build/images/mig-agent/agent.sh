#!/usr/bin/env bash
set -euo pipefail
# GShare mig-agent: toggle one card's MIG mode.
#
# Env (set by the GpuModeChangeReconciler):
#   GPU_INDEX   — nvidia-smi -i target
#   GPU_UUID    — the physical card's UUID; cross-checked against the index before acting
#   TARGET_MODE — "mig" (enable) or "hami-core" (disable)
#
# The exit code is the contract: 0 flips the GpuModeChange to Succeeded, anything else to Failed.

log() { printf '[mig-agent] %s\n' "$*"; }
die() { printf '[mig-agent][ERROR] %s\n' "$*" >&2; exit 1; }

: "${GPU_INDEX:?GPU_INDEX is required}"
: "${GPU_UUID:?GPU_UUID is required}"
: "${TARGET_MODE:?TARGET_MODE is required}"

command -v nvidia-smi >/dev/null || die "nvidia-smi not injected (RuntimeClass nvidia missing?)"

# 1. The index must be the card the control plane meant.
actual_uuid=$(nvidia-smi -i "${GPU_INDEX}" --query-gpu=uuid --format=csv,noheader | tr -d ' ')
[ "${actual_uuid}" = "${GPU_UUID}" ] \
  || die "index ${GPU_INDEX} is ${actual_uuid}, expected ${GPU_UUID} — refusing to touch the wrong card"

# 2. The card must be process-free (the ledger drained it; verify on the metal).
procs=$(nvidia-smi -i "${GPU_INDEX}" --query-compute-apps=pid --format=csv,noheader | sed '/^$/d' | wc -l)
[ "${procs}" -eq 0 ] || die "card still runs ${procs} compute process(es); aborting"

case "${TARGET_MODE}" in
  mig)       target=1 ;;
  hami-core) target=0 ;;
  *)         die "unknown TARGET_MODE '${TARGET_MODE}'" ;;
esac

current=$(nvidia-smi -i "${GPU_INDEX}" --query-gpu=mig.mode.current --format=csv,noheader | tr -d ' ')
if { [ "${target}" = 1 ] && [ "${current}" = "Enabled" ]; } \
   || { [ "${target}" = 0 ] && [ "${current}" = "Disabled" ]; }; then
  log "MIG mode already ${current} on GPU ${GPU_INDEX}; nothing to do"
  exit 0
fi

log "setting MIG mode ${target} on GPU ${GPU_INDEX} (${GPU_UUID})"
out=$(nvidia-smi -i "${GPU_INDEX}" -mig "${target}" 2>&1) || die "nvidia-smi -mig failed: ${out}"
log "${out}"

# 3. Some GPUs need a reset for the toggle to take effect ("pending" in the output). A per-GPU
#    reset only works with no attached clients; the card is drained, so try it, and treat a
#    still-pending state as failure — the operator surfaces it and the admin can reboot the node.
if printf '%s' "${out}" | grep -qi "pending"; then
  log "mode change pending; attempting GPU reset"
  reset_out=$(nvidia-smi -i "${GPU_INDEX}" --gpu-reset 2>&1) || die "GPU reset failed: ${reset_out}"
  log "${reset_out}"
fi

final=$(nvidia-smi -i "${GPU_INDEX}" --query-gpu=mig.mode.current --format=csv,noheader | tr -d ' ')
if [ "${target}" = 1 ]; then
  [ "${final}" = "Enabled" ] || die "MIG mode still ${final} after toggle+reset"
else
  [ "${final}" = "Disabled" ] || die "MIG mode still ${final} after toggle+reset"
fi
log "done: GPU ${GPU_INDEX} MIG mode is now ${final}"
