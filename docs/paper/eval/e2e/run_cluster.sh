#!/usr/bin/env bash
# E1/E2 bucket-② conditions on the GShare cluster (containerd, no docker on nodes).
# Runs the workload as K8s Pods requesting a full HAMi card (nvidia.com/gpu:1 +
# gpucores:100 + gpumem = full) on gpu2-1, while no GShare session occupies it.
# Driven through the master's kubectl over SSH. Solo / keep-idle / cold-STOP here;
# app-ckpt + GShare go through the operator path (run_gshare.sh); Orion via orion_setup.sh.
#
# Image: docker.io/boanlab/gshare-e2e-workload:latest (built+pushed GPU-free).
# Usage: MASTER=ubuntu@10.10.10.162 ./run_cluster.sh <MEM_GB> <BURST> <IDLE> <OUTDIR>
set -euo pipefail
MASTER="${MASTER:?set MASTER=ubuntu@10.10.10.162}"
MEM="${1:-6}"; BURST="${2:-60}"; IDLE="${3:-60}"; OUT="${4:-./e1_out}"
MEM_MB=$((MEM*1024)); NODE=gpu2-1; IMG=docker.io/boanlab/gshare-e2e-workload:latest
mkdir -p "$OUT"
K() { ssh -o BatchMode=yes "$MASTER" "kubectl $*"; }

pod() {  # $1=name $2=secs $3=idle_every $4=idle_sec $5=tag
  cat <<EOF
apiVersion: v1
kind: Pod
metadata: {name: $1, namespace: default, labels: {app: e2e}}
spec:
  restartPolicy: Never
  nodeSelector: {kubernetes.io/hostname: $NODE}
  containers:
  - name: w
    image: $IMG
    args: ["--mem-gb","$MEM","--secs","$2","--idle-every","$3","--idle-sec","$4","--tag","$5"]
    resources:
      limits: {nvidia.com/gpu: "1", nvidia.com/gpucores: "100", nvidia.com/gpumem: "$MEM_MB"}
EOF
}
launch() { pod "$@" | ssh -o BatchMode=yes "$MASTER" "kubectl apply -f -" >/dev/null; }
waitlog() { # $1=pod $2=outfile
  K "wait --for=condition=Ready pod/$1 --timeout=90s" >/dev/null 2>&1 || true
  K "wait --for=jsonpath='{.status.phase}'=Succeeded pod/$1 --timeout=900s" >/dev/null 2>&1 || true
  K "logs $1" | tee "$2"; K "delete pod $1 --wait=false" >/dev/null 2>&1 || true
}

echo "== Solo(R) =="; launch e2e-solor $((BURST*2+IDLE)) 0 0 soloR; waitlog e2e-solor "$OUT/soloR.log"
echo "== Solo(S) =="; launch e2e-solos "$IDLE" 0 0 soloS;            waitlog e2e-solos "$OUT/soloS.log"

echo "== keep-idle: R holds card; S cannot schedule (full card busy) =="
launch e2e-ki-r $((BURST*2+IDLE)) 0 0 ki_R
K "wait --for=condition=Ready pod/e2e-ki-r --timeout=90s" >/dev/null 2>&1 || true
launch e2e-ki-s "$IDLE" 0 0 ki_S    # should stay Pending (no free card) → S blocked
sleep 5; K "get pod e2e-ki-s -o jsonpath='{.status.phase}'" | tee "$OUT/ki_S.log"
waitlog e2e-ki-r "$OUT/ki_R.log"; K "delete pod e2e-ki-s --wait=false" >/dev/null 2>&1 || true

echo "== cold-STOP: R burst1, delete R (cold), S runs window, cold-restart R (time-to-Ready) =="
launch e2e-cs-r1 "$BURST" 0 0 cs_R1; waitlog e2e-cs-r1 "$OUT/cs_R1.log"
launch e2e-cs-s "$IDLE" 0 0 cs_S;    waitlog e2e-cs-s  "$OUT/cs_S.log"
T0=$(date +%s.%N); launch e2e-cs-r2 "$BURST" 0 0 cs_R2
K "wait --for=condition=Ready pod/e2e-cs-r2 --timeout=120s" >/dev/null 2>&1 || true
T1=$(date +%s.%N); echo "cold_restart_wall_s=$(echo "$T1-$T0"|bc)" | tee "$OUT/cs_resume.txt"
waitlog e2e-cs-r2 "$OUT/cs_R2.log"
echo "== bucket② done. app-ckpt+GShare: run_gshare.sh ; Orion: orion_setup.sh =="
