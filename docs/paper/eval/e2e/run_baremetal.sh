#!/usr/bin/env bash
# E1/E2 bare-GPU conditions (bucket ②) — runs on a GPU NOT managed by GShare.
# Controlled 3-phase scenario per the FV1 Gantt: resident burst → idle window → resident burst.
# During the idle window a spot (S) job competes for the freed card; how the card is freed
# (or not) is what distinguishes the conditions. The GShare condition (①) is measured
# separately via the live API (run_gshare.sh).
#
# Requires: gshare-e2e-workload image built on this node (see Dockerfile);
#           cuda-checkpoint on PATH for app-ckpt; exclusive use of --gpu.
# Usage: ./run_baremetal.sh <GPU_ID> <MEM_GB> <BURST_SEC> <IDLE_SEC> <OUTDIR>
set -euo pipefail
GPU="${1:-0}"; MEM="${2:-6}"; BURST="${3:-60}"; IDLE="${4:-60}"; OUT="${5:-./e1_out}"
IMG=gshare-e2e-workload
mkdir -p "$OUT"
GPUS="\"device=$GPU\""
run() { docker run --rm --gpus "$GPUS" --name "$1" "${@:2}"; }   # $1=name, rest=args
wl()  { echo "--rm --gpus $GPUS"; }

echo "== Solo(R): resident alone, full card, no contention =="
docker run --rm --gpus "$GPUS" --name e1_soloR $IMG \
  --mem-gb "$MEM" --secs $((BURST*2+IDLE)) --idle-every 100000 --tag soloR | tee "$OUT/soloR.log"

echo "== Solo(S): spot alone, full card =="
docker run --rm --gpus "$GPUS" --name e1_soloS $IMG \
  --mem-gb "$MEM" --secs "$IDLE" --tag soloS | tee "$OUT/soloS.log"

echo "== keep-idle: R holds card across idle; S blocked the whole window =="
# R runs the full span doing burst/idle/burst; S attempts to start but card is occupied → 0 useful.
docker run --rm --gpus "$GPUS" --name e1_ki_R $IMG \
  --mem-gb "$MEM" --secs $((BURST*2+IDLE)) --idle-every 100000 --tag ki_R > "$OUT/ki_R.log" 2>&1 &
RPID=$!
sleep "$BURST"
# S tries to grab the card during the idle window but R never released it → expect OOM/serialized.
( docker run --rm --gpus "$GPUS" --name e1_ki_S $IMG --mem-gb "$MEM" --secs "$IDLE" --tag ki_S \
    > "$OUT/ki_S.log" 2>&1 || echo "ki_S blocked (card held by R)" >> "$OUT/ki_S.log" ) &
wait $RPID; wait || true

echo "== cold-STOP: kill R during idle, run S, cold-restart R (lost warm context) =="
docker run --rm --gpus "$GPUS" --name e1_cs_R1 $IMG --mem-gb "$MEM" --secs "$BURST" --tag cs_R1 | tee "$OUT/cs_R1.log"
# R is now stopped (container exited = cold). Spot runs the idle window:
docker run --rm --gpus "$GPUS" --name e1_cs_S $IMG --mem-gb "$MEM" --secs "$IDLE" --tag cs_S | tee "$OUT/cs_S.log"
# Cold restart R — measure time-to-READY (full re-init + realloc):
T0=$(date +%s.%N)
docker run --rm --gpus "$GPUS" --name e1_cs_R2 $IMG --mem-gb "$MEM" --secs "$BURST" --tag cs_R2 | tee "$OUT/cs_R2.log"
T1=$(date +%s.%N)
echo "cold_restart_wall_s=$(echo "$T1-$T0" | bc)" | tee "$OUT/cs_resume.txt"

echo "== app-ckpt: cuda-checkpoint R to DISK during idle, restore from disk =="
# Launch R detached, checkpoint to disk at idle, run S, restore R from disk.
docker run -d --gpus "$GPUS" --name e1_ac_R $IMG --mem-gb "$MEM" --secs $((BURST*3)) --tag ac_R >/dev/null
sleep "$BURST"
RPID=$(docker inspect -f '{{.State.Pid}}' e1_ac_R)
echo "checkpoint R(pid=$RPID) to disk…"
TC0=$(date +%s.%N); sudo cuda-checkpoint --toggle --pid "$RPID"; TC1=$(date +%s.%N)   # evict to host
# (disk persistence step is app/criu-specific; we record the evict+restore round-trip as the floor)
docker run --rm --gpus "$GPUS" --name e1_ac_S $IMG --mem-gb "$MEM" --secs "$IDLE" --tag ac_S | tee "$OUT/ac_S.log"
TR0=$(date +%s.%N); sudo cuda-checkpoint --toggle --pid "$RPID"; TR1=$(date +%s.%N)   # restore
echo "ac_evict_s=$(echo "$TC1-$TC0"|bc) ac_restore_s=$(echo "$TR1-$TR0"|bc)" | tee "$OUT/ac_resume.txt"
docker logs e1_ac_R > "$OUT/ac_R.log" 2>&1 || true
docker rm -f e1_ac_R >/dev/null 2>&1 || true

echo "== done. logs in $OUT =="
