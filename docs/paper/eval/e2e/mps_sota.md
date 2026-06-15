# Real NVIDIA MPS measurement — validating the concurrent-share baseline (named SOTA)

Addresses the "concurrent-share is just your HAMi baseline, not a real sharing system" referee
objection by measuring the **actual NVIDIA Multi-Process Service (MPS)** — the canonical space-sharing
SOTA — on the live RTX-4090 (gpu2-1), and confirming the same ~½ split.

## Setup
- One privileged pod, single card (`NVIDIA_VISIBLE_DEVICES=<uuid>`, `DRIVER_CAPABILITIES=all`),
  `gshare-e2e-workload` image (gpu_sgemm + CUDA runtime). MPS binaries present in-image
  (`/usr/bin/nvidia-cuda-mps-control`, `nvidia-cuda-mps-server`); compute mode Default.
- MPS daemon started in-pod (`nvidia-cuda-mps-control -d`); two gpu_sgemm clients run concurrently
  and connect to the MPS server (confirmed active via `get_server_list`).

## Measured (gpu_sgemm, 4 GB, fp32 SM-saturating GEMM)
| condition | throughput | note |
|---|---|---|
| solo (full card) | **54.69 TFLOPS** | reference |
| **MPS 2-way** A | **27.27 TFLOPS** | ~½ |
| **MPS 2-way** B | **27.49 TFLOPS** | ~½ (sum 54.76 ≈ solo) |

**Result:** two compute-bound co-located GEMMs each get **~½** of the card under *real NVIDIA MPS*
(27.4 TFLOPS each), matching the HAMi-native `concurrent-share` measurement (26.5 each, §E.4) to within
the HAMi interception overhead. This confirms the §E.4 finding — *space partitioning cannot give any
job full throughput under SM-saturated concurrency* — is a property of the space-sharing family itself
(NVIDIA MPS, the system MPS/MIG/Orion/Salus all build on), not an artifact of the HAMi proxy. GShare's
orthogonality argument (it reclaims an idle card *whole* rather than co-locating) therefore stands
against the real named system, not only a stand-in.

## Scope (honest) — preemption-family SOTA
This measures the **space-sharing** SOTA (MPS), the family GShare is orthogonal to. Direct runs of the
**preemption/checkpoint** family remain out of reach: Orion (EuroSys'24) loads and runs to the
scheduler but deadlocks on the torch/CUDA-12/4090 ABI (documented negative result, `eval/e2e/orion/`);
AntMan (OSDI'20) is TensorFlow-1.x-era and not buildable on the current CUDA-13/RTX-4090 stack. These
are differentiated qualitatively in related-work §1–§2 (framework-intrusive / device-proxy), and the
preemption *resume cost* is compared via app-checkpoint (§E.4: GShare 1.74 s vs app-ckpt ~9 s).

## Reproduce
In a privileged pod with one GPU by UUID: `nvidia-cuda-mps-control -d`; run two
`gpu_sgemm --mem-gb 4 --secs 25` concurrently; compare each `tflops` to a solo run. Requires
authorization for privileged + GPU pods.
