# Reclaim end-to-end head-to-head — Job path vs resident node-agent (measured)

Real measurement on the live 2-node RTX-4090 cluster (gpu2-1), replacing the earlier *projection*
of the resident-node-agent reclaim path. A long-lived privileged hostPID agent pod was deployed
("already running") and measured against the current per-reclaim Job path on the **same** live GPU
process. Constants feed `reclaim_decomp.py` (figure 13).

## Setup
- **VRAM holder**: a pod (`python:3.11-slim` + HAMi `nvidia.com/gpu:1`) running a ctypes libcuda
  workload (same approach as `micro/m1_vram_hold.py`) holding **4 GB** VRAM, host PID 26574.
- **Resident agent**: a pod from `boanlab/gshare-lossless-agent:latest`, `privileged`, `hostPID:true`,
  `runtimeClassName: nvidia`, `/dev` hostPath, `command: ["sleep","infinity"]` — i.e. the production
  agent image, already running (not spawned per reclaim). `cuda-checkpoint` at `/usr/bin`.
- cuda-checkpoint state machine: `running --lock--> locked --checkpoint--> checkpointed
  --restore--> locked --unlock--> running`. (`--toggle` alone was unreliable; explicit `--action`
  with `--timeout` is robust.)

## Lossless cross-container cycle verified (nvidia-smi VRAM, n=6 in-pod, ms)
| step | op | latency | VRAM after |
|---|---|---|---|
| evict | lock | ~18 ms | — |
| evict | checkpoint | ~3.1 s | **1 MiB** (4492→1) |
| reclaim | restore | **~1.07 s** | 4492 MiB (1→4492) |
| reclaim | unlock | ~21 ms | state `running` (resumed, lossless) |

Cross-container restore 1.07 s ≈ M1 host measurement (4 GB ≈ 1.06–1.27 s) → a resident pod's
cuda-checkpoint invocation costs the same as M1's host invocation; only the *invocation path* differs.

## Head-to-head reclaim (control-plane timed, same PID 26574)
| path | what is timed | reclaim latency |
|---|---|---|
| **Job** (current) | fresh privileged hostPID Job runs `restore`+`unlock`, create→Succeeded | **8.2 s** (n=3: 8.33/8.13/8.05) |
| **resident node-agent** | single exec/RPC into the already-running agent does `restore`+`unlock` | **1.28 s** (n=5: 1.316/1.275/1.279/1.278/1.269) |

→ **6.4× faster, measured end-to-end.** With the operator's real 3 s poll added, the Job path is
~9.7 s, matching the deployed "~10 s" reclaim.

Supporting measured constants:
- resident-agent exec/RPC round-trip (dispatch): **0.19 s** (n=8: 0.214/0.181/0.205/0.183/0.188/0.199/0.205/0.179) — conservative (real gRPC < kubectl-exec).
- plain no-op Job container start (no nvidia runtime): 4.04 s (n=6); the privileged+nvidia agent Job
  container start ≈ 7.0 s (backed out of the 8.2 s Job reclaim minus the 1.09 s mechanism).

## Reproduce (from the control-plane host)
The exact kubectl manifests/commands used: deploy holder (HAMi GPU) + resident agent (privileged
hostPID nvidia), find the holder host PID via `/proc/*/cmdline` in the agent (hostPID), then per rep
`lock`+`checkpoint` (evict, verify VRAM→1) and time `restore`+`unlock` via (a) `kubectl exec` into the
resident agent and (b) a fresh Job. Requires authorization for privileged hostPID + GPU pods on the
cluster.
