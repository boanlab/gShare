# Evaluation reproduction

The scripts, data, and commands that reproduce every figure and table in the manuscript
([../manuscript/main.tex](../manuscript/main.tex)). Figure numbers are assigned when the
manuscript is compiled; the table below is keyed to the evaluation subsections instead.

## Layout

```
docs/paper/eval/
├── micro/   # §E.2 mechanism microbenchmarks + §E.3 FV1 figure (M1, M5, FV1)
├── e2e/     # §E.4 five-condition end-to-end comparison (workload, runner, parser, plots)
│            #      plus e2e/orion/ (the Orion attempt)
├── sim/     # §E.5 cluster-scale trace simulation (S1–S3)
├── o1/      # §E.6 system overhead (scheduler and ledger benchmarks, cluster metrics)
└── README.md
```

## Environment, by bucket

- **Bucket ① (live GShare)** — a running GShare cluster with an external Compose control
  plane. Nodes must support `cuda-checkpoint` and `criu` (RTX 4090, driver ≥ 535). Our
  testbed: `gpu2-1`, Kubernetes 1.36 with HAMi.
- **Bucket ② (bare GPU)** — a full card not held by GShare. We ran these as HAMi full-card
  pods while no session was active, so no teardown was needed.
- **Bucket ③ (simulation)** — no GPU. Python 3.10 or newer; `matplotlib` only for
  generating figures.
- **Common** — cluster commands go through the master (`MASTER=ubuntu@<master-ip>`). Build
  and push the workload image with `docker build -t boanlab/gshare-e2e-workload e2e/` so
  the nodes can pull it. Generating PNGs needs `matplotlib`; without it, run
  `docker run --rm -v "$PWD":/w -w /w python:3.12-slim sh -c "pip install -q matplotlib numpy && python <plot>.py"`.

## Figure and table → reproduction command

| Fig | Section | Bucket | Script | Reproduce |
|---|---|---|---|---|
| **M1** | E.2 | ① | `micro/m1_vram_hold.py`, `m1_run.sh`, `m1_plot.py` | ① below |
| **M5** | E.2 | derived | `micro/m5_breakeven.py` | `python3 micro/m5_breakeven.py micro/m1.csv` → `m5.png` |
| **M2/M3/M4** | E.2 | ① | derived | Decomposed from M1 (`m1.csv`) plus live E1 (cold-STOP) and FV1 — see §Evaluation |
| **FV1** | E.3 | ① | `micro/fv1_gantt.py` | `python3 micro/fv1_gantt.py` → `fv1.png`, a Gantt chart over live measured latencies |
| **FV2** | E.3 | unit | `backend/tests/test_scheduler_logic.py` | ② below |
| **FV3** | E.3 | ① | negative result | See §Background (C2, the lossless-pause negative result) |
| **FV4** | E.3 | ① | `e2e/manifests/fv4_webhook_deny.yaml` | ③ below |
| **E1, E2** | E.4 | ①+② | `e2e/*` | ④ below |
| **MT (fig. 15)** | E.4c | ① | `e2e/mt_plot.py`, `e2e/mt_e2e.md` | Real two-node multi-tenant yield/borrow/reclaim; procedure and raw data in `mt_e2e.md` |
| **MPS (SOTA)** | E.4, E.7 | ② | `e2e/mps_sota.md` | Direct measurement of real NVIDIA MPS, validating concurrent-share ½ as representative |
| **Pageability, T2** | E.6b | ① | `e2e/pageability.md` | Measured non-pinned anonymous pages in the evict buffer, plus a real T2 (disk/swap) lossless round trip |
| **Tier policy (fig. 16)** | E.6c | derived | `e2e/tier_policy.py` | The T2-versus-T3 selection boundary, from measured constants: swap-in 221 MB/s, cold start 12.87 s |
| **Trace impact (fig. 17)** | E.5c | ③ | `sim/trace_impact.py`, `sim/trace_impact.md` | Per-GPU utilisation distribution from the Alibaba PAI 2020 trace (fetch with `download_data.sh`; method and numbers in the note) |
| **Trace-driven (fig. 18)** | E.5d | ③ | `sim/sim_trace_driven.py` | Simulation driven by the real utilisation distribution, sweeping the idle-gap φ (boundary 0–223 GPU-h per 24 h) |
| **S1, S2, S3** | E.5 | ③ | `sim/simulator.py`, `sim_plot.py`, `sim_validate.py` | ⑤ below |
| **S4 (economics)** | E.5b | ③ | `sim/econ_plot.py` | ⑤ below |
| **Host-RAM bound (fig. 14)** | E.6b | derived | `sim/hostram_bound.py` | ⑥ below |
| **O1** | E.6 | ① | `o1/*` | ⑥ below |
| **Reclaim decomposition (fig. 13)** | E.6 | ① | `o1/reclaim_decomp.py` | ⑥ below |

---

### ① M1 — evict/restore against VRAM size (GPU node, as root)

`m1_vram_hold.py` holds N GB of VRAM through the ctypes driver API, so no PyTorch is
needed; `m1_run.sh` times N evict/restore cycles with `cuda-checkpoint --toggle` and emits
a CSV.

```bash
# On the node, or copy there through the master:
scp docs/paper/eval/micro/m1_vram_hold.py <node>:/tmp/
sudo VRAMS="2 4 6 8 12 16" N=5 WORKLOAD=/tmp/m1_vram_hold.py OUT=/tmp/m1.csv \
  bash docs/paper/eval/micro/m1_run.sh

# Collect the CSV, then plot:
python3 docs/paper/eval/micro/m1_plot.py docs/paper/eval/micro/m1.csv   # → m1.png
```

