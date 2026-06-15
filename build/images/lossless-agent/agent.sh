#!/usr/bin/env bash
# GShare lossless-pause node agent — run by the operator as a privileged (hostPID) Job on the node.
# Actions: checkpoint | yield | resume | cleanup | restore (unsupported; see do_restore).
#
# Requires: hostPID + privileged + /dev/nvidia* + host containerd socket (/run/containerd).
# The container process dump is done by host containerd (`ctr c checkpoint`), so criu must be
# installed on the NODE (label gshare.io/criu=ready), not in this image. The agent itself only runs
# cuda-checkpoint (GPU VRAM) via hostPID and drives `ctr` over the mounted socket.
set -euo pipefail

CC="${CUDA_CHECKPOINT_BIN:-/usr/bin/cuda-checkpoint}"
CTR="${CTR_BIN:-/usr/local/bin/ctr}"
CTR_NS="${CTR_NAMESPACE:-k8s.io}"   # kubelet-created containers live in containerd's k8s.io namespace
log() { printf '[lossless-agent] %s\n' "$*" >&2; }
die() { printf '[lossless-agent] ERROR: %s\n' "$*" >&2; exit 1; }

ctr() { "$CTR" -n "$CTR_NS" "$@"; }

# host PID of the single CUDA process running on the bound GPU (UUID).
pid_on_gpu() {
  local uuid="$1"
  nvidia-smi --query-compute-apps=gpu_uuid,pid --format=csv,noheader 2>/dev/null \
    | awk -F', *' -v u="$uuid" '$1==u{print $2; exit}'
}
gpu_mem_used() { nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1; }
gpu_mem_total() { nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1; }
gpu_mem_free() { echo $(( $(gpu_mem_total) - $(gpu_mem_used) )); }

# checkpoint: evict VRAM (cuda-checkpoint) + dump the container (containerd-native) so the Pod can be deleted.
do_checkpoint() {
  local uuid="" cid="" dir=""
  while [ $# -gt 0 ]; do case "$1" in
    --gpu-uuid) uuid="$2"; shift 2;; --container-id) cid="$2"; shift 2;;
    --dir) dir="$2"; shift 2;; *) shift;; esac; done
  [ -n "$uuid" ] && [ -n "$cid" ] && [ -n "$dir" ] || die "checkpoint: --gpu-uuid, --container-id, --dir required"
  local pid; pid="$(pid_on_gpu "$uuid")"
  [ -n "$pid" ] || die "no CUDA process found on GPU $uuid"
  mkdir -p "$dir"
  local ref
  ref="localhost/gshare-ckpt/$(basename "$dir"):latest"
  log "checkpoint pid=$pid gpu=$uuid container=$cid  (before VRAM=$(gpu_mem_used) MiB)"

  # 1) Toggle CUDA state to host RAM → frees physical VRAM, suspends CUDA. The container dump below
  #    then captures that host-RAM state (lossless on restore via VRAM reload).
  "$CC" --toggle --pid "$pid"
  # get-state fails on a container PID, so confirm via VRAM drop.
  for _ in $(seq 1 40); do [ "$(gpu_mem_used)" -lt 64 ] && break; sleep 0.5; done
  log "VRAM after cuda-checkpoint = $(gpu_mem_used) MiB"

  # 2) containerd-native container checkpoint. --task: include the running task; --rw: include rootfs
  #    changes. No --exit — the task stays blocked (CUDA suspended) and the operator deletes the Pod to
  #    reclaim the GPU slot (avoids a kubelet restartPolicy restart race). Output is a named OCI image
  #    in the content store ($ref) — named = GC-safe, node-persistent.
  ctr image rm "$ref" >/dev/null 2>&1 || true   # idempotent: drop a stale ref from a prior attempt
  ctr c checkpoint --task --rw "$cid" "$ref" \
    || die "ctr c checkpoint failed (host needs criu — node label gshare.io/criu=ready)"
  ctr image ls -q 2>/dev/null | grep -qx "$ref" \
    || die "checkpoint image not in content store: $ref"
  # 3) metadata in the shared dir (node hostPath); ref/UUID for any future restore.
  printf '%s\n' "$uuid" > "$dir/gpu-uuid"
  printf '%s\n' "$cid"  > "$dir/container-id"
  printf '%s\n' "$ref"  > "$dir/ref"
  # 4) best-effort portable tar export — some containerd versions can't export a checkpoint image
  #    (OCI exporter mis-reads the criu config blob). The named content-store image is canonical, so
  #    proceed on failure.
  if ctr image export "$dir/checkpoint.tar" "$ref" 2>"$dir/export.err"; then
    log "portable tar export -> $dir/checkpoint.tar"
  else
    log "WARN: ctr image export unsupported — content-store image ($ref) is canonical: $(tail -1 "$dir/export.err" 2>/dev/null)"
  fi
  log "checkpoint done (ref=$ref, GPU freed)"
}

# restore: intentionally unsupported — it must run in a kubelet-created sandbox netns, which only
# kubelet/CRI checkpoint-restore can provide (docs/paper/lossless-pause.md). resume is cold.
do_restore() {
  die "restore is unsupported; needs kubelet/CRI checkpoint-restore (docs/paper/lossless-pause.md). resume is cold; progress is preserved by app-level checkpointing."
}

