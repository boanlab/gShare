# Running Orion (EuroSys'24) as the concurrent-share baseline — status & fix

Goal: run Orion's interference-aware co-location on gpu2-1 (RTX 4090) and compare
`--algo sequential` vs `--algo orion` on identical 2×resnet50, as a *measured* spatial-
sharing baseline (vs the HAMi-native `concurrent-share` already in §E.4).

## What works
The build is straightforward (clone + `compile.sh` + torch). The blocker was that Orion's
CUDA interception (`src/cuda_capture/intercept_{cublas,cudnn}.cpp`) resolves the real CUDA
functions with `dlsym(RTLD_NEXT, "cublasSgemm_v2")`, which returns **NULL** for the
**version-noded** symbols (`cublasSgemm_v2@@libcublas.so.12`, `cudnnBackendExecute@@…`)
that modern PyTorch CUDA wheels ship. This affects every CUDA/torch combo tried
(cu118/cu121/cu124/cu130) — it is an interposition-vs-symbol-versioning incompatibility,
not a per-version issue.

**Fix (`orion_resolve_patch.sh`)**: replace the 13 `dlsym(RTLD_NEXT, "X")` sites with
`orion_resolve("X")`, which `dlsym`s from explicit `dlopen` handles of the real libraries
(`libcublas.so.12`, `libcudnn.so.9`, …). Explicit-handle `dlsym` resolves the default-
versioned symbol correctly. Plus a one-line client patch: `torch.cuda.get_stream_from_external`
→ `torch.cuda.ExternalStream` (the former is only in very recent torch).

With the patch, Orion **loads, resolves all CUDA symbols, parses the kernel profile
(175 kernels), sets up the external stream, and reaches model execution** — the symbol
blocker is fully resolved.

## Remaining blocker (open) — profile mismatch, root-caused
Execution then hangs at `model.to(0)` (train_imagenet.py): the client enqueues the model-
weight cudaMalloc/cudaMemcpy ops and busy-waits in `block()` for the scheduler to drain
them, but the scheduler stops draining. Root cause (traced through `scheduler_eval.cpp`
`busy_wait_profile`): Orion is **profile-driven** — the scheduler gates each client on
`seen[i] == num_client_kernels[i]` and, per op, looks up `op_info_vector[i][seen[i]]` from
the shipped kernel profile (`resnet50_4_fwd`, 175 entries). With torch 2.5.1 on a 4090,
`model.to(0)` + resnet50 inference emit a different op count/structure than that profile
encodes (the profile was captured on the authors' torch/GPU), so the profile-gated
coordination never aligns and the scheduler ceases draining mid-setup. This is why BOTH
`sequential` and `orion` hang identically — it is not the interference policy.

Consequence: completing an Orion run here requires **regenerating the kernel profile for
this exact GPU + torch** via Orion's `PROFILE.md` pipeline (nsys/ncu instrumentation per
model), not just a "simpler" model — any workload needs its own matching profile.
Re-profiling is a self-contained sub-project (instrument each model, post-process op_info,
verify SM counts on Ada). Given the spatial-sharing design point is already measured live
via HAMi `concurrent-share` (§E.4: both co-tenants → ~½ throughput regardless of split),
re-profiling Orion was judged out of scope; the symbol patch above is preserved so a future
profiling pass can complete the comparison.

## Reproduce
1. Build image: base `nvidia/cuda:12.6.0-cudnn-devel`, clone orion, run
   `orion_resolve_patch.sh`, `compile.sh`, `pip install torch==2.5.1 … cu124`.
2. Pod on gpu2-1 with a full HAMi card (`nvidia.com/gpu:1, gpucores:100, gpumem:10240`).
3. Inside: patch `launch_jobs.py` (drop BERT/Transformer imports) and `train_imagenet.py`
   (`get_stream_from_external`→`ExternalStream`); set
   `LD_PRELOAD=libinttemp.so:libcudnn.so.9:libcublasLt.so.12:libcublas.so.12`;
   `python3 launch_jobs.py --algo {sequential|orion} --config_file config_2client.json`.

Until the scheduler hang is resolved, the representative spatial-sharing baseline for this
HAMi cluster is the live-measured `concurrent-share` (§E.4): two compute-bound co-tenants
converge to ~½ throughput each regardless of core split (50/70/90), confirming spatial
sharing cannot grant full per-job throughput — only GShare's temporal exclusivity does.
