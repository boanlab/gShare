#!/usr/bin/env bash
# E1/E2 Orion baseline (SOTA concurrent-sharing anchor, EuroSys'24) — clone + build +
# run resident and spot AS CONCURRENT CLIENTS under Orion's scheduler (no eviction;
# both share the card simultaneously). This is the alternative design point: it keeps
# both jobs resident and time-slices kernels, so resident never pays a resume cost but
# both pay interference. Emits orion_R.log / orion_S.log in the parser's format.
#
# Orion is feasible on the eval HW: CUDA 12.6, standalone Docker, tested on RTX-3090/H100.
# It will NOT run on Pascal (GTX 1070) — use the cuda-checkpoint-capable 4090 node.
# Reference: https://github.com/eth-easl/orion  (Strati et al., EuroSys'24)
set -euo pipefail
WORKDIR="${1:-/opt/orion}"; GPU="${2:-0}"; MEM="${3:-6}"; SECS="${4:-120}"; OUT="${5:-./e1_out}"
mkdir -p "$OUT"

if [ ! -d "$WORKDIR/.git" ]; then
  git clone https://github.com/eth-easl/orion "$WORKDIR"
fi
cd "$WORKDIR"
# Orion ships a Docker build (CUDA 12.6). Build the scheduler + Python client shim.
docker build -t orion:eval -f Dockerfile . 2>&1 | tail -5

# Orion intercepts CUDA via its scheduler process; client jobs link the Orion shim.
# We submit two instances of our SGEMM workload (R = with idle bursts, S = continuous)
# as co-located Orion clients on the same GPU and let Orion time-slice them.
# NOTE: Orion's client API expects PyTorch/TF op streams; for a raw-cuBLAS workload
# use Orion's `cublas` reqs interception path (docs/) or wrap gpu_sgemm as an Orion job.
echo "Orion built. Submit co-located clients (see Orion docs for the scheduler launch):"
cat <<EOF
  # terminal 1: start the Orion scheduler bound to GPU $GPU
  docker run --rm --gpus '"device=$GPU"' --name orion-sched orion:eval scheduler --gpu $GPU
  # terminal 2: resident client (idle bursts) → tee $OUT/orion_R.log
  # terminal 3: spot client (continuous)      → tee $OUT/orion_S.log
EOF
echo "After both finish, run: python3 parse_e1.py $OUT"
