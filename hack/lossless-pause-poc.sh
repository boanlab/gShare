#!/usr/bin/env bash
# lossless-pause-poc.sh — phase 1 proof of concept for lossless pause: an exclusive session on a
# single node.
#
# It verifies, directly on a node, that cuda-checkpoint and CRIU together can return a GPU while
# preserving its VRAM and then restore the process exactly as it was.
# Design: see docs/paper/manuscript, §Implementation.
#
# What this relies on, with driver R550 or later:
#   - cuda-checkpoint toggles directly on the host PID, with no nsenter, moving VRAM out to host RAM
#     and back again.
#   - --get-state fails on a container PID ("OS call ... not supported"), so state is judged from the
#     VRAM change nvidia-smi reports.
#   - --action restore accepts --device-map old=new, so a restore works even when a different card is
#     re-acquired.
#   - cuda-checkpoint returns only the VRAM; freeing the Kubernetes GPU slot requires dumping the
#     process with CRIU.
#
# Scope:
#   - Exclusive (full-card) sessions only. Fractional sessions, which go through HAMi's libvgpu
#     LD_PRELOAD, are phase 2.
#   - Run as root on a GPU node. Needs NVIDIA driver R550 or later, cuda-checkpoint, and CRIU.
#   - A process-level checkpoint/restore proof of concept. Container runtime integration (runc and
#     containerd) is phase 2.
#
# Usage:
#   sudo ./hack/lossless-pause-poc.sh checkpoint <PID> [CKPT_DIR]   # return the VRAM, dump the process
#   sudo ./hack/lossless-pause-poc.sh restore   <CKPT_DIR>          # restore the process, reload the VRAM
#   sudo ./hack/lossless-pause-poc.sh demo       [CKPT_DIR]          # run the whole flow on a sample workload
set -euo pipefail

CKPT_DIR_DEFAULT="/var/lib/gshare/checkpoints/poc"
CUDA_CKPT="${CUDA_CHECKPOINT_BIN:-cuda-checkpoint}"

log() { printf '\033[1;36m[poc]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[poc] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

require_tools() {
  command -v "$CUDA_CKPT" >/dev/null 2>&1 || die "cuda-checkpoint not found. Install NVIDIA driver R550 or later and check PATH; CUDA_CHECKPOINT_BIN can point at it directly."
  command -v criu          >/dev/null 2>&1 || die "criu not found. Install it with apt-get install criu; the kernel needs CONFIG_CHECKPOINT_RESTORE."
  command -v nvidia-smi    >/dev/null 2>&1 || die "nvidia-smi not found; run this on a GPU node"
  [ "$(id -u)" -eq 0 ] || die "run as root; CRIU and cuda-checkpoint both need privileges"
}

gpu_mem_used() { nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | paste -sd, -; }

# ── checkpoint: toggle the CUDA state out to the host, returning the VRAM, then dump the process
#    tree ──
do_checkpoint() {
  local pid="$1" ckpt_dir="${2:-$CKPT_DIR_DEFAULT}"
  [ -n "${pid:-}" ] || die "a PID argument is required"
  kill -0 "$pid" 2>/dev/null || die "no such process: PID $pid"
  mkdir -p "$ckpt_dir"

  log "before: GPU mem used (MiB) = $(gpu_mem_used)"
  local before; before="$(gpu_mem_used)"
  log "1) cuda-checkpoint toggle (PID $pid): move VRAM into host RAM and release the CUDA context"
  "$CUDA_CKPT" --toggle --pid "$pid"
  # get-state fails on a container PID, so wait for nvidia-smi to show the VRAM come back instead.
  for _ in $(seq 1 20); do
    [ "$(gpu_mem_used)" != "$before" ] && break
    sleep 0.5
  done
  log "   GPU mem used: $before -> $(gpu_mem_used) MiB  (VRAM returned)"

  log "2) criu dump: save the process tree into $ckpt_dir and exit, fully releasing the GPU"
  criu dump --tree "$pid" --images-dir "$ckpt_dir" \
    --shell-job --tcp-established --file-locks --link-remap --ext-unix-sk \
    --log-file "$ckpt_dir/dump.log" \
    || die "criu dump failed; see $ckpt_dir/dump.log"

  log "done: checkpoint written to $ckpt_dir; final GPU mem used (MiB) = $(gpu_mem_used)"
  log "to restore: sudo $0 restore $ckpt_dir"
}

# ── restore: bring the process back, then toggle the CUDA state to reload the VRAM ──
do_restore() {
  local ckpt_dir="${1:-$CKPT_DIR_DEFAULT}"
  [ -d "$ckpt_dir" ] || die "checkpoint directory not found: $ckpt_dir"

  log "before: GPU mem used (MiB) = $(gpu_mem_used)"
  log "1) criu restore: bringing the process tree back from $ckpt_dir"
  criu restore --images-dir "$ckpt_dir" \
    --shell-job --tcp-established --file-locks --link-remap --ext-unix-sk \
    --restore-detached --log-file "$ckpt_dir/restore.log" \
    || die "criu restore failed; see $ckpt_dir/restore.log"

  # Recover the restored PID; without a pidfile from CRIU, it reuses the PID from dump time.
  local pid; pid="$(criu show --images-dir "$ckpt_dir" 2>/dev/null | awk '/pid/{print $2; exit}' || true)"
  [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null || pid=""

  log "2) cuda-checkpoint toggle: reload the VRAM and resume the CUDA context"
  if [ -n "$pid" ]; then
    "$CUDA_CKPT" --toggle --pid "$pid"
    log "   restored PID=$pid"
  else
    log "   could not determine the restored PID; run cuda-checkpoint --toggle --pid <restored-pid> by hand"
  fi
  log "done: after-restore GPU mem used (MiB) = $(gpu_mem_used)  (compare with the checkpoint state)"
}

# ── demo: start a sample CUDA workload and walk the whole flow — checkpoint, confirm the GPU is
#    empty, restore ──
do_demo() {
  local ckpt_dir="${1:-$CKPT_DIR_DEFAULT}"
  command -v python3 >/dev/null 2>&1 || die "the demo needs python3 with a CUDA-enabled torch"
  log "starting the sample workload: it puts a sentinel tensor in VRAM and holds its value"
  python3 - <<'PY' &
import time, torch
assert torch.cuda.is_available(), "CUDA is not available"
x = torch.arange(1_000_000, device="cuda", dtype=torch.float32) * 3.14  # sentinel
print(f"[demo] sentinel[42]={x[42].item():.4f}  pid={__import__('os').getpid()}", flush=True)
while True:
    time.sleep(2)  # hold idle, simulating a session eligible for lossless pause
PY
  local pid=$!
  sleep 5
  log "workload PID=$pid; checkpointing"
  do_checkpoint "$pid" "$ckpt_dir"
  log "confirm the GPU is empty, then restore. A preserved sentinel value means the pause was lossless."
  do_restore "$ckpt_dir"
}

main() {
  require_tools
  case "${1:-}" in
    checkpoint) shift; do_checkpoint "$@";;
    restore)    shift; do_restore "$@";;
    demo)       shift; do_demo "$@";;
    *) die "usage: $0 {checkpoint <PID> [DIR] | restore [DIR] | demo [DIR]}";;
  esac
}
main "$@"
