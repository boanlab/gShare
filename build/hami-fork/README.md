# GShare HAMi fork — yielded-card preemptible borrowing

Makes HAMi's scheduler-extender treat a **yielded** GPU (owner VRAM evicted, physically
free per the GShare ledger) as **free capacity for preemptible (borrow) pods** — so borrow
pods schedule through the standard HAMi path with proper 2D accounting, instead of the
device-plugin bypass. Design + rationale: the manuscript, §Implementation ([`docs/paper/`](../../docs/paper/)).

## Layout
- HAMi source is a submodule pinned at **v2.9.0** (`third_party/hami`).
- The modification is a patch (`0001-gshare-yield-borrow.patch`) — the submodule stays a clean
  upstream checkout; the patch is the source of truth (no writable HAMi fork remote needed).

## What the patch does (extender logic)
- `DeviceUsage.Yielded` flag (pkg/device/devices.go).
- `getNodesUsage` reads node annotation `gshare.io/yielded-gpus` (CSV of GPU UUIDs) and marks
  matching devices `Yielded` (pkg/scheduler/scheduler.go).
- `NvidiaGPUDevices.Fit`: for a pod annotated `gshare.io/preemptible=true`, a yielded card is
  evaluated against a copy with owner usage (Used/Usedmem/Usedcores) zeroed — i.e. treated as
  empty — leaving global usage untouched (pkg/device/nvidia/device.go). Non-preemptible pods and
  non-yielded cards are unaffected (same invariant the admission webhook enforces).
- Unit test: `TestFit_GShareYieldedBorrow` (pkg/device/nvidia/gshare_yield_test.go).

## Build & deploy (thin overlay — only the scheduler binary changes; requires Go 1.26+)
Only the `vgpu-scheduler-extender` container's `scheduler` binary differs from stock, so we build
a thin overlay rather than the full (libvgpu/CUDA) HAMi image:
```sh
git submodule update --init third_party/hami
# build the patched scheduler binary (CGO on — pulls go-nvml)
docker run --rm -v "$PWD/third_party/hami":/src -v "$PWD/build/hami-fork":/patch \
  -w /src golang:1.26 sh -c 'apt-get update -qq && apt-get install -y -qq patch && \
    patch -p1 < /patch/0001-gshare-yield-borrow.patch && go build -o /src/bin/scheduler ./cmd/scheduler'
# thin image over the stock HAMi image + push
docker build -f build/hami-fork/Dockerfile.scheduler -t <reg>/hami:v2.9.0-gshare third_party/hami/bin
docker push <reg>/hami:v2.9.0-gshare
# swap ONLY the extender container (kube-scheduler container untouched); imagePullPolicy=Always
kubectl -n kube-system set image deploy/hami-scheduler vgpu-scheduler-extender=<reg>/hami:v2.9.0-gshare
# rollback if needed: kubectl -n kube-system rollout undo deploy/hami-scheduler
```

## End-to-end path
- GShare side: the operator publishes `gshare.io/yielded-gpus`; borrow pods route through
  `hami-scheduler` (`use-gpuuuid` + `gshare.io/preemptible`); reclaim evicts all borrowers.
- The extender reads the **live** node via `nodeLister` (HAMi's cached node object only refreshes
  on `hami.io` device-registration changes, so a bare cache read misses a `gshare.io/yielded-gpus`
  change and treats the card as not-yielded).
- Validated on-cluster: with a card occupied to 20000/24000 MiB and marked yielded, a
  **preemptible** borrower requesting 20000 MiB schedules onto that same card (both hold HAMi
  allocations on the same GPU), while a **non-preemptible** control stays Pending
  (`CardInsufficientMemory`) — yielded cards are preemptible-only capacity.

> ⚠️ A custom cluster scheduler has cluster-wide blast radius (no fail-open, unlike the admission
> webhook); running pods are unaffected and rollback is one `rollout undo`. Use a test cluster.
