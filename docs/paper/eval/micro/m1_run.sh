#!/usr/bin/env bash
# M1 sweep — evict/restore latency vs VRAM size, via the ctypes driver-API workload
# (m1_vram_hold.py) + cuda-checkpoint --toggle. Runs ON a GPU node (root via sudo).
# Output: CSV to stdout + $OUT. Columns: vram_gb,rep,init_s,evict_s,restore_s,vram_used_mib.
#   init_s = launch→VRAM-resident (driver ctx+alloc; NOT the torch framework cold-start).
set -u
WORKLOAD="${WORKLOAD:-/tmp/m1_vram_hold.py}"
VRAMS="${VRAMS:-2 4 6 8 12 16}"
N="${N:-5}"
OUT="${OUT:-/tmp/m1.csv}"
vram(){ nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits; }
el(){ awk "BEGIN{printf \"%.3f\", $2-$1}"; }

echo "vram_gb,rep,init_s,evict_s,restore_s,vram_used_mib" > "$OUT"
for gb in $VRAMS; do
  for rep in $(seq 1 "$N"); do
    rm -f /tmp/m1.out
    l0=$(date +%s.%N)
    python3 "$WORKLOAD" --gb "$gb" >/tmp/m1.out 2>&1 &
    for _ in $(seq 1 160); do grep -q READY /tmp/m1.out 2>/dev/null && break; sleep 0.25; done
    if ! grep -q READY /tmp/m1.out 2>/dev/null; then echo "FAIL gb=$gb rep=$rep" >&2; cat /tmp/m1.out >&2; continue; fi
    l1=$(date +%s.%N)
    PID=$(sed -n 's/.*pid=\([0-9]*\).*/\1/p' /tmp/m1.out)
    used=$(vram)
    t0=$(date +%s.%N); sudo cuda-checkpoint --toggle --pid "$PID" >/dev/null 2>&1; t1=$(date +%s.%N)  # evict
    sleep 0.4
    t2=$(date +%s.%N); sudo cuda-checkpoint --toggle --pid "$PID" >/dev/null 2>&1; t3=$(date +%s.%N)  # restore
    sleep 0.4
    echo "$gb,$rep,$(el "$l0" "$l1"),$(el "$t0" "$t1"),$(el "$t2" "$t3"),$used" >> "$OUT"
    kill "$PID" 2>/dev/null || true
    sleep 1
  done
done
echo "=== CSV ($OUT) ==="; cat "$OUT"
