# E-series end-to-end harness (§E.4: 5-condition comparison)

One workload, seven conditions, one parser. Compares how each GPU-sharing strategy
handles a **resident** session with idle windows competing against a preemptible
**spot** job on the same card. The discriminating scenario is the FV1 3-phase pattern:
`resident burst → idle window → resident burst`, with a spot job trying to use the
freed card during the idle window.

## Workload
`gpu_sgemm.cu` — cuBLAS SGEMM loop. `--mem-gb` sizes the VRAM footprint, `--secs` the
budget, `--idle-every/--idle-sec` inject idle gaps. Emits `READY/PROG/IDLE/DONE` lines
with unix timestamps and achieved GFLOP/s. Built inside `nvidia/cuda:*-devel` (no host
toolchain install) via `Dockerfile`, or compiled natively with `nvcc -lcublas`.

## Conditions and where each runs
| condition | bucket | runner | GPU requirement |
|---|---|---|---|
| Solo(R), Solo(S) | ② bare GPU | `run_baremetal.sh` | any CUDA GPU |
| keep-idle | ② bare GPU | `run_baremetal.sh` | any CUDA GPU |
| cold-STOP | ② bare GPU | `run_baremetal.sh` | any CUDA GPU |
| app-ckpt | ② bare GPU | `run_baremetal.sh` | **cuda-checkpoint (Volta+)** |
| Orion | ② bare GPU | `orion_setup.sh` | CUDA 12.6, Ampere+ (not Pascal) |
| GShare | ① live system | `run_gshare.sh` | the GShare cluster (RTX 4090, gpu2-1) |

For a methodologically clean E1 every condition must run on the **same GPU model**.
The cuda-checkpoint conditions (app-ckpt, GShare) and Orion require the 4090 node, so the
definitive run is on the cluster; a Pascal/local GPU can only validate the non-checkpoint
baselines (Solo/keep-idle/cold-STOP).

## Procedure
1. Build the workload image on the target node: `docker build -t gshare-e2e-workload .`
2. Bare-GPU conditions: `sudo ./run_baremetal.sh <GPU_ID> <MEM_GB> <BURST> <IDLE> e1_out`
3. Orion: `./orion_setup.sh /opt/orion <GPU_ID> <MEM_GB> <SECS> e1_out` then run the two clients.
4. GShare: drive the resident/spot sessions through the GShare API (FV1 path), then
   `R_POD=… S_POD=… ./run_gshare.sh <MEM_GB> <BURST> <IDLE> e1_out`.
5. `python3 parse_e1.py e1_out` → the §E.4 condition×metric matrix + `e1.json`.

## Metrics (parser output → Fig E1)
R-prog (iters vs Solo), R-thpt, R-resume(s), R-slowdown(×), S-thpt, S-loss, goodput,
host-RAM. Fig E2 (occupancy timeline) is built from the `PROG`/`IDLE` timestamps per
condition (1 Hz). GShare's host-RAM column = evicted VRAM (the co-location cost).