# yield: in-place GPU yield — evict VRAM only (process kept alive) to free the physical card; records
# PID for resume. Pod is not deleted, so resume toggles back into the same process (lossless). (gpu-yield-lending)
do_yield() {
  local uuid="" dir=""
  while [ $# -gt 0 ]; do case "$1" in
    --gpu-uuid) uuid="$2"; shift 2;; --dir) dir="$2"; shift 2;; *) shift;; esac; done
  [ -n "$uuid" ] && [ -n "$dir" ] || die "yield: --gpu-uuid, --dir required"
  local pid; pid="$(pid_on_gpu "$uuid")"
  [ -n "$pid" ] || die "no CUDA process found on GPU $uuid"
  mkdir -p "$dir"
  local before; before="$(gpu_mem_used)"
  # Host-RAM guard: evicted VRAM lives in host RAM, so refuse yield when free RAM is short (avoids
  # host OOM) → operator falls back to cold (durable app checkpoint). (gpu-yield-lending)
  local avail need
  avail="$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0)"
  need="$(( before * 110 / 100 ))"     # evicted VRAM + 10% headroom
  if [ "${avail:-0}" -lt "$need" ]; then
    die "insufficient host RAM (avail=${avail}MiB < need=${need}MiB) — yield refused, operator cold fallback"
  fi
  "$CC" --toggle --pid "$pid"          # evict VRAM -> host RAM (process suspends, Ssl)
  for _ in $(seq 1 40); do [ "$(gpu_mem_used)" -lt 64 ] && break; sleep 0.5; done
  printf '%s\n' "$pid"  > "$dir/yield.pid"        # for resume toggle-back into the same process
  printf '%s\n' "$uuid" > "$dir/yield.gpu-uuid"
  printf '%s\n' "$before" > "$dir/yield.need-mb"  # VRAM needed to reload (pre-evict usage); resume gate
  log "yield done pid=$pid gpu=$uuid  VRAM ${before}->$(gpu_mem_used) MiB (physical GPU freed)"
}

# resume: undo a yield — toggle-back via the recorded PID, reloading VRAM into the same live process (lossless).
do_resume() {
  local dir=""
  while [ $# -gt 0 ]; do case "$1" in --dir) dir="$2"; shift 2;; *) shift;; esac; done
  [ -n "$dir" ] || die "resume: --dir required"
  # Idempotent: no yield state means already resumed — no-op (avoids a double-toggle re-evict).
  [ -s "$dir/yield.pid" ] || { log "resume no-op (no yield state — already resumed)"; return 0; }
  local pid; pid="$(cat "$dir/yield.pid")"
  # VRAM gate: wait for a preempted borrower to release VRAM before toggling (avoids OOM). need =
  # owner's pre-evict usage; poll until free >= need (up to ~60s; proceed best-effort past timeout).
  local need; need="$(cat "$dir/yield.need-mb" 2>/dev/null || echo 0)"
  if [ "${need:-0}" -gt 0 ]; then
    local ok=0
    for _ in $(seq 1 120); do
      [ "$(gpu_mem_free)" -ge "$need" ] && { ok=1; break; }
      sleep 0.5
    done
    [ "$ok" = 1 ] && log "VRAM gate passed (free=$(gpu_mem_free) >= need=${need} MiB)" \
                   || log "WARN: VRAM gate timeout (free=$(gpu_mem_free) < need=${need}) — proceeding best-effort"
  fi
  "$CC" --toggle --pid "$pid"          # restore VRAM into the live process
  for _ in $(seq 1 40); do [ "$(gpu_mem_used)" -gt 8 ] && break; sleep 0.5; done
  log "resume done pid=$pid  VRAM=$(gpu_mem_used) MiB (lossless)"
  rm -f "$dir/yield.pid" "$dir/yield.gpu-uuid" "$dir/yield.need-mb"
}

# cleanup: reclaim the node-local checkpoint (content-store named image + shared dir). Idempotent (no-op if absent).
do_cleanup() {
  local dir="" ref=""
  while [ $# -gt 0 ]; do case "$1" in
    --dir) dir="$2"; shift 2;; --ref) ref="$2"; shift 2;; *) shift;; esac; done
  [ -n "$dir" ] || die "cleanup: --dir required"
  [ -z "$ref" ] && [ -s "$dir/ref" ] && ref="$(cat "$dir/ref" 2>/dev/null || true)"
  [ -z "$ref" ] && ref="localhost/gshare-ckpt/$(basename "$dir"):latest"
  ctr image rm "$ref" >/dev/null 2>&1 || true   # remove content-store named image (ignore if absent)
  rm -rf "$dir" 2>/dev/null || true             # remove shared dir (metadata + tar)
  log "cleanup done (ref=$ref, dir=$dir)"
}

case "${1:-}" in
  checkpoint) shift; do_checkpoint "$@";;
  restore)    shift; do_restore "$@";;
  cleanup)    shift; do_cleanup "$@";;
  yield)      shift; do_yield "$@";;
  resume)     shift; do_resume "$@";;
  *) die "usage: {checkpoint --gpu-uuid U --container-id C --dir D | yield --gpu-uuid U --dir D | resume --dir D | cleanup --dir D | restore (unsupported)}";;
esac