### ② FV2 — bin-packing decisions (unit test)

```bash
docker run --rm -v "$PWD/backend":/app -w /app gshare-backend-test \
  python -m pytest tests/test_scheduler_logic.py -q          # 9/9 pass
```

### ③ FV4 — lend-guard admission webhook denial (live)

Create a device-plugin-bypassing pod in `gshare-sessions` that pins a non-yielded card; the
webhook must reject it.

```bash
# Find a non-yielded card UUID:
ssh $MASTER 'kubectl -n kube-system logs <hami-device-plugin-pod> -c device-plugin | grep Registered'

# Substitute the UUID into the manifest, then:
ssh $MASTER 'kubectl apply -f -' < docs/paper/eval/e2e/manifests/fv4_webhook_deny.yaml
#  → Error: admission webhook "pods.lend-guard.gshare.io" denied:
#            GPU ... is not a yielded/lendable card
```

### ④ E1/E2 — five-condition end-to-end comparison (cluster)

```bash
# 0) Build and push the workload image (compiles without a GPU):
docker build -t boanlab/gshare-e2e-workload docs/paper/eval/e2e && docker push boanlab/gshare-e2e-workload

# 1) Bare-GPU conditions (solo, keep-idle, cold-STOP) as full-card Kubernetes pods:
MASTER=ubuntu@<master> bash docs/paper/eval/e2e/run_cluster.sh 6 60 60 docs/paper/eval/e2e/e1_out

# 2) concurrent-share (50/50, 70/30, 90/10), varying gpucores:
ssh $MASTER 'kubectl apply -f -' < docs/paper/eval/e2e/manifests/concurrent_share.yaml
#    collect the logs, then delete

# 3) The GShare condition: live yield/borrow/reclaim (the FV1 path) via run_gshare.sh,
#    or assembled from the M1 and FV1 components.

# 4) Matrix and figures:
python3 docs/paper/eval/e2e/parse_e1.py docs/paper/eval/e2e/e1_out   # → matrix + e1.json
cd docs/paper/eval/e2e && python3 e1_plot.py                          # → e1.png, e1b.png, e2.png

# App-checkpoint disk bandwidth (E1b):
ssh $MASTER 'kubectl apply -f -' < docs/paper/eval/e2e/manifests/diskbw.yaml

# Optional Orion comparison: docs/paper/eval/e2e/orion/README.md
```

> The nodes run containerd, so `docker --gpus` is unavailable — hence `run_cluster.sh`,
> which uses Kubernetes pods. On a docker-GPU host, `run_baremetal.sh` runs the same
> scenarios through docker.

### ⑤ S1/S2/S3 — cluster simulation (no GPU)

```bash
cd docs/paper/eval/sim
python3 sim_validate.py                                                # cross-validation, 19/19
python3 simulator.py --out sim_results.json                            # default: measured cold-start anchor
python3 simulator.py --cold-a 9.2 --cold-b 0.73 --out sim_cold_lo.json # sensitivity: anchor as lower bound
python3 simulator.py --cold-a 20  --cold-b 3.0  --out sim_cold_mid.json
python3 simulator.py --cold-a 40  --cold-b 6.0  --out sim_cold_hi.json # sensitivity: high
python3 sim_plot.py sim_results.json sim_cold_lo.json sim_cold_mid.json sim_cold_hi.json
#  → s1.png, s2.png, s3.png   (fixed seed, so reproduction is deterministic)
```

`sim_validate.py` checks three things: (V1) the cost model is grounded in `m1.csv`,
(V2) hand calculation matches aggregate precision, and (V3) a real E1 cycle is reproduced
with 0% error.

### ⑥ O1 — system overhead

```bash
# Scheduler decision latency (CPU only, backend image):
docker run --rm -v "$PWD":/app -w /app gshare-backend-test python docs/paper/eval/o1/scheduler_bench.py

# Ledger transaction latency (api → postgres, rolled back):
docker exec gshare-gshare-api-1 python - < docs/paper/eval/o1/ledger_bench.py

# Webhook admission and operator reconcile latency (cluster metrics, no workload needed):
MASTER=ubuntu@<master> bash docs/paper/eval/o1/cluster_metrics.sh

# Reclaim latency decomposition (fig. 13): job orchestration (measured container start)
# versus mechanism versus node-agent projection.
python3 docs/paper/eval/o1/reclaim_decomp.py          # → reclaim_decomp.png (needs matplotlib)
#   The container-start constant (4.04 s) was measured on the live cluster by spinning up
#   N=6 no-op agent Jobs and timing create → running.

# Host-RAM bound (fig. 14): concurrent yields per node = floor(host_RAM · 0.5 / VRAM)
python3 docs/paper/eval/sim/hostram_bound.py          # → hostram_bound.png (testbed: 64 GiB measured)
```

## Determinism and caveats

- The simulations (S) are fully deterministic under a fixed seed. The microbenchmarks
  (M, O1) report the mean and quantiles over N runs.
- The live measurements (FV1, the GShare E1 condition, FV4) depend on cluster state and
  will vary with active sessions and card lend status. Always clean up afterwards:
  `kubectl delete pod -l app=e2e`.
- Absolute throughput figures (54 TFLOPS and similar) are specific to the RTX 4090. The
  relative results — slowdown, whether capacity was reclaimed, the lossless advantage —
  reproduce on any hardware.
