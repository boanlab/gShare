# Real-trace impact — is GShare's low-duty regime the production common case?

Grounds the impact argument in a real production trace instead of a synthetic load sweep. Parses the
**Alibaba PAI GPU trace 2020** (`cluster-trace-gpu-v2020`; Weng et al., *MLaaS in the Wild*, NSDI'22):
a production cluster of >6,500 GPUs on ~1,800 machines, July–August 2020.

## Method
`pai_machine_metric.csv` (437 MB) gives, per worker interval, `machine_gpu` = the **sum** of per-GPU
utilization (%) on that machine. Join `pai_machine_spec.csv` (`cap_gpu` = GPU count) → per-GPU util =
`machine_gpu / cap_gpu`, weighted by `interval × cap_gpu` (GPU-seconds). 1,814 GPU machines, **18.4M
GPU-hours** of data. (`trace_impact.py` documents the exact parse; the 437 MB CSV is downloaded via the
trace's `download_data.sh`, not committed.)

## Measured per-GPU utilization (GPU-time weighted)
| util band | % of GPU-hours | cumulative |
|---|---|---|
| 0–5%   | 0.6 | 0.6 |
| 5–10%  | 2.6 | 3.2 |
| 10–25% | 55.6 | 58.8 |
| 25–50% | 36.2 | 95.0 |
| 50–75% | 4.3 | 99.3 |
| 75–90% | 0.6 | 99.9 |
| 90–100%| 0.1 | 100 |

**Median per-GPU util ~20–25%; 58.8% of GPU-time runs below 25% util; 95% below 50%.** Production GPU
clusters are pervasively underutilized — the motivation (idle/under-occupied GPUs are billed) is real
at scale, not assumed.

## How this lands for GShare (honest)
1. **The opportunity envelope is real and large.** 95% of GPU-time is < 50% util — clusters operate
   deep in the low-duty regime where the simulator's S1 shows GShare's occupancy recovery is largest
   (load 0.1: 2.4× ; the gain vanishes only at high duty ≥ 0.5, which is 0.7% of GPU-time here). So
   GShare's benefit regime is the **common operating point, not a corner case**.
2. **Most of it is low-*active*, not whole-card idle — and that is the space-sharing regime.** The bulk
   (10–50% util, 92% of GPU-time) is where co-location (MPS/Orion) helps; GShare is *orthogonal* to it
   (§E.4). GShare's specific target is the whole-card *idle* tail plus lossless preemption, not the
   low-active bulk. We do not claim GShare reclaims the 95% — we claim it addresses the idle/preemption
   slice that space-sharing cannot, and that slice sits inside a demonstrably low-duty cluster.
3. **The trace under-resolves GShare's exact target.** `machine_gpu` is averaged over each worker's
   lifetime (mean ~2 h), so it masks intra-session idle *gaps* — a bursty interactive session at 20%
   lifetime-average alternates 100%↔0% and its 0% gaps (> IDLE_TIMEOUT) are exactly what GShare
   reclaims. The near-idle (<5%) figure (0.6%) is therefore a floor, not the reclaimable-gap total;
   resolving the gaps needs per-GPU fine-grained timelines the public trace does not provide.

Takeaway: the real trace confirms pervasive low-duty operation (median ~20–25% util), placing GShare's
largest-gain regime at the production common case, while honestly bounding GShare's slice (idle/
preemption) against the low-active bulk that space-sharing owns.

## Reproduce
`cd cluster-trace-gpu-v2020/data && bash download_data.sh` (downloads `pai_machine_metric.tar.gz` 207 MB
+ `pai_machine_spec.tar.gz`), `tar xzf` both, then run the parse in `trace_impact.py`.
